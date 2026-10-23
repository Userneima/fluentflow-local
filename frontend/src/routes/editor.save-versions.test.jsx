// @vitest-environment jsdom

// Every save names the version of the content it was made on, and the service
// refuses a save made on an older copy (decision 6.17). The page must keep its
// own version current (界面优化线二审查，2026-10-22):
//   - Typing again while the previous save is still on its way: the next save
//     names the version the previous one produced, so nothing is refused and
//     nothing typed is lost. Same for the note and the transcript.
//   - After the text model rewrites the note, the first edit is saved against
//     the rewritten note's version.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}, writesItsOwnNote: false};
let jobs = {};
let noteSaves = [];
let transcriptSaves = [];
let answerNoteSave = null;
let answerTranscriptSave = null;

vi.mock('../app/AppContext.jsx', async () => {
    const React = await vi.importActual('react');
    const Ctx = React.createContext(null);
    return {AppCtx: Ctx, useApp: () => React.useContext(Ctx)};
});

// The rich editor is tiptap; what matters here is what the page saves.
vi.mock('../components/RichNoteEditor.jsx', () => ({
    default: ({markdown, onChange}) => (
        <textarea aria-label="note" value={markdown} onChange={(e) => onChange(e.target.value)}/>
    ),
}));

// Each save stays on its way until the test answers it.
const pending = (list) => (taskId, payload) => new Promise((resolve, reject) => list.push({taskId, payload, resolve, reject}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({aiProvider: 'deepseek'}), saveSettings: () => {}}),
        useApi: () => (globalThis.__saveVersionsApi ||= {
            processVideoSSE: async () => ({}),
            fetchJobArtifactFile: async () => new File(['x'], 'x'),
            downloadJobMedia: async () => {},
            recordEvent: () => {},
            getJob: async (taskId) => jobs[taskId],
            saveTranscriptEdit: (...args) => answerTranscriptSave(...args),
            saveSummaryEdit: (...args) => answerNoteSave(...args),
            getCredentialsStatus: async () => ({deepseek_api_key_configured: true}),
            getVisualNoteAvailability: async () => ({available: false, reason: 'no frames'}),
        }),
    };
});

const {AppCtx} = await import('../app/AppContext.jsx');
const {default: Editor} = await import('./editor.jsx');

const Harness = ({initial}) => {
    const [lastResult, setLastResult] = useState(initial);
    const value = {
        lastResult, setLastResult,
        lastSourceFile: null, setLastSourceFile: () => {},
        addToHistory: () => {}, currentJob: null, setCurrentJob: () => {},
        addLarkExport: () => {}, runtimeConfig,
    };
    return <AppCtx.Provider value={value}><Editor/></AppCtx.Provider>;
};

const task = (extra = {}) => ({
    task_id: 't1',
    filename: 't1.mp4',
    display_title: '课',
    transcript_text: 'first line here\nsecond line here',
    segments: [{start: 0, end: 3, text: 'first line here'}, {start: 3, end: 6, text: 'second line here'}],
    summary_markdown: '# 笔记\n\n正文。',
    summary_status: 'completed',
    note_version: 'n7',
    transcript_version: 'v7',
    ...extra,
});

const mount = () => {
    jobs.t1 = {task_id: 't1', status: 'completed', result: task()};
    return render(
        <MemoryRouter initialEntries={['/editor']}>
            <Routes><Route path="/editor" element={<Harness initial={task()}/>}/></Routes>
        </MemoryRouter>,
    );
};

beforeEach(() => {
    jobs = {};
    noteSaves = [];
    transcriptSaves = [];
    answerNoteSave = pending(noteSaves);
    answerTranscriptSave = pending(transcriptSaves);
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => {};
    globalThis.fetch = vi.fn(async () => new Response('x', {status: 206, headers: {'content-type': 'video/mp4'}}));
});

afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
});

describe('typing again while the previous save is on its way', () => {
    it('saves the note against the version the previous save produced', async () => {
        mount();
        const note = await screen.findByLabelText('note');
        fireEvent.change(note, {target: {value: '# 笔记\n\n正文。第一处改动。'}});
        await waitFor(() => expect(noteSaves.length).toBe(1), {timeout: 3000});
        expect(noteSaves[0].payload.base_version).toBe('n7');

        // Still waiting for the first answer, the user types again.
        fireEvent.change(note, {target: {value: '# 笔记\n\n正文。第一处改动。第二处。'}});
        await act(async () => { await new Promise((r) => setTimeout(r, 1200)); });
        await act(async () => { noteSaves[0].resolve({result: {...task(), summary_markdown: noteSaves[0].payload.summary_markdown, note_version: 'n8'}}); });

        await waitFor(() => expect(noteSaves.length).toBe(2), {timeout: 3000});
        expect(noteSaves[1].payload.base_version).toBe('n8');
        expect(noteSaves[1].payload.summary_markdown).toContain('第二处');
    });

    it('saves the transcript against the version the previous save produced', async () => {
        mount();
        const line = await screen.findByDisplayValue('first line here');
        fireEvent.change(line, {target: {value: 'first line here, fixed'}});
        await waitFor(() => expect(transcriptSaves.length).toBe(1), {timeout: 3000});
        expect(transcriptSaves[0].payload.base_version).toBe('v7');

        fireEvent.change(line, {target: {value: 'first line here, fixed again'}});
        await act(async () => { await new Promise((r) => setTimeout(r, 1200)); });
        await act(async () => { transcriptSaves[0].resolve({result: {transcript_version: 'v8', transcript_edited: true}}); });

        await waitFor(() => expect(transcriptSaves.length).toBe(2), {timeout: 3000});
        expect(transcriptSaves[1].payload.base_version).toBe('v8');
        expect(transcriptSaves[1].payload.segments[0].text).toBe('first line here, fixed again');
    });
});

describe('after the text model writes the note', () => {
    it('saves the first edit against the written note\'s version', async () => {
        globalThis.fetch = vi.fn(async (url) => {
            if (String(url).includes('regenerate-summary')) {
                // The service has stored the new note; reading the task now
                // gives it with its version.
                jobs.t1 = {task_id: 't1', status: 'completed', result: task({summary_markdown: '# 新笔记\n\n重写的正文。', note_version: 'n9'})};
                return new Response(JSON.stringify({task_id: 't1', summary_markdown: '# 新笔记\n\n重写的正文。', summary_status: 'completed'}), {status: 200, headers: {'content-type': 'application/json'}});
            }
            return new Response('x', {status: 206, headers: {'content-type': 'video/mp4'}});
        });
        // A task whose note was not written: the page offers 写笔记.
        const noNote = task({summary_markdown: '', summary_status: 'failed', summary_error: 'AI summarization returned empty result', note_version: 'n7'});
        jobs.t1 = {task_id: 't1', status: 'completed', result: noNote};
        render(
            <MemoryRouter initialEntries={['/editor']}>
                <Routes><Route path="/editor" element={<Harness initial={noNote}/>}/></Routes>
            </MemoryRouter>,
        );
        fireEvent.click(await screen.findByTestId('write-note'));
        await act(async () => { fireEvent.click(await screen.findByText('开始写')); });
        await waitFor(() => expect(screen.getByLabelText('note').value).toContain('重写的正文'));

        fireEvent.change(screen.getByLabelText('note'), {target: {value: '# 新笔记\n\n重写的正文。我补一句。'}});
        await waitFor(() => expect(noteSaves.length).toBe(1), {timeout: 3000});
        expect(noteSaves[0].payload.base_version).toBe('n9');
    });
});

describe('opening a copy kept in the browser', () => {
    it('shows the service\'s transcript when it was edited since, even if no longer', async () => {
        // The browser's copy predates a fixed typo: same length, no version.
        const cached = task({transcript_version: undefined, note_version: undefined,
            segments: [{start: 0, end: 3, text: 'frist line here'}, {start: 3, end: 6, text: 'second line here'}],
            transcript_text: 'frist line here\nsecond line here'});
        jobs.t1 = {task_id: 't1', status: 'completed', result: task({transcript_version: 'v8'})};
        render(
            <MemoryRouter initialEntries={['/editor']}>
                <Routes><Route path="/editor" element={<Harness initial={cached}/>}/></Routes>
            </MemoryRouter>,
        );
        expect(await screen.findByDisplayValue('first line here')).toBeTruthy();
        expect(screen.queryByDisplayValue('frist line here')).toBeNull();
    });
});
