// @vitest-environment jsdom

// Task steps batch 2 (to-do "任务『完成』指每一步都做完", 2026-10-25), on the
// records page:
// - a task the user stopped reads 已停下, says which step it stopped at, and
//   offers 接着做, which carries the same task on (no new record);
// - a finished task whose step failed reads 完成，但<步骤>失败 and offers
//   重做这一步;
// - a task still working on a step after its transcript can be stopped from
//   the 更多 menu;
// - a task with nothing to carry on offers nothing.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

let tasks = [];
const resumeJob = vi.fn(async () => ({ok: true}));
const stopJobStep = vi.fn(async () => ({ok: true}));
const retryJob = vi.fn(async () => ({}));

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        tasks,
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
        addToHistory: () => {},
        removeFromHistory: () => {},
        restoreTask: () => {},
        ingestJobs: () => {},
        markCancelled: () => {},
        revertCancelled: () => {},
        abortPendingUpload: () => {},
        runtimeConfig: {},
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useApi: () => ({
            getJob: async () => ({}),
            getJobs: async () => [],
            cancelJob: async () => ({}),
            deleteJob: async () => ({}),
            retryJob,
            resumeJob,
            stopJobStep,
            createVideoSourceJob: async () => ({}),
            downloadJobMedia: async () => ({}),
            openOutputFolder: async () => ({}),
        }),
    };
});

const {default: AgentTasks} = await import('./agent-tasks.jsx');

const step = (id, label) => ({step: id, label});
const task = (id, activity, result = {}) => ({
    task_id: id,
    status: 'completed',
    source_type: 'video_file',
    source_filename: `${id}.mp4`,
    created_at: '2026-10-25T10:00:00Z',
    updated_at: '2026-10-25T10:00:00Z',
    metadata: {display_title: `录像 ${id}`},
    result: {task_id: id, transcript_text: '有转录', summary_status: 'completed', summary_chars: 1200, ...result},
    activity: {busy: false, open_steps: [], failed_steps: [], stopped: false, stopped_steps: [], resume_from: null, ...activity},
});

const mount = () => render(
    <MemoryRouter initialEntries={['/agent']}>
        <Routes><Route path="/agent" element={<AgentTasks/>}/></Routes>
    </MemoryRouter>,
);

afterEach(() => {
    cleanup();
    tasks = [];
    vi.clearAllMocks();
});

describe('carrying a task on from the records page', () => {
    it('a stopped task says where it stopped and carries the same task on', async () => {
        tasks = [task('stopped-1', {stopped: true, stopped_steps: [step('note', '写笔记')], resume_from: step('note', '写笔记')},
            {summary_status: 'failed', summary_chars: 0})];
        mount();
        expect(screen.getByTestId('task-outcome').textContent).toBe('已停下');
        expect(screen.getByTestId('resume-offer').textContent).toContain('停在了「写笔记」这一步');
        fireEvent.click(screen.getByRole('button', {name: '接着做'}));
        await waitFor(() => expect(resumeJob).toHaveBeenCalledWith('stopped-1', expect.anything()));
        expect(retryJob).not.toHaveBeenCalled();
    });

    it('a finished task whose step failed offers to redo that step', async () => {
        tasks = [task('failed-step', {failed_steps: [step('feishu', '发飞书')], resume_from: step('feishu', '发飞书')})];
        mount();
        expect(screen.getByTestId('task-outcome').textContent).toBe('完成，但发飞书失败');
        fireEvent.click(screen.getByRole('button', {name: '重做这一步'}));
        await waitFor(() => expect(resumeJob).toHaveBeenCalledWith('failed-step', expect.anything()));
    });

    it('a task that is done offers nothing to carry on', () => {
        tasks = [task('done-1', {})];
        mount();
        expect(screen.queryByTestId('resume-offer')).toBeNull();
        expect(screen.queryByTestId('task-outcome')).toBeNull();
    });

    it('a step still working after the transcript can be stopped', async () => {
        tasks = [task('busy-1', {busy: true, label: '正在写笔记', current_step: 'note', open_steps: ['note']},
            {summary_status: 'pending', summary_chars: 0})];
        mount();
        fireEvent.click(screen.getByRole('button', {name: '更多'}));
        fireEvent.click(await screen.findByText('停下这一步'));
        await waitFor(() => expect(stopJobStep).toHaveBeenCalledWith('busy-1', expect.anything()));
        expect(screen.queryByTestId('resume-offer')).toBeNull();
    });
});
