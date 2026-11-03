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

const followSwitch = () => screen.getByRole('button', {name: 'edit.followPlayback'});

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
