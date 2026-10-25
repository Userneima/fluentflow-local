// @vitest-environment jsdom

// Carrying a stopped task on from the editor (review of task steps batch 2,
// 2026-10-25). What the person should get: a task whose note they stopped
// says so in the header; clicking 接着做 carries the same task on, and once
// the note is written it appears in the note pane without reloading the page.

import {createContext, useContext, useState} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const AppState = createContext(null);

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => useContext(AppState),
}));

vi.mock('../components/NoteEditor.jsx', () => ({
    default: ({markdown, onChange}) => (
        <textarea aria-label="note" value={markdown} onChange={(e) => onChange(e.target.value)}/>
    ),
}));

const LONG_TEXT = 'hello world '.repeat(40);
const STOPPED_NOTE = '已停下，点「接着做」从这一步开始。';
const stoppedResult = {
    task_id: 't1',
    filename: 'lecture.mp4',
    transcript_text: LONG_TEXT,
    segments: [{start: 0, end: 3, text: LONG_TEXT}],
    summary_markdown: '',
    summary_status: 'failed',
    summary_error: STOPPED_NOTE,
    summary_skipped: false,
};
const activity = (extra) => ({busy: false, open_steps: [], failed_steps: [], stopped: false, resume_from: null, ...extra});

let jobNow = null;
const api = {
    processVideoSSE: async () => ({}),
    fetchJobSourceFile: async () => { throw new Error('no source'); },
    fetchJobArtifactFile: async () => { throw new Error('no artifact'); },
    recordEvent: () => {},
    getJob: vi.fn(async () => jobNow),
    getJobActivity: vi.fn(async () => jobNow),
    resumeJob: vi.fn(async () => {
        jobNow = {task_id: 't1', status: 'completed', result: {...stoppedResult, summary_status: 'pending', summary_error: null},
            activity: activity({busy: true, label: '正在写笔记', current_step: 'note'})};
        return {ok: true, resume_from: {step: 'note', label: '写笔记'}};
    }),
    saveTranscriptEdit: async () => ({}),
    saveSummaryEdit: vi.fn(async () => ({})),
    startJobVisualNote: vi.fn(async () => ({ok: true, accepted: true})),
    getVisualNoteAvailability: vi.fn(async () => ({available: true, reason: null})),
    getCredentialsStatus: async () => ({}),
};

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({}), saveSettings: () => {}}),
        useApi: () => api,
    };
});

const {default: Editor} = await import('./editor.jsx');

const Harness = () => {
    const [lastResult, setLastResult] = useState(stoppedResult);
    const value = {
        lastResult, setLastResult, lastSourceFile: null, setLastSourceFile: () => {}, addToHistory: () => {},
        currentJob: null, setCurrentJob: () => {}, addLarkExport: () => {},
        runtimeConfig: {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}},
    };
    return <AppState.Provider value={value}><MemoryRouter><Editor/></MemoryRouter></AppState.Provider>;
};

const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => {
    vi.useFakeTimers();
    jobNow = {task_id: 't1', status: 'completed', result: stoppedResult,
        activity: activity({stopped: true, stopped_steps: [{step: 'note', label: '写笔记'}], resume_from: {step: 'note', label: '写笔记'}})};
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => {};
});

afterEach(() => {
    cleanup();
    vi.useRealTimers();
});

describe('carrying a stopped note on from the editor', () => {
    it('shows the new note once it is written, without reloading', async () => {
        render(<Harness/>);
        await advance(0);
        await advance(0);
        const offer = screen.getByText('停在了「写笔记」这一步，接着做');
        // Stopped is not failed: the note pane says it in grey.
        expect(screen.getByTestId('note-failure-reason').dataset.tone).toBe('stopped');
        fireEvent.click(offer);
        await advance(0);
        expect(api.resumeJob).toHaveBeenCalledWith('t1', expect.anything());

        await advance(5000);
        expect(screen.queryByDisplayValue('# 新笔记')).toBeNull();

        jobNow = {task_id: 't1', status: 'completed',
            result: {...stoppedResult, summary_status: 'completed', summary_error: null, summary_markdown: '# 新笔记'},
            activity: activity({})};
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('# 新笔记');
    });
});
