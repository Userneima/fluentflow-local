// @vitest-environment jsdom

// The editor's address and the cut's open questions (界面优化清单 2026-10-22,
// 线二第 5、6 条和线一第 10 条). Written from what the owner asked for:
//   - The address names the open task, so a refresh, the back button or a
//     bookmark brings back the same result; a task that no longer exists says so.
//   - Opening another result moves the address with it.
//   - "有 N 处需要你判断" is visible in the editor and opens those places there;
//     ?review=cut, the records page's link, opens them straight away.
//   - The player plays the file the transcript belongs to, also when the full
//     copy of a task arrives after the page opened with a light one.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let jobs = {};
let getJobCalls = [];
let setResultFromTest = null;
let uiLang = 'zh';
// The service answering that a task's media file is not there.
let mediaMissing = false;

vi.mock('../app/AppContext.jsx', async () => {
    const React = await vi.importActual('react');
    const Ctx = React.createContext(null);
    return {AppCtx: Ctx, useApp: () => React.useContext(Ctx)};
});

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: uiLang}),
        apiFetch: (url, ...rest) => (mediaMissing && String(url).includes('/artifacts/')
            ? Promise.resolve(new Response('', {status: 404}))
            : actual.apiFetch(url, ...rest)),
        useSettings: () => ({loadSettings: () => ({}), saveSettings: () => {}}),
        // One object for the whole page, as the real useApi gives (useMemo).
        useApi: () => (globalThis.__addressTestApi ||= {
            processVideoSSE: async () => ({}),
            fetchJobArtifactFile: async () => new File(['x'], 'x'),
            downloadJobMedia: async () => {},
            recordEvent: () => {},
            getJob: async (taskId) => {
                getJobCalls.push(taskId);
                if (!jobs[taskId]) {
                    const err = new Error('Job not found');
                    err.status = 404;
                    throw err;
                }
                return jobs[taskId];
            },
            saveTranscriptEdit: async () => ({}),
            saveSummaryEdit: async () => ({}),
            getCredentialsStatus: async () => null,
        }),
    };
});

const {AppCtx} = await import('../app/AppContext.jsx');
const {default: Editor} = await import('./editor.jsx');

const Address = () => {
    const location = useLocation();
    return <output data-testid="address">{location.pathname + location.search}</output>;
};

const Harness = ({initial}) => {
    const [lastResult, setLastResult] = useState(initial);
    setResultFromTest = setLastResult;
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
    return (
        <AppCtx.Provider value={value}>
            <Editor/>
            <Address/>
        </AppCtx.Provider>
    );
};

const mount = (url, initial = null) => render(
    <MemoryRouter initialEntries={[url]}>
        <Routes><Route path="/editor" element={<Harness initial={initial}/>}/></Routes>
    </MemoryRouter>,
);

const address = () => screen.getByTestId('address').textContent;

const task = (id, extra = {}) => ({
    task_id: id,
    filename: `${id}.mp4`,
    display_title: `课程 ${id}`,
    transcript_text: 'hello there, this is long enough to be a whole transcript. '.repeat(8),
    segments: [{start: 0, end: 3, text: 'hello'}],
    summary_markdown: 'note',
    summary_status: 'completed',
    ...extra,
});

// A cut whose report asks the user about two places, one answered already.
const judgedCut = (verdicts = {a: 'agree'}) => ({
    status: 'completed',
    survey: {
        report: {
            before_seconds: 600,
            after_seconds: 420,
            sections: [
                {judge: true, title: '需要你判断', items: [
                    {key: 'a', ref: 'r-a', action: 'removed', start: 10, end: 14, seconds: 4, text: '一段停顿', doubt: '可能是在思考'},
                    {key: 'b', ref: 'r-b', action: 'removed', start: 30, end: 33, seconds: 3, text: '一句口误', doubt: '也可能是重点'},
                ]},
                {title: '剪掉的空白', items: [{key: 'c', ref: 'r-c', action: 'removed', start: 40, end: 42, seconds: 2, text: '空白'}]},
            ],
        },
        verdicts,
    },
});

beforeEach(() => {
    jobs = {};
    getJobCalls = [];
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => {};
    globalThis.fetch = vi.fn(async (url) => {
        const match = String(url).match(/\/jobs\/([^/?#]+)$/);
        if (match && jobs[match[1]]) {
            return new Response(JSON.stringify(jobs[match[1]]), {status: 200, headers: {'content-type': 'application/json'}});
        }
        const type = String(url).includes('playback_audio') ? 'audio/mpeg' : 'video/mp4';
        return new Response('x', {status: 206, headers: {'content-type': type}});
    });
});

afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
});

describe('the editor address', () => {
    it('reopens the task it names, as after a refresh', async () => {
        jobs.t1 = {result: task('t1')};
        mount('/editor?task=t1');

        expect(await screen.findByRole('heading', {name: '课程 t1'})).toBeTruthy();
        expect(screen.queryByText('edit.noResult')).toBeNull();
    });

    it('carries the open task, so the page can be refreshed or bookmarked', async () => {
        mount('/editor', task('t1'));

        await waitFor(() => expect(address()).toBe('/editor?task=t1'));
    });

    it('says the record cannot be found when the task it names is gone', async () => {
        mount('/editor?task=gone');

        expect(await screen.findByText('找不到这条记录')).toBeTruthy();
        expect(address()).toBe('/editor?task=gone');
    });

    it('opens the task it names rather than the one left open earlier', async () => {
        jobs.t2 = {result: task('t2')};
        mount('/editor?task=t2', task('t1'));

        expect(await screen.findByRole('heading', {name: '课程 t2'})).toBeTruthy();
        expect(address()).toBe('/editor?task=t2');
    });

    it('moves with the result when another one is opened, without going back to the first', async () => {
        jobs.t1 = {result: task('t1')};
        jobs.t2 = {result: task('t2')};
        mount('/editor', task('t1'));
        await waitFor(() => expect(address()).toBe('/editor?task=t1'));
        const readsOfFirst = getJobCalls.filter((id) => id === 't1').length;

        await act(async () => { setResultFromTest(task('t2')); });

        await waitFor(() => expect(address()).toBe('/editor?task=t2'));
        expect(screen.getByRole('heading', {name: '课程 t2'})).toBeTruthy();
        expect(getJobCalls.filter((id) => id === 't1').length).toBe(readsOfFirst);
    });
});

describe('cut places waiting for a judgement, in the editor', () => {
    it('says how many still wait and opens them there', async () => {
        const result = task('t1', {debreath: judgedCut()});
        jobs.t1 = {task_id: 't1', result};
        mount('/editor', result);

        const entry = await screen.findByRole('button', {name: '剪辑有 1 处需要你判断'});
        fireEvent.click(entry);

        const dialog = await screen.findByRole('dialog');
        expect(await screen.findByText('请你判断：也可能是重点')).toBeTruthy();
        expect(dialog.textContent).toContain('剪得对');
    });

    it('shows nothing when every place has been answered', async () => {
        mount('/editor', task('t1', {debreath: judgedCut({a: 'agree', b: 'agree'})}));

        await screen.findByRole('heading', {name: '课程 t1'});
        expect(screen.queryByTestId('editor-header-status')).toBeNull();
    });

    it('opens the places straight away from the records page link, once', async () => {
        const result = task('t1', {debreath: judgedCut({})});
        jobs.t1 = {task_id: 't1', result};
        mount('/editor?task=t1&review=cut');

        expect(await screen.findByRole('dialog')).toBeTruthy();
        expect(await screen.findByText('请你判断：可能是在思考')).toBeTruthy();
        // A refresh does not open it again.
        await waitFor(() => expect(address()).toBe('/editor?task=t1'));
    });
});

describe('the player', () => {
    it('moves to the cut file when the full copy of a task arrives after a light one', async () => {
        // What recent activity hands over: no record of which file the
        // transcript came from, only the saved audio.
        const light = task('t1', {artifacts: {playback_audio: {filename: 't1_audio.mp3'}}});
        const {container} = mount('/editor', light);
        await waitFor(() => expect(container.querySelector('video')?.getAttribute('src') || '').toMatch(/playback_audio$/));

        await act(async () => {
            setResultFromTest(task('t1', {
                transcript_media: 'debreath_media',
                artifacts: {
                    playback_audio: {filename: 't1_audio.mp3'},
                    debreath_media: {filename: 'debreath/t1_debreath.mp4'},
                },
            }));
        });

        await waitFor(() => expect(container.querySelector('video')?.getAttribute('src') || '').toMatch(/debreath_media$/));
    });
});

// Switching text review and video review keeps the player and the reader's
// place (界面优化线二审查，2026-10-22): rebuilding the player stopped playback
// while the page still showed it playing. Text review shows paragraphs and
// video review one sentence per line (decision 6.35), so the list itself is
// swapped; the sentence playing is brought into view in either.
describe('switching between text review and video review', () => {
    it('keeps the same player, and shows the sentence playing in either view', async () => {
        const recording = task('t1', {
            source_file_available: true,
            segments: Array.from({length: 30}, (_, i) => ({start: i * 2, end: i * 2 + 2, text: `第 ${i} 句`})),
        });
        jobs.t1 = {result: recording};
        const scrolled = [];
        Element.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this.textContent); };
        const {container} = mount('/editor', recording);
        await waitFor(() => expect(screen.getByRole('button', {name: '视频复查'}).disabled).toBe(false));
        const player = container.querySelector('video');
        // Playing sentence 20.
        await act(async () => {
            player.dispatchEvent(new Event('play'));
            player.currentTime = 41;
            player.dispatchEvent(new Event('timeupdate'));
        });

        fireEvent.click(screen.getByRole('button', {name: '视频复查'}));
        expect(container.querySelector('video')).toBe(player);
        expect(player.controls).toBe(true);
        expect(screen.getByDisplayValue('第 20 句')).toBeTruthy();
        expect(scrolled.at(-1)).toContain('00:40');

        fireEvent.click(screen.getByRole('button', {name: '文本校对'}));
        expect(container.querySelector('video')).toBe(player);
        expect(screen.getByRole('textbox', {name: '第 21 句'}).textContent).toBe('第 20 句');
        expect(scrolled.at(-1)).toBe('第 20 句');
    });
});

describe('switching while nothing plays', () => {
    it('keeps the sentence at the top of the list at the top', async () => {
        const recording = task('t1', {
            source_file_available: true,
            segments: Array.from({length: 30}, (_, i) => ({start: i * 2, end: i * 2 + 2, text: `第 ${i} 句`})),
        });
        jobs.t1 = {result: recording};
        const scrolled = [];
        Element.prototype.scrollIntoView = function scrollIntoView(options) { scrolled.push([this.textContent, options?.block]); };
        // The reader has scrolled so that sentence 12 is at the top: rows lie
        // 40 px apart, the list's top edge at 0.
        const original = Element.prototype.getBoundingClientRect;
        Element.prototype.getBoundingClientRect = function rect() {
            // A sentence in the paragraph view is its own node.
            const boxes = this.matches?.('[data-transcript-segment]') ? [this] : [];
            const match = boxes.length === 1 ? /第 (\d+) 句/.exec(boxes[0].textContent || '') : null;
            if (match) {
                const bottom = (Number(match[1]) - 12 + 1) * 40;
                return {top: bottom - 40, bottom, left: 0, right: 0, width: 0, height: 40};
            }
            return {top: 0, bottom: 400, left: 0, right: 0, width: 0, height: 400};
        };
        try {
            mount('/editor', recording);
            await waitFor(() => expect(screen.getByRole('button', {name: '视频复查'}).disabled).toBe(false));
            fireEvent.click(screen.getByRole('button', {name: '视频复查'}));
            expect(scrolled.at(-1)[0]).toContain('00:24');
            expect(scrolled.at(-1)[1]).toBe('start');
        } finally {
            Element.prototype.getBoundingClientRect = original;
        }
    });
});

// No playback audio (to-do 2026-11-26): when FluentFlow could not make the
// audio it plays and the recording is not kept either, the player says why in
// the service's words, never ffmpeg's English; with audio, nothing is said.
describe('the player without audio', () => {
    afterEach(() => { mediaMissing = false; });
    const problem = {code: 'playback_audio_failed', detail: '播放用的音频没做成：当时这台电脑的磁盘满了。转录稿和笔记不受影响。',
        error: 'av_interleaved_write_frame(): No space left on device'};

    it('says why there is no audio to play', async () => {
        jobs.t1 = {task_id: 't1', result: task('t1', {source_file_available: false, playback_audio_problem: problem})};
        mount('/editor?task=t1');

        const line = await screen.findByText(/播放用的音频没做成/);
        expect(line.textContent).toContain('磁盘满了');
        // What to do is the 选择原音频 button beside it.
        expect(screen.getByText('edit.chooseAudio')).toBeTruthy();
        expect(line.textContent).not.toMatch(/No space|av_interleaved/);
    });

    it('says it also for a result first opened from a list row, which never carries it', async () => {
        const light = task('t1', {source_file_available: false, transcript_text: 'short'});
        jobs.t1 = {task_id: 't1', result: {...light, playback_audio_problem: problem}};
        mount('/editor?task=t1', light);

        expect(await screen.findByText(/播放用的音频没做成/)).toBeTruthy();
    });

    it('says nothing of it once the audio was made after all', async () => {
        mediaMissing = true;
        jobs.t1 = {task_id: 't1', result: task('t1', {source_file_available: false, playback_audio_available: true,
            artifacts: {playback_audio: {filename: 't1_audio.mp3'}}, playback_audio_problem: problem})};
        mount('/editor?task=t1');

        expect(await screen.findByText('edit.mediaOpenFailed')).toBeTruthy();
        expect(screen.queryByText(/播放用的音频没做成/)).toBeNull();
    });

    it('says a file that would not open in its own words, not the error behind it', async () => {
        mediaMissing = true;
        jobs.t1 = {task_id: 't1', result: task('t1', {source_file_available: false,
            artifacts: {playback_audio: {filename: 't1_audio.mp3'}}})};
        mount('/editor?task=t1');
        expect(await screen.findByText('edit.mediaOpenFailed')).toBeTruthy();
        expect(screen.queryByText(/读不到|unavailable|not found/i)).toBeNull();
    });

    it('does not suggest the recording when the cut file the transcript follows is gone', async () => {
        mediaMissing = true;
        jobs.t1 = {task_id: 't1', result: task('t1', {source_file_available: true, transcript_media: 'debreath_media',
            debreath: {status: 'completed', ran_before_transcription: true, used_for_transcription: true},
            artifacts: {debreath_media: {filename: 'debreath/t1_debreath.mp4'}, playback_audio: {filename: 't1_audio.mp3'}}})};
        mount('/editor?task=t1');

        expect(await screen.findByText('edit.cutFileMissing')).toBeTruthy();
        expect(screen.queryByText('edit.mediaOpenFailed')).toBeNull();
    });

    it('gives the English page its own sentence, from the translations', async () => {
        uiLang = 'en';
        try {
            jobs.t1 = {task_id: 't1', result: task('t1', {source_file_available: false, playback_audio_problem: problem})};
            mount('/editor?task=t1');
            expect(await screen.findByText('edit.playbackAudioFailed')).toBeTruthy();
        } finally {
            uiLang = 'zh';
        }
    });

    it('says nothing of it when nothing failed', async () => {
        jobs.t1 = {task_id: 't1', result: task('t1', {source_file_available: false})};
        mount('/editor?task=t1');

        expect(await screen.findByText('edit.audioUnavailable')).toBeTruthy();
        expect(screen.queryByText(/播放用的音频没做成/)).toBeNull();
    });
});
