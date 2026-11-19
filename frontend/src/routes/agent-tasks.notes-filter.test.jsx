// @vitest-environment jsdom

// The 有笔记 filter (2026-11-19, decision 6.77). Requirements:
// - 有笔记 shows only records with a note in FluentFlow, and counts them;
//   transcript-only records (an agent's batch of clips) and failed ones are left out;
// - the 全部 / 有笔记 choice is kept: the page opens the way it was left;
//   the other filters are not kept;
// - sent here to look at one task, the page shows every record.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
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

const withNote = (id, updatedAt) => ({
    task_id: id, status: 'completed', source_type: 'video_file', source_filename: `${id}.mp4`,
    created_at: updatedAt, updated_at: updatedAt,
    metadata: {display_title: `录像 ${id}`},
    result: {task_id: id, summary_status: 'completed', summary_chars: 1200, transcript_text: '有转录'},
});
const transcriptOnly = (id, updatedAt) => ({
    task_id: id, status: 'completed', source_type: 'video_link', source_filename: `${id}.mp4`,
    created_at: updatedAt, updated_at: updatedAt,
    metadata: {display_title: `短片 ${id}`, cut: false},
    result: {task_id: id, summary_status: 'skipped', summary_skipped: true, summary_chars: 0, transcript_text: '有转录'},
});
const failedClip = (id, updatedAt) => ({
    task_id: id, status: 'failed', source_type: 'video_link', created_at: updatedAt, updated_at: updatedAt,
    metadata: {display_title: `失败 ${id}`}, error_reason: 'no speech', result: {},
});

const mount = (state) => render(
    <MemoryRouter initialEntries={[state ? {pathname: '/agent', state} : '/agent']}><AgentTasks/></MemoryRouter>,
);
const titles = () => screen.queryAllByRole('article').map((card) => within(card).getByRole('heading').textContent);

afterEach(() => {
    cleanup();
    window.localStorage.clear();
});

describe('the 有笔记 filter', () => {
    const list = () => [
        transcriptOnly('clip1', '2026-11-19T10:00:00Z'),
        failedClip('clip2', '2026-11-19T09:00:00Z'),
        withNote('course', '2026-11-03T10:00:00Z'),
        transcriptOnly('clip3', '2026-11-02T10:00:00Z'),
    ];

    it('shows only records with a note, and counts them', () => {
        tasks = list();
        mount();
        const notes = screen.getByRole('button', {name: /有笔记/});
        expect(notes.textContent).toContain('1');
        fireEvent.click(notes);
        expect(titles()).toEqual(['录像 course']);
    });

    it('opens the way it was left', () => {
        tasks = list();
        mount();
        fireEvent.click(screen.getByRole('button', {name: /有笔记/}));
        cleanup();
        mount();
        expect(screen.getByRole('button', {name: /有笔记/}).getAttribute('aria-pressed')).toBe('true');
        expect(titles()).toEqual(['录像 course']);
        fireEvent.click(screen.getByRole('button', {name: /全部/}));
        cleanup();
        mount();
        expect(titles()).toHaveLength(4);
    });

    it('does not keep the other filters', () => {
        tasks = list();
        mount();
        fireEvent.click(screen.getByRole('button', {name: /有问题/}));
        cleanup();
        mount();
        expect(screen.getByRole('button', {name: /全部/}).getAttribute('aria-pressed')).toBe('true');
    });

    it('shows every record when sent to look at one task', () => {
        window.localStorage.setItem('fluentflow_records_filter', 'notes');
        tasks = list();
        mount({highlightTaskId: 'clip1'});
        expect(titles()).toContain('短片 clip1');
    });
});
