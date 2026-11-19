// @vitest-environment jsdom

// Watching a recording in the editor (王愉超, 2026-11-03, while watching the
// third lesson). Written from what he asked for:
//   - The speed menu stays open while the playing sentence changes, so a
//     speed can be picked during playback. The browser's own player closes
//     its menus whenever anything on the page scrolls, and the transcript
//     scrolls on every new sentence, so the speed is picked from the page's
//     own menu and the player's built-in speed item is turned off.
//   - Scrolling the transcript by hand stops it following the playback, so
//     he can read another part while listening; the follow switch shows
//     that it is off, and turning it back on brings the playing sentence
//     into view at once, like the note's follow switch (decision 6.37).
//     Clicking a sentence's time also follows again, as before.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let jobs = {};
let getJobCalls = [];

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

const Harness = ({initial}) => {
    const [lastResult, setLastResult] = useState(initial);
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
        </AppCtx.Provider>
    );
};

const mount = (url, initial = null) => render(
    <MemoryRouter initialEntries={[url]}>
        <Routes><Route path="/editor" element={<Harness initial={initial}/>}/></Routes>
    </MemoryRouter>,
);

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

beforeEach(() => {
    // The page has the window's focus, as when he works in it.
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
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


const recording = () => task('t1', {
    source_file_available: true,
    segments: Array.from({length: 30}, (_, i) => ({start: i * 2, end: i * 2 + 2, text: `第 ${i} 句`})),
});

const playAt = async (player, seconds) => {
    await act(async () => {
        player.currentTime = seconds;
        player.dispatchEvent(new Event('timeupdate'));
    });
};

const openVideoReview = async () => {
    jobs.t1 = {result: recording()};
    const scrolled = [];
    Element.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this.textContent); };
    const view = mount('/editor', recording());
    await waitFor(() => expect(screen.getByRole('button', {name: '视频复查'}).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', {name: '视频复查'}));
    const player = view.container.querySelector('video');
    await act(async () => { player.dispatchEvent(new Event('play')); });
    return {...view, player, scrolled};
};

// Named by what it does now: 转录稿跟随 while following, 回到播放处 once stopped.
const followSwitch = () => screen.getByTestId('transcript-follow-switch');

describe('the playback speed', () => {
    it('stays open while the sentence playing changes, and sets the speed picked', async () => {
        const {player} = await openVideoReview();
        expect(player.getAttribute('controlslist') || '').toContain('noplaybackrate');

        fireEvent.click(screen.getByRole('button', {name: '播放速度'}));
        expect(screen.getByRole('menu')).toBeTruthy();
        await playAt(player, 5);
        await playAt(player, 9);
        expect(screen.getByRole('menu')).toBeTruthy();

        fireEvent.click(screen.getByRole('menuitem', {name: /1\.5×/}));
        expect(player.playbackRate).toBe(1.5);
        expect(screen.queryByRole('menu')).toBeNull();
        expect(screen.getByRole('button', {name: '播放速度'}).textContent).toContain('1.5×');
    });

    it('keeps the speed when switching to text review', async () => {
        const {player} = await openVideoReview();
        fireEvent.click(screen.getByRole('button', {name: '播放速度'}));
        fireEvent.click(screen.getByRole('menuitem', {name: /1\.25×/}));
        fireEvent.click(screen.getByRole('button', {name: '文本校对'}));
        expect(player.playbackRate).toBe(1.25);
        expect(screen.getByRole('button', {name: '播放速度'}).textContent).toContain('1.25×');
    });
});

describe('the transcript following the playback', () => {
    it('brings each new sentence into view while following', async () => {
        const {player, scrolled} = await openVideoReview();
        await playAt(player, 41);
        expect(scrolled.at(-1)).toContain('00:40');
        expect(followSwitch().getAttribute('aria-pressed')).toBe('true');
    });

    it('stops following once the reader scrolls it, and the switch shows it is off', async () => {
        const {container, player, scrolled} = await openVideoReview();
        await playAt(player, 41);
        const list = container.querySelector('[data-testid="transcript-scroll"]');
        fireEvent.wheel(list, {deltaY: -400});
        const before = scrolled.length;
        await playAt(player, 45);
        await playAt(player, 49);
        expect(scrolled.length).toBe(before);
        expect(followSwitch().getAttribute('aria-pressed')).toBe('false');
        expect(followSwitch().textContent).toContain('edit.backToPlayback');
    });

    it('also stops following on a touch scroll', async () => {
        const {container, player, scrolled} = await openVideoReview();
        fireEvent.touchMove(container.querySelector('[data-testid="transcript-scroll"]'));
        const before = scrolled.length;
        await playAt(player, 45);
        expect(scrolled.length).toBe(before);
    });

    it('goes back to the sentence playing as soon as the switch is turned on', async () => {
        const {container, player, scrolled} = await openVideoReview();
        fireEvent.wheel(container.querySelector('[data-testid="transcript-scroll"]'), {deltaY: 400});
        await playAt(player, 45);
        fireEvent.click(followSwitch());
        expect(followSwitch().getAttribute('aria-pressed')).toBe('true');
        expect(scrolled.at(-1)).toContain('00:44');
        await playAt(player, 49);
        expect(scrolled.at(-1)).toContain('00:48');
    });

    it('goes back even while paused', async () => {
        const {container, player, scrolled} = await openVideoReview();
        fireEvent.wheel(container.querySelector('[data-testid="transcript-scroll"]'), {deltaY: 400});
        await playAt(player, 45);
        await act(async () => { player.dispatchEvent(new Event('pause')); });
        fireEvent.click(followSwitch());
        expect(scrolled.at(-1)).toContain('00:44');
    });

    it('follows again after a sentence time is clicked', async () => {
        const {container, player, scrolled} = await openVideoReview();
        fireEvent.wheel(container.querySelector('[data-testid="transcript-scroll"]'), {deltaY: 400});
        fireEvent.click(screen.getByRole('button', {name: /00:20/}));
        expect(followSwitch().getAttribute('aria-pressed')).toBe('true');
        await playAt(player, 23);
        expect(scrolled.at(-1)).toContain('00:22');
    });
});

// The note follows the playback in every editor (decision 6.56): opening a
// record with no extra address reads the note's section times.
describe('the note following the playback', () => {
    it('reads the section times without anything added to the address', async () => {
        jobs.t1 = {result: task('t1', {summary_markdown: '## 一、开场\n\n内容'})};
        mount('/editor?task=t1');
        await waitFor(() => expect(globalThis.fetch.mock.calls.some(([url]) => String(url).includes('/jobs/t1/note/section-times'))).toBe(true));
    });
});

// Fixing words while watching (王愉超, 2026-11-19: "修改了转录的原文之后转录稿跟随
//会断掉"). Written from what he chose (evaluation 2026-11-19, actions 1, 2, 5):
//   - Putting the caret in a sentence stops the transcript moving, so the
//     sentence being typed in stays where it is.
//   - Leaving the sentence (clicking anywhere that is not another sentence)
//     follows again by itself, if it was following before; going straight to
//     another sentence keeps it still, and scrolling away by hand while
//     editing means the reader wants to stay there, so leaving does not pull
//     the transcript back.
//   - With the caret in a sentence, Esc plays or pauses and Shift+Esc goes
//     back 3 seconds and plays, so the hand stays on the keyboard; Esc that
//     ends a Chinese input method's composition does neither.
const openTextReview = async () => {
    jobs.t1 = {result: recording()};
    const scrolled = [];
    Element.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this.textContent); };
    const view = mount('/editor', recording());
    await waitFor(() => expect(screen.getByRole('button', {name: '视频复查'}).disabled).toBe(false));
    const player = view.container.querySelector('video');
    await act(async () => { player.dispatchEvent(new Event('play')); });
    return {...view, player, scrolled};
};
const sentence = (n) => screen.getByRole('textbox', {name: `第 ${n + 1} 句`});

describe('following again after fixing a sentence', () => {
    it('stays still while the caret is in a sentence and follows again once it leaves', async () => {
        const {player, scrolled} = await openTextReview();
        await playAt(player, 41);
        fireEvent.focus(sentence(20));
        const before = scrolled.length;
        await playAt(player, 45);
        expect(scrolled.length).toBe(before);
        expect(followSwitch().getAttribute('aria-pressed')).toBe('false');

        fireEvent.blur(sentence(20), {relatedTarget: null});
        expect(followSwitch().getAttribute('aria-pressed')).toBe('true');
        await playAt(player, 49);
        expect(scrolled.at(-1)).toBe('第 24 句');
    });

    it('stays still when the caret goes straight to another sentence', async () => {
        const {player, scrolled} = await openTextReview();
        fireEvent.focus(sentence(20));
        fireEvent.blur(sentence(20), {relatedTarget: sentence(21)});
        fireEvent.focus(sentence(21));
        const before = scrolled.length;
        await playAt(player, 49);
        expect(scrolled.length).toBe(before);
        expect(followSwitch().getAttribute('aria-pressed')).toBe('false');
    });

    it('does not pull the reader back after they scrolled away while editing', async () => {
        const {container, player, scrolled} = await openTextReview();
        fireEvent.focus(sentence(20));
        fireEvent.wheel(container.querySelector('[data-testid="transcript-scroll"]'), {deltaY: 400});
        fireEvent.blur(sentence(20), {relatedTarget: null});
        const before = scrolled.length;
        await playAt(player, 49);
        expect(scrolled.length).toBe(before);
        expect(followSwitch().getAttribute('aria-pressed')).toBe('false');
    });

    it('keeps still when the caret leaves only because another app was opened', async () => {
        const {player, scrolled} = await openTextReview();
        fireEvent.focus(sentence(20));
        document.hasFocus.mockReturnValue(false);
        fireEvent.blur(sentence(20), {relatedTarget: null});
        const before = scrolled.length;
        await playAt(player, 49);
        expect(scrolled.length).toBe(before);
        expect(followSwitch().getAttribute('aria-pressed')).toBe('false');
    });

    it('does not start following when it was switched off before editing', async () => {
        const {player, scrolled} = await openTextReview();
        fireEvent.click(followSwitch());
        expect(followSwitch().getAttribute('aria-pressed')).toBe('false');
        fireEvent.focus(sentence(20));
        fireEvent.blur(sentence(20), {relatedTarget: null});
        const before = scrolled.length;
        await playAt(player, 49);
        expect(scrolled.length).toBe(before);
    });

    it('follows again after leaving a line in video review too', async () => {
        const {container, player, scrolled} = await openVideoReview();
        const line = container.querySelectorAll('textarea[data-transcript-segment="true"]')[20];
        fireEvent.focus(line);
        expect(followSwitch().getAttribute('aria-pressed')).toBe('false');
        fireEvent.blur(line, {relatedTarget: null});
        await playAt(player, 49);
        expect(followSwitch().getAttribute('aria-pressed')).toBe('true');
        expect(scrolled.at(-1)).toContain('00:48');
    });
});

describe('the keyboard while the caret is in a sentence', () => {
    const controls = (player, paused) => {
        Object.defineProperty(player, 'paused', {value: paused, configurable: true});
        player.play = vi.fn(() => Promise.resolve());
        player.pause = vi.fn();
    };

    it('plays and pauses with Esc', async () => {
        const {player} = await openTextReview();
        controls(player, false);
        fireEvent.keyDown(sentence(5), {key: 'Escape'});
        expect(player.pause).toHaveBeenCalledTimes(1);
        controls(player, true);
        fireEvent.keyDown(sentence(5), {key: 'Escape'});
        expect(player.play).toHaveBeenCalledTimes(1);
    });

    it('goes back 3 seconds and plays with Shift+Esc', async () => {
        const {player} = await openTextReview();
        await playAt(player, 20);
        controls(player, true);
        fireEvent.keyDown(sentence(5), {key: 'Escape', shiftKey: true});
        expect(player.currentTime).toBe(17);
        expect(player.play).toHaveBeenCalledTimes(1);
    });

    it('leaves Esc to the input method while it is composing', async () => {
        const {player} = await openTextReview();
        controls(player, false);
        fireEvent.keyDown(sentence(5), {key: 'Escape', isComposing: true});
        expect(player.pause).not.toHaveBeenCalled();
    });

    it('works in the lines of video review too', async () => {
        const {container, player} = await openVideoReview();
        controls(player, false);
        const line = container.querySelectorAll('textarea[data-transcript-segment="true"]')[3];
        fireEvent.keyDown(line, {key: 'Escape'});
        expect(player.pause).toHaveBeenCalledTimes(1);
    });
});
