// @vitest-environment jsdom

// The note is saved only when the user changed it (2026-10-22 incident: the
// editor opened from the task list saved the list's 240-character preview over
// a 40,000-character note, and did so again on every open). Written from what
// the owner asked for:
//   - Until the full note has arrived, the note is not editable and nothing is
//     saved: not on load, not when the full task arrives, not on a poll, not
//     when another task is opened.
//   - Once the user types in the note, it is saved as usual.
//   - A newer note taken in from elsewhere cannot be undone back to the older
//     text (that would save the old note over the newer one).
//   - A save refused because the note changed elsewhere brings the newer note
//     in, but not while a table cell is being typed in: it waits until the
//     cell is left, and then the newer note and a notice come.
// The real note editor is mounted, since a mocked one cannot show it emitting
// a change by itself.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';
import {EditorView} from '@codemirror/view';
import {undo} from '@codemirror/commands';

let runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}, writesItsOwnNote: true};
let jobs = {};
let saves = [];
let setResultFromTest = null;
let releaseFullTask = null;
let refuseSaves = 0;

vi.mock('../app/AppContext.jsx', async () => {
    const React = await vi.importActual('react');
    const Ctx = React.createContext(null);
    return {AppCtx: Ctx, useApp: () => React.useContext(Ctx)};
});

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({}), saveSettings: () => {}}),
        // One object for the whole page, as the real useApi gives (useMemo).
        useApi: () => (globalThis.__noteLoadSaveApi ||= {
            processVideoSSE: async () => ({}),
            fetchJobArtifactFile: async () => new File(['x'], 'x'),
            downloadJobMedia: async () => {},
            recordEvent: () => {},
            // The full task arrives when the test lets it, as it does after a
            // second or more for a long recording.
            getJob: (taskId) => new Promise((resolve, reject) => {
                const answer = () => (jobs[taskId] ? resolve(jobs[taskId]) : reject(Object.assign(new Error('Job not found'), {status: 404})));
                if (releaseFullTask) { releaseFullTask.push(answer); return; }
                answer();
            }),
            saveTranscriptEdit: async () => ({}),
            saveSummaryEdit: async (taskId, payload) => {
                saves.push({taskId, ...payload});
                if (refuseSaves > 0) {
                    refuseSaves -= 1;
                    throw Object.assign(new Error('笔记在别处被改过，已换成最新的版本。'), {stale: true, status: 409});
                }
                return {};
            },
            getCredentialsStatus: async () => ({visual_note_available: true}),
            getVisualNoteAvailability: async () => ({available: true, reason: null}),
        }),
    };
});

// jsdom has no layout. The note editor scrolls the selection into view after
// a change, which asks for rectangles; without these the run ends with an
// unhandled error and a failing exit code even though every test passed.
const noRects = () => ({length: 0, item: () => null, [Symbol.iterator]: function* () {}});
const emptyRect = () => ({x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({})});
Range.prototype.getClientRects = noRects;
Range.prototype.getBoundingClientRect = emptyRect;
Element.prototype.getClientRects = noRects;

const {AppCtx} = await import('../app/AppContext.jsx');
const {default: Editor} = await import('./editor.jsx');

const Harness = ({initial}) => {
    const [lastResult, setLastResult] = useState(initial);
    setResultFromTest = setLastResult;
    const value = {
        lastResult, setLastResult,
        lastSourceFile: null, setLastSourceFile: () => {},
        addToHistory: () => {}, currentJob: null, setCurrentJob: () => {},
        addLarkExport: () => {}, runtimeConfig,
    };
    return <AppCtx.Provider value={value}><Editor/></AppCtx.Provider>;
};

const mount = (url, initial = null) => render(
    <MemoryRouter initialEntries={[url]}>
        <Routes><Route path="/editor" element={<Harness initial={initial}/>}/></Routes>
    </MemoryRouter>,
);

// A note shaped like the incident's: an HTML comment, headings, a table, a
// callout, images and long paragraphs.
const FULL_NOTE = [
    '# 对罗福莉的访谈',
    '',
    '<!-- 第 1/3 部分（00:00–73:27） -->',
    '',
    '## 一、开场片段：入场券和算力配比',
    '',
    '正片之前有一段剪辑，罗福莉在里面给了几个判断：',
    '',
    '- 现有的这些能力，快的话一两个月、慢的话三四个月，都能被别人很快"洗"出来，所以**环境比经验更重要**。',
    '- **算力配比**：研究、预训练（pre-train）、后训练（post-train）三块。',
    '',
    '> [!question] 为什么是 3:1:1',
    '> 她没有展开。',
    '',
    '| 阶段 | 占比 |',
    '| --- | --- |',
    '| 研究 | 3 |',
    '| 预训练 | 1 |',
    '',
    '![关键画面](/jobs/t1/artifacts/frame?file=note_0001.jpg)',
    '',
    ...Array.from({length: 60}, (_, i) => `第 ${i + 1} 段：${'这是一段很长的笔记正文，'.repeat(12)}`),
].join('\n');

const task = (extra = {}) => ({
    task_id: 't1',
    filename: 't1.mp4',
    display_title: '访谈',
    transcript_text: 'hello there. '.repeat(400),
    segments: Array.from({length: 40}, (_, i) => ({start: i * 2, end: i * 2 + 2, text: `第 ${i} 句`})),
    summary_markdown: FULL_NOTE,
    summary_status: 'completed',
    note_version: 7,
    transcript_version: 3,
    visual_note: {status: 'completed'},
    ...extra,
});

// What the task list carries: the note cut to 240 characters, no segments,
// no version.
const listRow = () => ({
    task_id: 't1',
    filename: 't1.mp4',
    display_title: '访谈',
    transcript_text: 'hello there. '.repeat(400).slice(0, 240),
    summary_markdown: FULL_NOTE.slice(0, 240),
    summary_preview: FULL_NOTE.slice(0, 240),
    summary_chars: FULL_NOTE.length,
    summary_status: 'completed',
});

beforeEach(() => {
    jobs = {t1: {task_id: 't1', status: 'completed', result: task()}};
    saves = [];
    releaseFullTask = null;
    refuseSaves = 0;
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => {};
    globalThis.fetch = vi.fn(async () => new Response('x', {status: 206, headers: {'content-type': 'video/mp4'}}));
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    cleanup();
});

const noteBox = () => document.querySelector('.cm-content');
// CodeMirror keeps only the visible lines in the page; the whole note is the
// editor's document.
const noteText = () => {
    const editor = document.querySelector('.cm-editor');
    return editor ? EditorView.findFromDOM(editor)?.state.doc.toString() || '' : '';
};
const settle = async (ms = 2500) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };

vi.setConfig({testTimeout: 15000});

describe('opening a task from the list, whose note is only a preview', () => {
    it('shows no editable note until the full note arrives, and saves nothing', async () => {
        releaseFullTask = [];
        mount('/editor', listRow());
        await settle(1200);
        // The preview is not offered as the note.
        expect(noteBox()).toBeNull();

        await act(async () => { const pending = releaseFullTask; releaseFullTask = null; pending.forEach((answer) => answer()); });
        await waitFor(() => expect(noteText()).toContain('第 60 段'));
        await settle();
        expect(saves).toEqual([]);
    });

    // The incident: a change to the note while only the preview was on the
    // page was saved as the whole note.
    it('never saves the preview, even if the note is changed while the full note loads', async () => {
        releaseFullTask = [];
        mount('/editor', listRow());
        await settle(800);
        const toolbar = document.querySelector('#note-line-style');
        if (toolbar) fireEvent.change(toolbar, {target: {value: 'h2'}});
        await settle(1500);
        await act(async () => { const pending = releaseFullTask; releaseFullTask = null; pending.forEach((answer) => answer()); });
        await settle(1500);
        expect(saves.filter((save) => save.summary_markdown.length < FULL_NOTE.length - 50)).toEqual([]);
    });

    it('saves nothing when it is opened from the address, after a refresh', async () => {
        mount('/editor?task=t1');
        await waitFor(() => expect(noteText()).toContain('第 60 段'));
        await settle();
        expect(saves).toEqual([]);
    });

    it('saves nothing when the task is updated by a poll, or another task is opened', async () => {
        mount('/editor', task());
        await waitFor(() => expect(noteBox()).toBeTruthy());
        await act(async () => { setResultFromTest((prev) => ({...prev, note_version: 8, summary_markdown: `${FULL_NOTE}\n\n新加的一段。`})); });
        await settle(1500);
        // The newer note is what the editor shows (an Obsidian edit taken in).
        expect(noteText()).toContain('新加的一段。');
        await act(async () => { setResultFromTest(task({task_id: 't2', summary_markdown: '# 另一篇\n\n正文。'})); });
        await settle();
        expect(saves).toEqual([]);
    });
});

describe('the user editing the note', () => {
    it('is saved, whole and against the version it was made on', async () => {
        mount('/editor', task());
        await waitFor(() => expect(noteBox()).toBeTruthy());
        await settle(500);
        // The user turns a paragraph into a heading from the toolbar (jsdom
        // cannot type into the editor; any change of the user's will do).
        fireEvent.change(document.querySelector('#note-line-style'), {target: {value: 'h2'}});
        await waitFor(() => expect(saves.length).toBe(1), {timeout: 4000});
        expect(saves[0].taskId).toBe('t1');
        expect(saves[0].summary_markdown).toMatch(/^## /m);
        expect(saves[0].summary_markdown.length).toBeGreaterThan(FULL_NOTE.length - 50);
        expect(saves[0].base_version).toBe(7);
    });
});

describe('a newer note and the user\'s own edits', () => {
    it('cannot undo a newer note back to the older one', async () => {
        mount('/editor', task());
        await waitFor(() => expect(noteBox()).toBeTruthy());
        await settle(500);
        const newer = `${FULL_NOTE}\n\n在 Obsidian 里加的一段。`;
        await act(async () => { setResultFromTest((prev) => ({...prev, note_version: 8, summary_markdown: newer})); });
        await waitFor(() => expect(noteText()).toBe(newer));
        expect(document.querySelector('button[aria-label="撤销"]').disabled).toBe(true);
        const editor = EditorView.findFromDOM(document.querySelector('.cm-editor'));
        act(() => { undo(editor); });
        await settle(1500);
        expect(noteText()).toBe(newer);
        expect(saves).toEqual([]);
    });

    it('brings in the newer note after a refused save only once the open table cell is left', async () => {
        mount('/editor', task());
        await waitFor(() => expect(noteBox()).toBeTruthy());
        await settle(500);
        refuseSaves = 1;
        const newer = `${FULL_NOTE}\n\n在 Obsidian 里加的一段。`;
        jobs.t1 = {task_id: 't1', status: 'completed', result: task({note_version: 9, summary_markdown: newer})};
        // An edit whose save will be refused, then a table cell opened.
        fireEvent.change(document.querySelector('#note-line-style'), {target: {value: 'h2'}});
        const cell = [...document.querySelectorAll('.cm-ob-table-wrap td')].find((node) => node.textContent === '研究');
        act(() => { fireEvent.focus(cell); });
        await waitFor(() => expect(saves.length).toBe(1), {timeout: 4000});
        await settle(800);
        // The refusal came while the cell was open: the cell is still there and
        // the note on the page is the user's.
        expect(cell.isConnected).toBe(true);
        expect(noteText()).not.toBe(newer);
        cell.textContent = '探索';
        act(() => { fireEvent.blur(cell); });
        await waitFor(() => expect(noteText()).toBe(newer), {timeout: 4000});
        expect(document.body.textContent).toContain('笔记在别处被改过，已换成最新的版本。');
        await settle(1500);
        // Nothing older was saved on top of the newer note's version.
        expect(saves.filter((save) => save.base_version === 9)).toEqual([]);
    });
});
