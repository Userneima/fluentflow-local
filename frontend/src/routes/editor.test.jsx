// @vitest-environment jsdom

// What the editor SENDS. Each test here corresponds to a failure that reached a
// real user's data this week and was, at the time, invisible to the suite —
// every fix was unit-tested through an extracted helper, while the damage
// happened in the debounced request the component fires.

import {act, cleanup, fireEvent, screen, waitFor} from '@testing-library/react';
import {afterAll, afterEach, beforeAll, describe, expect, it, vi} from 'vitest';
import {installEditorDomStubs, renderEditor, sseBody} from './editor-harness.jsx';

let restoreDom;
const originalFetch = globalThis.fetch;

beforeAll(() => { restoreDom = installEditorDomStubs(); });
afterAll(() => { restoreDom(); globalThis.fetch = originalFetch; });
afterEach(() => { cleanup(); vi.useRealTimers(); localStorage.clear(); });

const TASK = 'task-1';
const FULL_NOTE = '# 完整笔记\n\n第一段正文。';
const TRANSCRIPT = '这是一句转录。'.repeat(80);

const segments = () => Array.from({length: 6}, (_, index) => ({
    start: index * 5, end: index * 5 + 5, text: `第 ${index + 1} 句转录。`,
}));

/** What `GET /jobs/{id}` returns: the record, with bodies. */
const record = (overrides = {}) => ({
    task_id: TASK,
    status: 'completed',
    filename: 'lecture.mp4',
    display_title: 'lecture',
    summary_markdown: FULL_NOTE,
    summary_status: 'completed',
    transcript_text: TRANSCRIPT,
    raw_segments: segments(),
    display_segments: segments(),
    audio_duration_seconds: 600,
    ...overrides,
});

/** What `GET /jobs` returns: previews, and it says so. */
const listRow = (overrides = {}) => ({
    task_id: TASK,
    status: 'completed',
    filename: 'lecture.mp4',
    display_title: 'lecture',
    result_partial: true,
    summary_preview: FULL_NOTE.slice(0, 12),
    summary_markdown_chars: FULL_NOTE.length,
    summary_status: 'completed',
    transcript_text_preview: TRANSCRIPT.slice(0, 12),
    transcript_text_chars: TRANSCRIPT.length,
    audio_duration_seconds: 600,
    ...overrides,
});

const noteEditor = () => screen.getByRole('textbox', {name: '编辑笔记正文'});

const pasteInto = (editor, text) => {
    fireEvent.focus(editor);
    fireEvent.paste(editor, {
        clipboardData: {
            files: [],
            types: ['text/plain'],
            getData: (type) => (type === 'text/plain' ? text : ''),
        },
    });
};

const typeInNote = async (text) => pasteInto(noteEditor(), text);

/** Let the 800ms autosave debounce elapse. */
const letAutosaveFire = async () => {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 1100));
    });
};

describe('opening a list row', () => {
    // The bug: a 240-char preview reached the note editor, the first keystroke
    // armed autosave, and the PATCH replaced a 13,021-character note.
    it('never saves anything while it holds previews instead of the record', async () => {
        // The record fetch never resolves, so the editor stays partial for the
        // whole test — the exact window in which the note was destroyed.
        const {fetch: stub} = renderEditor({
            result: listRow(),
            routes: [
                {match: `/jobs/${TASK}/summary`, method: 'PATCH', reply: {result: {}}},
                {match: `/jobs/${TASK}`, method: 'GET', reply: () => new Promise(() => {})},
            ],
        });
        await waitFor(() => expect(stub.calling(`/jobs/${TASK}`, 'GET').length).toBe(1));

        // Type if there is anywhere to type. A regression that makes previews
        // editable has to surface here as a save, not pass for lack of trying.
        const editor = screen.queryByRole('textbox', {name: '编辑笔记正文'});
        if (editor) pasteInto(editor, '误触的一个字');
        await letAutosaveFire();

        expect(stub.calling('/summary', 'PATCH')).toHaveLength(0);
        expect(stub.calling('/transcript', 'PATCH')).toHaveLength(0);
    });

    it('fetches the record rather than trusting the previews it was handed', async () => {
        const {fetch: stub} = renderEditor({
            result: listRow(),
            routes: [{match: `/jobs/${TASK}`, method: 'GET', reply: {result: record()}}],
        });
        await waitFor(() => expect(stub.calling(`/jobs/${TASK}`, 'GET').length).toBe(1));
    });

    it('shows the full note once the record arrives, not the preview', async () => {
        renderEditor({
            result: listRow(),
            routes: [{match: `/jobs/${TASK}`, method: 'GET', reply: {result: record()}}],
        });
        // Rendered twice on purpose: the editing surface and the offscreen node
        // the PDF/DOCX export reads from.
        await waitFor(() => expect(screen.getAllByText('第一段正文。').length).toBeGreaterThan(0));
        expect(screen.queryByText(FULL_NOTE.slice(0, 12))).toBeNull();
    });

    it('leaves the note read-only until the record has arrived', async () => {
        // A stalled fetch keeps it partial: the editing surface must not exist.
        renderEditor({
            result: listRow(),
            routes: [{match: `/jobs/${TASK}`, method: 'GET', reply: () => new Promise(() => {})}],
        });
        await waitFor(() => expect(screen.getByText('正在读取完整笔记…')).toBeTruthy());
        expect(screen.queryByRole('textbox', {name: '编辑笔记正文'})).toBeNull();
    });
});

describe('editing the record', () => {
    it('saves the whole note, not a truncation of it', async () => {
        const {fetch: stub} = renderEditor({
            result: record(),
            routes: [
                {match: `/jobs/${TASK}/summary`, method: 'PATCH', reply: {result: {}}},
                {match: `/jobs/${TASK}`, method: 'GET', reply: {result: record()}},
            ],
        });

        await waitFor(() => expect(noteEditor()).toBeTruthy());
        await typeInNote('补充一句。');
        await letAutosaveFire();

        const saves = stub.calling('/summary', 'PATCH');
        expect(saves.length).toBeGreaterThan(0);
        const saved = saves.at(-1).body.summary_markdown;
        expect(saved).toContain('第一段正文。');
        expect(saved).toContain('补充一句。');
        expect(saved.length).toBeGreaterThan(FULL_NOTE.length);
    });

    it('debounces instead of saving on every keystroke', async () => {
        const {fetch: stub} = renderEditor({
            result: record(),
            routes: [
                {match: `/jobs/${TASK}/summary`, method: 'PATCH', reply: {result: {}}},
                {match: `/jobs/${TASK}`, method: 'GET', reply: {result: record()}},
            ],
        });
        await waitFor(() => expect(noteEditor()).toBeTruthy());

        await typeInNote('一');
        await typeInNote('二');
        await typeInNote('三');
        await letAutosaveFire();

        expect(stub.calling('/summary', 'PATCH')).toHaveLength(1);
    });
});

describe('regenerating the note', () => {
    const regenerateRoutes = (streamReply) => [
        {match: '/regenerate-summary/stream', method: 'POST', reply: streamReply},
        {match: `/jobs/${TASK}`, method: 'GET', reply: {result: record()}},
    ];

    it('streams progress and installs the regenerated note', async () => {
        const onResultChange = vi.fn();
        renderEditor({
            result: record(),
            onResultChange,
            routes: regenerateRoutes({
                sse: true,
                body: sseBody([
                    {stage: 'summary', progress: 30, note_step: 'evidence', note_step_label: '提取要点',
                     note_step_completed: 2, note_step_total: 7, event_index: 0},
                    {stage: 'done', progress: 100, event_index: 1,
                     result: {task_id: TASK, summary_markdown: '# 重生后的笔记'}},
                ]),
            }),
        });

        await waitFor(() => expect(screen.getByRole('button', {name: /重生笔记/})).toBeTruthy());
        fireEvent.click(screen.getByRole('button', {name: /重生笔记/}));
        fireEvent.click(await screen.findByRole('button', {name: '确认重生笔记'}));

        await waitFor(() => {
            const installed = onResultChange.mock.calls.map(([value]) => value?.summary_markdown);
            expect(installed).toContain('# 重生后的笔记');
        });
    });

    // The bug: the stream was the work, so a backgrounded tab tore generation
    // down mid-run. It is a job task now, and the client re-attaches.
    it('re-attaches to the job events when the stream drops mid-run', async () => {
        let streamed = false;
        const {fetch: stub} = renderEditor({
            result: record(),
            routes: [
                {match: '/regenerate-summary/stream', method: 'POST', reply: () => {
                    streamed = true;
                    return {sse: true, body: sseBody([
                        {stage: 'summary', progress: 30, note_step: 'evidence', event_index: 0},
                    ])};
                }},
                {match: `/jobs/${TASK}/events`, method: 'GET', reply: {
                    sse: true,
                    body: sseBody([
                        {stage: 'summary', progress: 80, note_step: 'coverage', event_index: 1},
                        {stage: 'done', progress: 100, event_index: 2,
                         result: {task_id: TASK, summary_markdown: '# 重连后完成的笔记'}},
                    ]),
                }},
                {match: `/jobs/${TASK}`, method: 'GET', reply: {result: record()}},
            ],
        });

        await waitFor(() => expect(screen.getByRole('button', {name: /重生笔记/})).toBeTruthy());
        fireEvent.click(screen.getByRole('button', {name: /重生笔记/}));
        fireEvent.click(await screen.findByRole('button', {name: '确认重生笔记'}));

        await waitFor(() => expect(streamed).toBe(true));
        await waitFor(() => expect(stub.calling(`/jobs/${TASK}/events`, 'GET').length).toBe(1));
        // Resumed from where the dropped stream stopped, so no event is replayed.
        expect(stub.calling(`/jobs/${TASK}/events`, 'GET')[0].url).toContain('since=1');
    });
});
