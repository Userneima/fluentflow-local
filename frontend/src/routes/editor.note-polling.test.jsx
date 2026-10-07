// @vitest-environment jsdom

// The note after transcription, and "重生笔记".
//
// What the person should get:
//   - A task opened while its note is still being written shows the note once
//     it is written, without reopening the task. The page asks every five
//     seconds and stops asking once the note is in or has failed, when the page
//     closes, or when another task is opened.
//   - A note they have typed and not yet saved is never replaced by the poll.
//   - When Claude writes the notes, "重生笔记" asks Claude to rewrite it from the
//     transcript and the frames, says so in the dialog, and shows the new note
//     when it is written. A busy queue is reported in plain Chinese.
//   - When a text model writes the notes, the dialog names it, and warns that
//     the screenshots go if the current note was written by Claude.

import {createContext, useContext, useState} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const AppState = createContext(null);
let runtimeConfig = {};
let credentialStatus = {};
let switchTask = null;

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => useContext(AppState),
}));

// The rich editor is tiptap, which jsdom cannot type into. What matters here is
// what the page hands it and what it gets back.
vi.mock('../components/RichNoteEditor.jsx', () => ({
    default: ({markdown, onChange}) => (
        <textarea aria-label="note" value={markdown} onChange={(e) => onChange(e.target.value)}/>
    ),
}));

const api = {
    processVideoSSE: async () => ({}),
    fetchJobSourceFile: async () => { throw new Error('no source'); },
    fetchJobArtifactFile: async () => { throw new Error('no artifact'); },
    uploadJobPlaybackAudio: async () => ({}),
    recordEvent: () => {},
    getJob: vi.fn(),
    saveTranscriptEdit: async () => ({}),
    saveSummaryEdit: vi.fn(async () => ({})),
    startJobVisualNote: vi.fn(async () => ({ok: true, accepted: true})),
    getCredentialsStatus: async () => credentialStatus,
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

// Long enough that the page does not go and fetch the full transcript first.
const LONG_TEXT = 'hello world '.repeat(40);
const task = (extra = {}) => ({
    task_id: 't1',
    filename: 'lecture.mp4',
    transcript_text: LONG_TEXT,
    segments: [{start: 0, end: 3, text: LONG_TEXT}],
    summary_markdown: '',
    summary_status: 'pending',
    summary_skipped: false,
    ...extra,
});
const jobWith = (result, stage = 'done') => ({task_id: result.task_id, status: 'completed', stage, result});

const Harness = ({initial}) => {
    const [lastResult, setLastResult] = useState(initial);
    switchTask = setLastResult;
    const value = {
        lastResult,
        setLastResult,
        lastSourceFile: null,
        setLastSourceFile: () => {},
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        addLarkExport: () => {},
        runtimeConfig,
    };
    return <AppState.Provider value={value}><MemoryRouter><Editor/></MemoryRouter></AppState.Provider>;
};

const mount = (initial) => render(<Harness initial={initial}/>);
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
// Answers the polls in order; the last answer repeats.
const answerPolls = (...results) => {
    let i = 0;
    api.getJob.mockImplementation(async () => {
        const next = results[Math.min(i, results.length - 1)];
        i += 1;
        return next;
    });
};

beforeEach(() => {
    vi.useFakeTimers();
    runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};
    credentialStatus = {};
    api.getJob.mockReset();
    api.saveSummaryEdit.mockReset();
    api.saveSummaryEdit.mockImplementation(async () => ({}));
    api.startJobVisualNote.mockReset();
    api.startJobVisualNote.mockImplementation(async () => ({ok: true, accepted: true}));
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => {};
});

afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe('a task opened while its note is still being written', () => {
    it('shows the note once it is written, asking every five seconds', async () => {
        answerPolls(
            jobWith(task(), 'note'),
            jobWith(task({summary_status: 'completed', summary_markdown: '# Claude note'})),
        );
        mount(task());
        await advance(0);
        expect(screen.getByText('edit.summaryPending')).toBeTruthy();

        await advance(4900);
        expect(api.getJob).not.toHaveBeenCalled();
        await advance(100);
        expect(api.getJob).toHaveBeenCalledTimes(1);
        expect(screen.getByText('edit.summaryPending')).toBeTruthy();

        await advance(5000);
        expect(api.getJob).toHaveBeenCalledTimes(2);
        expect(screen.getByLabelText('note').value).toBe('# Claude note');
        expect(screen.queryByText('edit.summaryPending')).toBeNull();
    });

    it('stops asking once the note is in', async () => {
        answerPolls(jobWith(task({summary_status: 'completed', summary_markdown: 'done'})));
        mount(task());
        await advance(5000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
    });

    it('also waits while Claude is still reading the frames', async () => {
        answerPolls(
            jobWith(task({summary_status: 'completed', summary_markdown: 'old', visual_note: {status: 'running'}})),
            jobWith(task({summary_status: 'completed', summary_markdown: 'new', visual_note: {status: 'completed'}})),
        );
        mount(task({summary_status: 'completed', summary_markdown: 'old', visual_note: {status: 'running'}}));
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('old');
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('new');
        await advance(20000);
        expect(api.getJob).toHaveBeenCalledTimes(2);
    });

    it('shows the failure when the note could not be written, and stops asking', async () => {
        answerPolls(jobWith(task({summary_status: 'failed', summary_error: 'Claude 登录已过期'})));
        mount(task());
        await advance(5000);
        expect(screen.getByText('edit.summaryFailed')).toBeTruthy();
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
    });

    it('stops asking when the page closes', async () => {
        answerPolls(jobWith(task(), 'note'));
        const view = mount(task());
        await advance(5000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
        view.unmount();
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
    });

    it('stops asking about the old task when another task is opened', async () => {
        answerPolls(jobWith(task(), 'note'));
        mount(task());
        await advance(5000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
        await act(async () => {
            switchTask(task({task_id: 't2', summary_status: 'completed', summary_markdown: 'other note'}));
        });
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
    });

    it('keeps a note the user has typed and not yet saved', async () => {
        // The save never comes back, so the edit stays unsaved.
        api.saveSummaryEdit.mockImplementation(() => new Promise(() => {}));
        answerPolls(jobWith(task({summary_status: 'completed', summary_markdown: 'Claude note', visual_note: {status: 'completed'}})));
        mount(task({summary_status: 'completed', summary_markdown: 'old', visual_note: {status: 'running'}}));
        await advance(0);
        fireEvent.change(screen.getByLabelText('note'), {target: {value: 'my own words'}});

        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('my own words');
        // Not given up: it asks again, to take the server's copy once saved.
        await advance(5000);
        expect(api.getJob).toHaveBeenCalledTimes(2);
        expect(screen.getByLabelText('note').value).toBe('my own words');
    });
});

const claudeWriter = () => {
    runtimeConfig = {...runtimeConfig, writesItsOwnNote: true};
    credentialStatus = {visual_note_available: true};
};
const textWriter = () => {
    runtimeConfig = {...runtimeConfig, writesItsOwnNote: false};
    credentialStatus = {deepseek_api_key_configured: true};
};
const finishedTask = (extra = {}) => task({summary_status: 'completed', summary_markdown: 'old note', ...extra});

const openRegenerateDialog = async () => {
    await advance(0);
    fireEvent.click(screen.getByText('edit.regenerate'));
    return screen.getByRole('dialog');
};
const confirmRegenerate = async () => {
    await act(async () => {
        fireEvent.click(screen.getByText('edit.regenerateConfirmAction'));
    });
};

describe('重生笔记 when Claude writes the notes', () => {
    it('asks Claude to rewrite from the frames, not the text model, and shows the new note', async () => {
        claudeWriter();
        const fetchSpy = vi.fn(async () => ({ok: true, json: async () => ({})}));
        vi.stubGlobal('fetch', fetchSpy);
        // The previous run's record is still on the task when the new one starts.
        const before = finishedTask({visual_note: {status: 'completed', started_at: '2020-01-01T00:00:00+00:00'}});
        const startedAt = new Date(Date.now() + 1000).toISOString();
        answerPolls(
            jobWith(before),
            jobWith(finishedTask({visual_note: {status: 'running', started_at: startedAt}})),
            jobWith(finishedTask({summary_markdown: '# rewritten with frames', visual_note: {status: 'completed', started_at: startedAt}})),
        );
        mount(before);

        const dialog = await openRegenerateDialog();
        expect(dialog.textContent).toMatch(/Claude/);
        expect(dialog.textContent).toMatch(/画面/);
        await confirmRegenerate();

        expect(api.startJobVisualNote).toHaveBeenCalledTimes(1);
        expect(api.startJobVisualNote.mock.calls[0][0]).toBe('t1');
        expect(api.startJobVisualNote.mock.calls[0][1]).toEqual({replace_note: true});
        expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('regenerate-summary'))).toBe(false);

        // The old record is not mistaken for the new note.
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('old note');
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('old note');
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('# rewritten with frames');
        expect(screen.getByText('edit.regenDone')).toBeTruthy();
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(3);
    });

    it('says so in plain Chinese when the queue is busy', async () => {
        claudeWriter();
        api.startJobVisualNote.mockImplementation(async () => {
            const err = new Error('有任务正在处理中，等队列空下来再生成笔记，避免两件重活同时跑。');
            err.status = 409;
            throw err;
        });
        mount(finishedTask());
        await openRegenerateDialog();
        await confirmRegenerate();
        expect(screen.getByText('队列里还有任务在处理，等它处理完再重生笔记。')).toBeTruthy();
        // Nothing started, so nothing is polled and the button is usable again.
        await advance(30000);
        expect(api.getJob).not.toHaveBeenCalled();
        expect(screen.getByText('edit.regenerate')).toBeTruthy();
    });

    it('reports a failed rewrite and leaves the old note in place', async () => {
        claudeWriter();
        const startedAt = new Date(Date.now() + 1000).toISOString();
        answerPolls(jobWith(finishedTask({visual_note: {status: 'failed', started_at: startedAt, error: '剪后文件读不到了'}})));
        mount(finishedTask());
        await openRegenerateDialog();
        await confirmRegenerate();
        await advance(5000);
        expect(screen.getByText(/Claude 重写笔记失败：剪后文件读不到了/)).toBeTruthy();
        expect(screen.getByLabelText('note').value).toBe('old note');
    });
});

describe('重生笔记 when a text model writes the notes', () => {
    it('names the text model, warns that the screenshots go, and uses the text rewrite', async () => {
        textWriter();
        const fetchSpy = vi.fn(async () => ({
            ok: true,
            json: async () => ({summary_markdown: 'text-only note', summary_status: 'completed'}),
        }));
        vi.stubGlobal('fetch', fetchSpy);
        mount(finishedTask({
            summary_markdown: '![frame](/frames/1.jpg)\n\nClaude note',
            summary_written_from: 'debreath_media_note',
            visual_note: {status: 'completed'},
        }));

        const dialog = await openRegenerateDialog();
        expect(dialog.textContent).toMatch(/DeepSeek/);
        expect(dialog.textContent).not.toMatch(/Claude（/);
        expect(screen.getByTestId('regenerate-warning').textContent).toMatch(/截图不会保留/);
        await confirmRegenerate();
        await advance(0);

        expect(api.startJobVisualNote).not.toHaveBeenCalled();
        expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('regenerate-summary'))).toBe(true);
        expect(screen.getByLabelText('note').value).toBe('text-only note');
    });

    it('does not warn about screenshots when the current note was not written by Claude', async () => {
        textWriter();
        mount(finishedTask());
        await openRegenerateDialog();
        expect(screen.queryByTestId('regenerate-warning')).toBeNull();
    });
});
