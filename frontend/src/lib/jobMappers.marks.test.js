import { describe, it, expect, vi, afterEach } from 'vitest';
import { reconcileTaskList, taskStateRow, writeCachedAccountJobs, accountJobsCacheKey, isPinnedJob, studiedOf } from './jobMappers.js';

// Requirements (review of 5554f67, 2026-11-26):
// - A pin or 学完了 the user just clicked stays as clicked when a reply read
//   before the click arrives after it. Marking leaves updated_at alone, so
//   that time cannot tell them apart; metadata.marks_changed_at does.
// - The editor's own read of a task brings its state and marks onto the list's
//   row, not its megabytes of transcript.
// - The browser's copy of the list carries no *segments and no cut survey.

const UPDATED = '2026-11-20T00:00:00Z';
const row = (metadata = {}, extra = {}) => ({
    task_id: 't1', status: 'completed', created_at: UPDATED, updated_at: UPDATED,
    metadata: {display_title: '第四课', ...metadata},
    result: {task_id: 't1', summary_chars: 900},
    ...extra,
});
const onto = (current, fetched) => reconcileTaskList({cached: current, fetched});

describe('a mark the user just made', () => {
    it('is not undone by an older reply arriving after the pin', () => {
        const beforeClick = row();
        const pinned = row({pinned_at: '2026-11-26T08:00:00+00:00', marks_changed_at: '2026-11-26T08:00:00.000100+00:00'});
        const list = onto(onto([beforeClick], [pinned]), [taskStateRow(beforeClick)]);
        expect(isPinnedJob(list[0])).toBe(true);
    });

    it('is not undone after unpinning either, nor by the whole list read before it', () => {
        const pinnedLongAgo = row({pinned_at: '2026-11-01T00:00:00+00:00', marks_changed_at: '2026-11-01T00:00:00.000000+00:00'});
        const unpinned = row({pinned_at: null, marks_changed_at: '2026-11-26T08:00:00.000000+00:00'});
        const list = reconcileTaskList({cached: [unpinned], fetched: [pinnedLongAgo], fetchedIsComplete: true});
        expect(isPinnedJob(list[0])).toBe(false);
    });

    it('keeps 学完了 over an older reply, and takes a newer mark from elsewhere', () => {
        const studied = row({studied: {at: '2026-11-26T08:00:00+00:00', verdict: 'good'}, marks_changed_at: '2026-11-26T08:00:00.000000+00:00'});
        expect(studiedOf(onto([studied], [taskStateRow(row())])[0])?.verdict).toBe('good');
        const takenBackByAgent = row({marks_changed_at: '2026-11-26T09:00:00.000000+00:00'});
        expect(studiedOf(onto([studied], [takenBackByAgent])[0])).toBe(null);
    });

    it('still lets a fresh read correct a stale browser copy that has no change time', () => {
        const cachedStale = row();
        const read = row({pinned_at: '2026-11-25T00:00:00+00:00'});
        expect(isPinnedJob(onto([cachedStale], [taskStateRow(read)])[0])).toBe(true);
    });
});

describe('the editor reading one task whole', () => {
    const whole = row({pinned_at: '2026-11-25T00:00:00+00:00'}, {
        status: 'completed',
        updated_at: '2026-11-21T00:00:00Z',
        result: {task_id: 't1', raw_segments: [{text: 'a'}], display_segments: [{text: 'a'}], stt_raw_segments: [{text: 'a'}],
                 source_display_segments: [{text: 'a'}], debreath: {status: 'done', survey: {report: {}, ranges: [1, 2, 3], to_judge: 2}}},
    });

    it('brings its state and marks onto the list row, which keeps its own result', () => {
        const list = onto([row({}, {status: 'running'})], [taskStateRow(whole)]);
        expect(list[0].status).toBe('completed');
        expect(list[0].updated_at).toBe('2026-11-21T00:00:00Z');
        expect(isPinnedJob(list[0])).toBe(true);
        expect(list[0].result).toEqual({task_id: 't1', summary_chars: 900});
        expect('__marksOnly' in list[0]).toBe(false);
    });

    it('adds a list-sized row when the list has none yet', () => {
        const [added] = onto([], [taskStateRow(whole)]);
        expect(isPinnedJob(added)).toBe(true);
        expect(added.result.display_segments).toBe(null);
        expect(added.result.debreath.survey).toEqual({has_report: true, to_judge: 2, rerendering: undefined});
        expect('__marksOnly' in added).toBe(false);
    });
});

describe('the browser copy of the list', () => {
    afterEach(() => vi.unstubAllGlobals());

    it('keeps no segments of any kind and no cut survey ranges', () => {
        const stored = {};
        vi.stubGlobal('localStorage', {setItem: (key, value) => { stored[key] = value; }, getItem: (key) => stored[key] ?? null});
        const segments = Array.from({length: 2000}, (_, i) => ({start: i, end: i + 1, text: '这一句是转录稿里的一句话'}));
        writeCachedAccountJobs('local', [row({}, {result: {
            task_id: 't1', raw_segments: segments, display_segments: segments, stt_raw_segments: segments,
            source_stt_raw_segments: segments, source_display_segments: segments, source_raw_segments: segments,
            debreath: {status: 'done', survey: {report: {items: segments}, to_judge: 1}},
            visual_note: {status: 'done', markdown: 'x'.repeat(50000)},
        }})]);
        const saved = stored[accountJobsCacheKey('local')];
        expect(saved.length).toBeLessThan(5000);
        const [job] = JSON.parse(saved).jobs;
        expect(Object.entries(job.result).filter(([key, value]) => key.endsWith('segments') && value?.length)).toEqual([]);
        expect(job.result.debreath.survey.to_judge).toBe(1);
        expect(job.result.visual_note.status).toBe('done');
    });
});
