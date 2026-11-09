// @vitest-environment jsdom

// What the note pane says while FluentFlow writes a note (user feedback,
// 2026-11-09): the stage the writer is really on, the chapters or parts and
// which are done, the time so far, and the text written so far. With nothing
// reported it keeps the editor's one sentence.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';

const getNoteProgress = vi.fn();
vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    const t = (key) => actual.msgs.zh[key] ?? key;
    return {...actual, useApi: () => ({getNoteProgress}), useI18n: () => ({t, lang: 'zh'})};
});

import NoteWritingProgress from './NoteWritingProgress.jsx';
import {msgs} from '../app/shared.jsx';
import {draftBlocks, noteProgressDetail, noteProgressShort, noteProgressStatus} from '../lib/noteProgress.js';
import {taskStatusLabel} from '../lib/taskState.js';

const t = (key) => msgs.zh[key] ?? key;
const tEn = (key) => msgs.en[key] ?? key;

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

const base = {
    writer: 'text', stage: 'writing', elapsed_seconds: 75, read_total: 0, read_done: 0,
    parts_total: 0, parts_done: 0, part_index: 0, part_starts_at: '', part_ends_at: '',
    seconds_per_part: null, part_elapsed_seconds: null, chapters: [], written_chars: 0,
};

describe('what the writer is doing, in words', () => {
    it('says how many stretches of transcript have been read for points', () => {
        expect(noteProgressStatus({...base, stage: 'reading', read_total: 8, read_done: 3}, t)).toBe('正在读转录稿、提取要点：已读 3/8 段');
    });

    it('says how many chapters are written once they are chosen', () => {
        const chapters = [{title: '开场', done: true}, {title: '推导', done: false}, {title: '例子', done: false}];
        expect(noteProgressStatus({...base, chapters}, t)).toBe('正在写各章：已写好 1/3 章');
        expect(noteProgressShort({...base, chapters}, t)).toBe('写好 1/3 章');
    });

    it('names the part being written, of how many, over which stretch of the recording', () => {
        const progress = {...base, writer: 'claude', parts_total: 5, part_index: 2, part_starts_at: '20:00', part_ends_at: '40:00'};
        expect(noteProgressStatus(progress, t)).toBe('正在写第 2 部分，共 5 部分（20:00–40:00）');
        expect(noteProgressStatus(progress, tEn, 'en')).toBe('Writing part 2 of 5 (20:00–40:00)');
        expect(noteProgressShort(progress, t)).toBe('第 2/5 部分');
    });

    it('says how much a single draft has written while it streams', () => {
        expect(noteProgressStatus({...base, written_chars: 1234}, t)).toBe('正在写笔记：已写 1234 字');
        expect(noteProgressStatus({...base, written_chars: 0}, t)).toBe('正在写笔记，等模型返回第一段文字');
    });

    it('promises no time it cannot know, and estimates the rest of a note in parts from its own pace', () => {
        expect(noteProgressDetail({...base}, t)).toBe('已用 1 分 15 秒');
        const parts = {...base, writer: 'claude', parts_total: 4, parts_done: 1, part_index: 2, seconds_per_part: 120, part_elapsed_seconds: 30};
        // Three parts left at two minutes each, half a minute into the second.
        expect(noteProgressDetail(parts, t)).toBe('已用 1 分 15 秒，已写好 1/4 部分，按目前的速度大约还要 5 分 30 秒');
    });

    it('turns a draft into headings, items and paragraphs, leaving out pictures and part markers', () => {
        expect(draftBlocks('<!-- 第 1/2 部分 -->\n## 一、开场\n\n> **定义**很重要。\n第二行\n- 要点\n![图](frames/a.jpg)')).toEqual([
            {kind: 'h2', text: '一、开场'},
            {kind: 'p', text: '定义很重要。 第二行'},
            {kind: 'li', text: '要点'},
        ]);
    });
});

describe('the task status', () => {
    const writing = (progress) => ({
        status: 'completed', result: {summary_status: 'pending'},
        activity: {busy: true, current_step: 'note', label: '正在写笔记', note_progress: progress},
    });

    it('carries how far the note has got', () => {
        expect(taskStatusLabel(writing({...base, stage: 'planning'}), 'zh')).toBe('写笔记中 · 定章节');
    });

    it('stays as it was when the writer reports nothing', () => {
        expect(taskStatusLabel(writing(null), 'zh')).toBe('写笔记中');
    });
});

describe('NoteWritingProgress', () => {
    it('shows the stage, the chapters and the text written so far', async () => {
        getNoteProgress.mockResolvedValue({
            progress: {...base, chapters: [{title: '开场', done: true}, {title: '推导', done: false}]},
            draft: '## 开场\n\n最小二乘法的定义。',
        });
        render(<NoteWritingProgress taskId="t1" fallback="正在写笔记，写好会自动出现。"/>);
        expect(await screen.findByText('正在写各章：已写好 1/2 章')).toBeTruthy();
        expect(screen.getByText('推导')).toBeTruthy();
        expect(screen.getByText('最小二乘法的定义。')).toBeTruthy();
        expect(getNoteProgress).toHaveBeenCalledWith('t1', {});
    });

    it('keeps the one sentence when the writer reports nothing', async () => {
        getNoteProgress.mockResolvedValue({progress: null, draft: ''});
        render(<NoteWritingProgress taskId="t1" fallback="正在写笔记，写好会自动出现。"/>);
        await waitFor(() => expect(getNoteProgress).toHaveBeenCalled());
        expect(screen.getByText('正在写笔记，写好会自动出现。')).toBeTruthy();
        expect(screen.queryByTestId('note-writing-progress')).toBeNull();
    });

    it('says the note is being saved and tells the page once the writer stops reporting', async () => {
        getNoteProgress
            .mockResolvedValueOnce({progress: {...base, written_chars: 40}, draft: '## 开场\n\n写好的一段。'})
            .mockResolvedValue({progress: null, draft: ''});
        const onWriterDone = vi.fn();
        vi.useFakeTimers({shouldAdvanceTime: true});
        try {
            render(<NoteWritingProgress taskId="t1" fallback="正在写笔记。" onWriterDone={onWriterDone}/>);
            expect(await screen.findByText('正在写笔记：已写 40 字')).toBeTruthy();
            await vi.advanceTimersByTimeAsync(3000);
            expect(await screen.findByText('写好了，正在保存')).toBeTruthy();
            expect(screen.getByText('写好的一段。')).toBeTruthy();
            await vi.advanceTimersByTimeAsync(3000);
            expect(onWriterDone).toHaveBeenCalledTimes(1);
        } finally {
            vi.useRealTimers();
        }
    });
});

describe('NoteWritingProgress on another task', () => {
    it('shows nothing of the task before', async () => {
        getNoteProgress.mockResolvedValueOnce({progress: {...base, written_chars: 40}, draft: '上一个任务的草稿。'});
        const view = render(<NoteWritingProgress taskId="t1" fallback="正在写笔记。"/>);
        expect(await screen.findByText('上一个任务的草稿。')).toBeTruthy();
        getNoteProgress.mockResolvedValue({progress: null, draft: ''});
        view.rerender(<NoteWritingProgress taskId="t2" fallback="正在写笔记。"/>);
        expect(screen.queryByText('上一个任务的草稿。')).toBeNull();
        expect(screen.getByText('正在写笔记。')).toBeTruthy();
    });
});
