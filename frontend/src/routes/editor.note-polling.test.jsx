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
//   - Whether Claude can rewrite from the frames is decided per task. A task
//     whose frames are out of reach (subtitle file, no cut file, source gone)
//     is rewritten by the text model with the chosen prompt, and the dialog
//     says why. A task that has them still offers the text model as a second
//     choice, so the chosen prompt can actually be used. When the service
//     cannot say, both ways are offered.

import {createContext, useContext, useState} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const AppState = createContext(null);
let runtimeConfig = {};
let credentialStatus = {};
let switchTask = null;
let storedSettings = {};

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
    getVisualNoteAvailability: vi.fn(async () => ({available: true, reason: null})),
    getCredentialsStatus: async () => credentialStatus,
};

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => storedSettings, saveSettings: () => {}}),
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
    storedSettings = {};
    api.getVisualNoteAvailability.mockReset();
    api.getVisualNoteAvailability.mockImplementation(async () => ({available: true, reason: null}));
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
        expect(screen.getByText('正在写笔记，写好会自动出现。')).toBeTruthy();

        await advance(4900);
        expect(api.getJob).not.toHaveBeenCalled();
        await advance(100);
        expect(api.getJob).toHaveBeenCalledTimes(1);
        expect(screen.getByText('正在写笔记，写好会自动出现。')).toBeTruthy();

        await advance(5000);
        expect(api.getJob).toHaveBeenCalledTimes(2);
        expect(screen.getByLabelText('note').value).toBe('# Claude note');
        expect(screen.queryByText('正在写笔记，写好会自动出现。')).toBeNull();
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

describe('重生笔记 decided per task when Claude writes the notes', () => {
    // Claude writes the notes, and a DeepSeek key is there for the text model.
    const claudeWithTextKey = () => {
        runtimeConfig = {...runtimeConfig, writesItsOwnNote: true};
        credentialStatus = {visual_note_available: true, deepseek_api_key_configured: true};
        storedSettings = {aiProvider: 'deepseek', promptPreset: 'custom', customPromptText: '只列三条要点'};
    };
    const textRewriteCall = (fetchSpy) => fetchSpy.mock.calls.find(([url]) => String(url).includes('regenerate-summary'));
    const stubTextRewrite = () => {
        const fetchSpy = vi.fn(async () => ({
            ok: true,
            json: async () => ({summary_markdown: 'text-only note', summary_status: 'completed'}),
        }));
        vi.stubGlobal('fetch', fetchSpy);
        return fetchSpy;
    };

    it('rewrites a task without frames with the text model and the chosen prompt, and says why', async () => {
        claudeWithTextKey();
        api.getVisualNoteAvailability.mockImplementation(async () => ({available: false, reason: '这是字幕文件，没有剪后视频。'}));
        const fetchSpy = stubTextRewrite();
        mount(finishedTask());

        const dialog = await openRegenerateDialog();
        expect(dialog.textContent).toMatch(/这是字幕文件，没有剪后视频/);
        expect(dialog.textContent).toMatch(/DeepSeek/);
        expect(dialog.textContent).toMatch(/提示词/);
        // Only one way is possible here, so no second choice is offered.
        expect(screen.queryByTestId('regenerate-alternative')).toBeNull();
        await confirmRegenerate();
        await advance(0);

        expect(api.startJobVisualNote).not.toHaveBeenCalled();
        const call = textRewriteCall(fetchSpy);
        expect(call).toBeTruthy();
        expect(call[1].body.get('system_prompt')).toBe('只列三条要点');
        expect(call[1].body.get('prompt_preset')).toBe('custom');
        expect(screen.getByLabelText('note').value).toBe('text-only note');
    });

    it('offers the text model as a second choice when the frames are there, and that choice uses the prompt', async () => {
        claudeWithTextKey();
        const fetchSpy = stubTextRewrite();
        mount(finishedTask());

        await openRegenerateDialog();
        const choice = screen.getByTestId('regenerate-alternative');
        expect(choice.textContent).toBe('改用文本模型按文字重写（会用你选的提示词，截图不保留）');
        await act(async () => { fireEvent.click(choice); });
        await advance(0);

        expect(api.startJobVisualNote).not.toHaveBeenCalled();
        expect(textRewriteCall(fetchSpy)[1].body.get('system_prompt')).toBe('只列三条要点');
    });

    it('still lets Claude rewrite from the frames when the task has them', async () => {
        claudeWithTextKey();
        const fetchSpy = stubTextRewrite();
        mount(finishedTask());
        await openRegenerateDialog();
        await confirmRegenerate();
        expect(api.startJobVisualNote).toHaveBeenCalledTimes(1);
        expect(textRewriteCall(fetchSpy)).toBeUndefined();
    });

    it('offers both ways when the service cannot say whether the frames are there', async () => {
        claudeWithTextKey();
        api.getVisualNoteAvailability.mockImplementation(async () => ({available: null, reason: null}));
        mount(finishedTask());
        const dialog = await openRegenerateDialog();
        expect(dialog.textContent).toMatch(/画面/);
        expect(screen.getByTestId('regenerate-alternative').disabled).toBe(false);
    });

    it('says the text model cannot be used when no text-model key is filled in', async () => {
        claudeWriter();
        mount(finishedTask());
        const dialog = await openRegenerateDialog();
        expect(screen.getByTestId('regenerate-alternative').disabled).toBe(true);
        expect(dialog.textContent).toMatch(/还没有填文本模型的 Key/);
    });
});

// What the person should get:
//   - While the note is being written the page says so in plain words; it
//     promises "几分钟" only when Claude is reading the frames, and names the
//     text model when that is who writes.
//   - A note the text model wrote because the frame note could not run says
//     so above the note, with the reason.
//   - With automatic Feishu export on, the link or the failure shows up on the
//     page without reopening the task, even though the export lands after the
//     note. The page stops asking once it has the answer, or after ten minutes.
describe('what the note panel says', () => {
    it('promises minutes only when Claude writes from the frames', async () => {
        claudeWriter();
        answerPolls(jobWith(task(), 'note'));
        mount(task());
        await advance(0);
        expect(screen.getByText('正在写笔记。Claude 结合画面写一般要几分钟，写好会自动出现。')).toBeTruthy();
    });

    it('names the text model and makes no time promise when it writes', async () => {
        textWriter();
        storedSettings = {aiProvider: 'deepseek'};
        answerPolls(jobWith(task(), 'note'));
        mount(task());
        await advance(0);
        const line = screen.getByText(/^正在写笔记，由 DeepSeek/);
        expect(line.textContent).not.toMatch(/几分钟/);
    });

    it('says a skipped note was skipped on request, and calls it 笔记', async () => {
        answerPolls(jobWith(task({summary_status: 'skipped', summary_skipped: true})));
        mount(task({summary_status: 'skipped', summary_skipped: true}));
        await advance(0);
        expect(screen.getByText('edit.summarySkipped')).toBeTruthy();
    });

    it('says when the text model wrote the note instead, and why', async () => {
        const fallback = task({
            summary_status: 'completed',
            summary_markdown: '# text note',
            note_written_by: 'text_fallback',
            summary_written_from: 'text_fallback',
            note_fallback_reason: '本机 Claude 的登录已过期。',
        });
        answerPolls(jobWith(fallback));
        mount(fallback);
        await advance(0);
        expect(screen.getByTestId('note-fallback').textContent)
            .toBe('这次改由文本模型按文字写的笔记（没有截图）。原因：本机 Claude 的登录已过期。');
    });

    it('says nothing of the kind for a note Claude wrote', async () => {
        const claude = task({summary_status: 'completed', summary_markdown: '# note', summary_written_from: 'debreath_media_note'});
        answerPolls(jobWith(claude));
        mount(claude);
        await advance(0);
        expect(screen.queryByTestId('note-fallback')).toBeNull();
    });

    it('shows the fallback notice once a polled note turns out to be one', async () => {
        answerPolls(jobWith(task({
            summary_status: 'completed',
            summary_markdown: '# text note',
            note_written_by: 'text_fallback',
            note_fallback_reason: '结合画面的笔记现在写不了。',
        })));
        mount(task());
        await advance(5000);
        expect(screen.getByTestId('note-fallback').textContent).toMatch(/原因：结合画面的笔记现在写不了。/);
    });
});

const autoExportJob = (result) => ({
    ...jobWith(result),
    metadata: {queue_options: {export_to_lark: 'true'}},
});

describe('a task with automatic Feishu export', () => {
    it('keeps asking after the note until the document link is in', async () => {
        const noteDone = task({summary_status: 'completed', summary_markdown: '# note'});
        answerPolls(
            autoExportJob(noteDone),
            autoExportJob(noteDone),
            autoExportJob({...noteDone, lark_response: {url: 'https://feishu.example/doc'}, lark_doc_title: 'lecture'}),
        );
        mount(task());
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('# note');
        expect(screen.queryByTestId('lark-export-link')).toBeNull();
        await advance(10000);
        expect(screen.getByTestId('lark-export-link').getAttribute('href')).toBe('https://feishu.example/doc');
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(3);
    });

    it('shows the export failure without reopening the task', async () => {
        const noteDone = task({summary_status: 'completed', summary_markdown: '# note'});
        answerPolls(
            autoExportJob(noteDone),
            autoExportJob({...noteDone, lark_error: '飞书没有登录'}),
        );
        mount(task());
        await advance(10000);
        expect(screen.getByText(/edit.autoExportFailed：飞书没有登录/)).toBeTruthy();
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(2);
    });

    it('does not replace a note typed while it waits for the export', async () => {
        const noteDone = task({summary_status: 'completed', summary_markdown: '# note'});
        api.saveSummaryEdit.mockImplementation(() => new Promise(() => {}));
        answerPolls(
            autoExportJob(noteDone),
            autoExportJob({...noteDone, summary_markdown: '# server copy', lark_response: {url: 'https://feishu.example/doc'}}),
        );
        mount(task());
        await advance(5000);
        fireEvent.change(screen.getByLabelText('note'), {target: {value: 'my edit'}});
        await advance(5000);
        expect(screen.getByLabelText('note').value).toBe('my edit');
        expect(screen.getByTestId('lark-export-link')).toBeTruthy();
    });

    it('gives up after ten minutes', async () => {
        const noteDone = task({summary_status: 'completed', summary_markdown: '# note'});
        answerPolls(autoExportJob(noteDone));
        mount(task());
        await advance(5000);
        await advance(11 * 60 * 1000);
        const calls = api.getJob.mock.calls.length;
        await advance(60000);
        expect(api.getJob.mock.calls.length).toBe(calls);
    });

    it('stops with the note when export was not asked for', async () => {
        answerPolls(jobWith(task({summary_status: 'completed', summary_markdown: '# note'})));
        mount(task());
        await advance(5000);
        await advance(30000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
    });
});
