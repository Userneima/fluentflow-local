import { describe, it, expect } from 'vitest';
import { reconcileTaskList, entryToJob, jobToHistoryEntry } from './jobMappers.js';
import { normalizeTaskState } from './taskState.js';

// Each test locks one historically-recurring list-reconciliation regression so
// it can never come back. See docs/task_list_reconciliation_plan.md for the
// mapping from test to commit.

const makeJob = (task_id, extra = {}) => ({
    task_id,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    task_state: 'completed',
    ...extra,
});

const ids = (list) => list.map((job) => job.task_id);

describe('reconcileTaskList', () => {
    it('returns an empty list with no sources', () => {
        expect(reconcileTaskList()).toEqual([]);
        expect(reconcileTaskList({})).toEqual([]);
    });

    // fbc37ca: a deleted record reappeared because it still lived in a cache the
    // delete handler forgot to purge. A tombstone must win over every source.
    it('drops a tombstoned id even when cached and fetched still contain it', () => {
        const list = reconcileTaskList({
            fetched: [makeJob('t1'), makeJob('t2')],
            cached: [makeJob('t1'), makeJob('t2')],
            tombstones: ['t2'],
        });
        expect(ids(list)).toEqual(['t1']);
    });

    // e53885b: another account's cached jobs leaked into the current list.
    it('keeps only jobs belonging to the active account', () => {
        const list = reconcileTaskList({
            cached: [
                makeJob('a', { client_id: 'user:A' }),
                makeJob('b', { client_id: 'user:B' }),
            ],
            accountId: 'A',
        });
        expect(ids(list)).toEqual(['a']);
    });

    it('treats the local account as owning every job', () => {
        const list = reconcileTaskList({
            cached: [makeJob('a', { client_id: 'user:A' }), makeJob('x')],
            accountId: 'local',
        });
        expect(ids(list).sort()).toEqual(['a', 'x']);
    });

    // be500b5: records were missing on cold start until the backend fetch
    // returned. The cache alone must still yield a list.
    it('yields a list from cache alone when nothing is fetched', () => {
        const list = reconcileTaskList({ cached: [makeJob('t1')], fetched: [] });
        expect(ids(list)).toEqual(['t1']);
    });

    // 84584b9: an in-flight optimistic upload disappeared behind a stale backend
    // row. The fresher optimistic record must survive.
    it('keeps an optimistic uploading record over a stale backend row', () => {
        const list = reconcileTaskList({
            optimistic: [makeJob('t1', { queueUpload: true, updated_at: '2026-07-02T00:00:00Z' })],
            fetched: [makeJob('t1', { task_state: 'completed', updated_at: '2026-07-01T00:00:00Z' })],
        });
        expect(list).toHaveLength(1);
        expect(normalizeTaskState(list[0])).toBe('uploading');
    });

    it('lets a fresher backend row win over an older optimistic record', () => {
        const list = reconcileTaskList({
            optimistic: [makeJob('t1', { queueUpload: true, updated_at: '2026-07-01T00:00:00Z' })],
            fetched: [makeJob('t1', { task_state: 'completed', updated_at: '2026-07-03T00:00:00Z' })],
        });
        expect(list).toHaveLength(1);
        expect(normalizeTaskState(list[0])).toBe('completed');
    });

    // mergeCachedJobs freshness contract: same task_id collapses to the newest.
    it('collapses duplicate task_ids to the freshest updated_at', () => {
        const list = reconcileTaskList({
            cached: [makeJob('t1', { updated_at: '2026-07-01T00:00:00Z', task_state: 'failed' })],
            fetched: [makeJob('t1', { updated_at: '2026-07-03T00:00:00Z', task_state: 'completed' })],
        });
        expect(list).toHaveLength(1);
        expect(list[0].updated_at).toBe('2026-07-03T00:00:00Z');
    });

    // b49dd85 / 85cc00b: agent records and recent activity diverged because each
    // surface assembled the list its own way. One pure function must be
    // deterministic and collapse a job present in several sources to one entry.
    it('is deterministic and collapses a job present in every source to one entry', () => {
        const inputs = {
            fetched: [makeJob('t1'), makeJob('t2')],
            cached: [makeJob('t1')],
            optimistic: [makeJob('t1', { queueUpload: true, updated_at: '2026-07-05T00:00:00Z' })],
        };
        const first = reconcileTaskList(inputs);
        const second = reconcileTaskList(inputs);
        expect(first).toEqual(second);
        expect(first.filter((job) => job.task_id === 't1')).toHaveLength(1);
    });

    // c1975cd: resubmitting a record produced a duplicate. A resubmit is an
    // upsert of the same task_id, not a new row.
    it('upserts a resubmitted task instead of duplicating it', () => {
        const list = reconcileTaskList({
            cached: [makeJob('t1', { task_state: 'failed', updated_at: '2026-07-01T00:00:00Z' })],
            optimistic: [makeJob('t1', { queueUpload: true, updated_at: '2026-07-05T00:00:00Z' })],
        });
        expect(list).toHaveLength(1);
        expect(normalizeTaskState(list[0])).toBe('uploading');
    });

    // Pins a user-cancelled task to cancelled even while a slow backend poll
    // still reports it running (replaces the per-page locallyCancelled set).
    it('pins a cancelled id to cancelled over a stale running backend row', () => {
        const list = reconcileTaskList({
            fetched: [makeJob('t1', { task_state: 'running', status: 'running' })],
            cancelled: ['t1'],
        });
        expect(list).toHaveLength(1);
        expect(normalizeTaskState(list[0])).toBe('cancelled');
    });

    it('leaves jobs untouched when the cancelled set is empty', () => {
        const list = reconcileTaskList({
            fetched: [makeJob('t1', { task_state: 'running', status: 'running' })],
            cancelled: [],
        });
        expect(normalizeTaskState(list[0])).toBe('running');
    });

    it('lets a tombstone win over a cancelled pin', () => {
        const list = reconcileTaskList({
            fetched: [makeJob('t1', { task_state: 'running' })],
            cancelled: ['t1'],
            tombstones: ['t1'],
        });
        expect(list).toEqual([]);
    });

    it('does not mutate its input arrays', () => {
        const cached = [makeJob('t1'), makeJob('t2')];
        const fetched = [makeJob('t2')];
        reconcileTaskList({ cached, fetched, tombstones: ['t1'] });
        expect(ids(cached)).toEqual(['t1', 't2']);
        expect(ids(fetched)).toEqual(['t2']);
    });
});

// Stage 2 lets a history entry re-enter the canonical raw-job list via
// entryToJob. The extra job->entry->job hop must not lose the fields the
// history view renders (taskId, title, status), or addToHistory would corrupt
// records. See docs/task_list_reconciliation_plan.md.
describe('entryToJob round trip', () => {
    it('preserves taskId, title, and status for a completed job entry', () => {
        const job = {
            task_id: 'j1',
            client_id: 'user:A',
            created_at: '2026-07-01T00:00:00Z',
            updated_at: '2026-07-02T00:00:00Z',
            task_state: 'completed',
            source_filename: 'lecture.mp4',
            result: {
                task_id: 'j1',
                transcript_text: 'hello world',
                summary_markdown: '# notes',
                display_title: 'lecture',
                filename: 'lecture.mp4',
            },
        };
        const e1 = jobToHistoryEntry(job);
        const e2 = jobToHistoryEntry(entryToJob(e1, { clientId: 'user:A' }));
        expect(e2.taskId).toBe(e1.taskId);
        expect(e2.name).toBe(e1.name);
        expect(e2.status).toBe(e1.status);
    });

    it('preserves a bare failed-record literal from the upload path', () => {
        const entry = { id: 123, taskId: 'f1', name: 'broken.mp4', timestamp: 1751328000000, durationMin: 0, status: 'failed' };
        const rt = jobToHistoryEntry(entryToJob(entry));
        expect(rt.taskId).toBe('f1');
        expect(rt.status).toBe('failed');
        expect(rt.name).toBeTruthy();
    });

    it('stamps client_id so account owner filtering keeps the record', () => {
        const job = entryToJob(
            { taskId: 'o1', name: 'x', status: 'completed', timestamp: 1751328000000 },
            { clientId: 'user:A' },
        );
        expect(ids(reconcileTaskList({ optimistic: [job], accountId: 'A' }))).toEqual(['o1']);
        expect(reconcileTaskList({ optimistic: [job], accountId: 'B' })).toEqual([]);
    });

    it('returns null for a missing entry or one without a task id', () => {
        expect(entryToJob(null)).toBeNull();
        expect(entryToJob({ name: 'no id' })).toBeNull();
    });
});
