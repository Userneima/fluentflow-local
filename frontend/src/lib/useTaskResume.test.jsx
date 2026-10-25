// @vitest-environment jsdom

// The editor's header (task steps batch 2, 2026-10-25): a task the user
// stopped offers 接着做 and a finished task whose step failed offers
// 重做这一步, read from the task's activity; clicking carries the same task
// on and reads the task again. A task with nothing to carry on offers nothing.

import {describe, expect, it, vi} from 'vitest';
import {act, renderHook, waitFor} from '@testing-library/react';
import {useTaskResume} from './useTaskResume.js';

const activity = (extra) => ({busy: false, open_steps: [], failed_steps: [], stopped: false, resume_from: null, ...extra});

describe('the editor offers to carry its task on', () => {
    it('offers 接着做 for a stopped task and carries it on', async () => {
        let job = {task_id: 't1', status: 'completed', activity: activity({stopped: true, resume_from: {step: 'note', label: '写笔记'}})};
        const getJobActivity = vi.fn(async () => job);
        const resumeJob = vi.fn(async () => {
            job = {...job, activity: activity({busy: true, label: '正在写笔记'})};
            return {ok: true};
        });
        const onResumed = vi.fn();
        const {result} = renderHook(() => useTaskResume({taskId: 't1', getJobActivity, resumeJob, lang: 'zh', onResumed}));
        await waitFor(() => expect(result.current.offer?.label).toBe('接着做'));
        await act(async () => { await result.current.resume(); });
        expect(resumeJob).toHaveBeenCalledWith('t1', undefined);
        expect(onResumed).toHaveBeenCalled();
        expect(result.current.offer).toBeNull();
    });

    it('offers 重做这一步 for a failed step and nothing for a finished task', async () => {
        const failed = {task_id: 't2', status: 'completed', activity: activity({failed_steps: [{step: 'note'}], resume_from: {step: 'note', label: '写笔记'}})};
        const {result: redo} = renderHook(() => useTaskResume({taskId: 't2', getJobActivity: async () => failed, resumeJob: vi.fn(), lang: 'zh'}));
        await waitFor(() => expect(redo.current.offer?.label).toBe('重做这一步'));

        const done = {task_id: 't3', status: 'completed', activity: activity({})};
        const read = vi.fn(async () => done);
        const {result: none} = renderHook(() => useTaskResume({taskId: 't3', getJobActivity: read, resumeJob: vi.fn(), lang: 'zh'}));
        await waitFor(() => expect(read).toHaveBeenCalled());
        expect(none.current.offer).toBeNull();
    });
});
