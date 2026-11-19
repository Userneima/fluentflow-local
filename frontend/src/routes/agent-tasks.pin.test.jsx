// @vitest-environment jsdom

// Pinning records (2026-11-19, docs/方案/2026-11-19-处理记录置顶和收藏.md).
// Requirements:
// - a pinned record shows a pin and sits above the other finished records,
//   which start under a small "其余记录" label;
// - "置顶" / "取消置顶" is in the record's 更多 menu, sends the new state and
//   puts the answered row straight into the list.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

let tasks = [];
const ingestJobs = vi.fn();
const pinJob = vi.fn(async (taskId, pinned) => ({
    task_id: taskId, status: 'completed', updated_at: '2026-10-20T10:00:00Z',
    metadata: {pinned_at: pinned ? '2026-11-19T06:00:00Z' : null},
}));

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        tasks,
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
        addToHistory: () => {},
        removeFromHistory: () => {},
        restoreTask: () => {},
        ingestJobs,
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
            pinJob,
            retryJob: async () => ({}),
            createVideoSourceJob: async () => ({}),
            downloadJobMedia: async () => ({}),
            openOutputFolder: async () => ({}),
        }),
    };
});

const {default: AgentTasks} = await import('./agent-tasks.jsx');

const finished = (id, updatedAt, pinnedAt = null) => ({
    task_id: id,
    status: 'completed',
    source_type: 'video_file',
    source_filename: `${id}.mp4`,
    created_at: updatedAt,
    updated_at: updatedAt,
    metadata: {display_title: `录像 ${id}`, pinned_at: pinnedAt},
    result: {task_id: id, summary_status: 'completed', summary_chars: 1200, transcript_text: '有转录'},
});

const mount = () => render(<MemoryRouter initialEntries={['/agent']}><AgentTasks/></MemoryRouter>);

const cardTitles = () => screen.getAllByRole('article').map((card) => within(card).getByRole('heading').textContent);

afterEach(() => {
    cleanup();
    ingestJobs.mockClear();
    pinJob.mockClear();
});

describe('pinned records', () => {
    it('sit above newer records, marked, with the rest under a label', () => {
        tasks = [
            finished('clip', '2026-11-19T10:00:00Z'),
            finished('course', '2026-11-03T10:00:00Z', '2026-11-19T06:00:00Z'),
        ];
        mount();
        expect(cardTitles()).toEqual(['录像 course', '录像 clip']);
        const [courseCard, clipCard] = screen.getAllByRole('article');
        expect(within(courseCard).getByTestId('task-pinned')).toBeTruthy();
        expect(within(clipCard).queryByTestId('task-pinned')).toBeNull();
        expect(screen.getByTestId('records-after-pinned').textContent).toBe('其余记录');
    });

    it('shows no label when nothing is pinned', () => {
        tasks = [finished('clip', '2026-11-19T10:00:00Z'), finished('other', '2026-11-18T10:00:00Z')];
        mount();
        expect(screen.queryByTestId('records-after-pinned')).toBeNull();
    });

    it('pins from the 更多 menu and puts the answer into the list', async () => {
        tasks = [finished('course', '2026-11-03T10:00:00Z')];
        mount();
        fireEvent.click(screen.getByRole('button', {name: '更多'}));
        fireEvent.click(screen.getByRole('menuitem', {name: '置顶'}));
        await waitFor(() => expect(pinJob).toHaveBeenCalledWith('course', true));
        await waitFor(() => expect(ingestJobs).toHaveBeenCalledTimes(1));
        expect(ingestJobs.mock.calls[0][0][0].metadata.pinned_at).toBe('2026-11-19T06:00:00Z');
    });

    it('offers 取消置顶 on a pinned record and sends false', async () => {
        tasks = [finished('course', '2026-11-03T10:00:00Z', '2026-11-19T06:00:00Z')];
        mount();
        fireEvent.click(screen.getByRole('button', {name: '更多'}));
        fireEvent.click(screen.getByRole('menuitem', {name: '取消置顶'}));
        await waitFor(() => expect(pinJob).toHaveBeenCalledWith('course', false));
    });
});
