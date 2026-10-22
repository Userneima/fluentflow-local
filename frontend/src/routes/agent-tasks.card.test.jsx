// @vitest-environment jsdom

// A finished record on the records page, mounted. Written from the walkthrough
// of 2026-10-22:
// - the badge says what actually came out: a task whose note was not written
//   does not wear a green 已完成 (item 9);
// - "删除记录" is not a button beside "查看结果" at the same weight; it sits in a
//   "更多" menu and still asks before deleting (item 14);
// - "查看结果" opens the editor with the task in the address, and a task whose
//   cut has places left to judge says so there, so the editor can take the
//   owner to them (item 10; the editor side is the editor's own work).

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';

let tasks = [];
const deleteJob = vi.fn(async () => ({}));

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
            deleteJob,
            retryJob: async () => ({}),
            createVideoSourceJob: async () => ({}),
            downloadJobMedia: async () => ({}),
            openOutputFolder: async () => ({}),
        }),
    };
});

const {default: AgentTasks} = await import('./agent-tasks.jsx');

const finished = (id, result) => ({
    task_id: id,
    status: 'completed',
    source_type: 'video_file',
    source_filename: `${id}.mp4`,
    created_at: '2026-10-20T10:00:00Z',
    updated_at: '2026-10-20T10:00:00Z',
    metadata: {display_title: `录像 ${id}`},
    result: {task_id: id, summary_status: 'completed', summary_chars: 1200, transcript_text: '有转录', ...result},
});

const EditorAddress = () => {
    const location = useLocation();
    return <p data-testid="editor-address">{`${location.pathname}${location.search}`}</p>;
};

const mount = () => render(
    <MemoryRouter initialEntries={['/agent']}>
        <Routes>
            <Route path="/agent" element={<AgentTasks/>}/>
            <Route path="/editor" element={<EditorAddress/>}/>
        </Routes>
    </MemoryRouter>,
);

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    deleteJob.mockClear();
});

describe('a finished record', () => {
    it('says the note was not written instead of 已完成', () => {
        tasks = [finished('a', {summary_status: 'failed', summary_chars: 0, summary_error: 'timeout'})];
        mount();
        expect(screen.getByTestId('task-outcome').textContent).toBe('笔记没生成');
    });

    it('keeps 删除记录 in the 更多 menu, and still asks before deleting', async () => {
        tasks = [finished('a', {})];
        mount();
        expect(screen.queryByRole('button', {name: '删除记录'})).toBeNull();
        fireEvent.click(screen.getByRole('button', {name: '更多'}));
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
        fireEvent.click(screen.getByRole('menuitem', {name: '删除记录'}));
        expect(confirm).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(deleteJob).toHaveBeenCalledWith('a', {}));
    });

    it('opens the editor with the task in the address', async () => {
        tasks = [finished('a', {debreath: {status: 'completed', survey: {has_report: true, to_judge: 0}}})];
        mount();
        fireEvent.click(screen.getByRole('button', {name: '查看结果'}));
        expect((await screen.findByTestId('editor-address')).textContent).toBe('/editor?task=a');
    });

    it('tells the editor there are places left to judge', async () => {
        tasks = [finished('b', {debreath: {status: 'completed', survey: {has_report: true, to_judge: 4}}})];
        mount();
        fireEvent.click(screen.getByRole('button', {name: '查看结果'}));
        expect((await screen.findByTestId('editor-address')).textContent).toBe('/editor?task=b&review=cut');
    });
});
