// @vitest-environment jsdom

// The editor, mounted, for a task that cut itself before transcribing: it plays
// the cut file, offers it from the transcript's export menu, and speaks up only
// when that file cannot be read (owner, 2026-10-22). Asserted against the real
// page, since a component test proves a bar works, not that anyone sees it.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let lastResult = null;
let artifactFetch = null;
let credentialStatus = null;

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        lastResult,
        setLastResult: (next) => { lastResult = next; },
        lastSourceFile: null,
        setLastSourceFile: () => {},
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        addLarkExport: () => {},
        runtimeConfig,
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({}), saveSettings: () => {}}),
        useApi: () => ({
            processVideoSSE: async () => ({}),
            fetchJobSourceFile: async () => { throw new Error('no source'); },
            fetchJobArtifactFile: (...args) => artifactFetch(...args),
            downloadJobMedia: actual.downloadJobMedia,
            recordEvent: () => {},
            getJob: async () => ({}),
            saveTranscriptEdit: async () => ({}),
            saveSummaryEdit: async () => ({}),
            getCredentialsStatus: async () => credentialStatus,
        }),
    };
});

const {default: Editor} = await import('./editor.jsx');

// A task that cut itself before anything read it: the media, the subtitles and
// the note are all the shortened file.
const cutResult = (extra = {}) => ({
    task_id: 't1',
    filename: 'lecture.mp4',
    transcript_media: 'debreath_media',
    transcript_text: 'hello',
    segments: [{start: 0, end: 3, text: 'hello'}],
    summary_markdown: 'note',
    source_file_available: true,
    debreath: {
        status: 'completed',
        ran_before_transcription: true,
        used_for_transcription: true,
        rendered: true,
        render_verified: true,
        plan: {cut_count: 4, removed_seconds: 6.4, removed_percent: 46, source_duration_seconds: 13.9, kept_seconds: 7.5},
    },
    artifacts: {debreath_media: {filename: 'debreath/lecture_debreath.mp4'}},
    ...extra,
});

const mount = () => render(<MemoryRouter><Editor/></MemoryRouter>);

describe('the editor for a task that cut itself', () => {
    beforeEach(() => {
        lastResult = null;
        artifactFetch = async (taskId, kind, filename) => new File(['x'], filename || kind, {type: 'video/mp4'});
        URL.createObjectURL = () => 'blob:cut';
        // The player reads media straight from the service; one byte is asked for
        // first to see the file is there.
        globalThis.fetch = vi.fn(async () => new Response('x', {status: 206, headers: {'content-type': 'video/mp4'}}));
        URL.revokeObjectURL = () => {};
        // jsdom treats the download anchor's click as a navigation and warns; the
        // handing-over itself is what this file checks, not the browser's part of it.
        vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
    });

    it('does not announce the cut while the cut file plays', async () => {
        lastResult = cutResult();
        const {container} = mount();

        await waitFor(() => expect(container.querySelector('video, audio')?.getAttribute('src')).toBeTruthy());
        expect(screen.queryByTestId('cut-flow-bar')).toBeNull();
    });

    // 2026-10-14: the player streams from the service instead of fetching the
    // whole recording into the browser before the first second plays.
    it('plays the cut file from its address, without fetching it whole', async () => {
        lastResult = cutResult();
        const asked = [];
        artifactFetch = async (...args) => { asked.push(args); return new File(['x'], 'x'); };
        const {container} = mount();

        await waitFor(() => {
            const media = container.querySelector('video, audio');
            expect(media?.getAttribute('src') || '').toMatch(/\/jobs\/t1\/artifacts\/debreath_media$/);
        });
        expect(asked).toEqual([]);
    });

    it('hands over the cut video from the export menu, streamed from its address under its own name', async () => {
        lastResult = cutResult();
        const fetchedWhole = [];
        artifactFetch = async (...args) => { fetchedWhole.push(args); return new File(['x'], 'x'); };
        const handed = [];
        HTMLAnchorElement.prototype.click.mockImplementation(function () {
            handed.push({href: this.getAttribute('href'), download: this.download});
        });
        mount();

        fireEvent.click(await screen.findByRole('button', {name: '导出'}));
        const menu = await screen.findByRole('menu');
        fireEvent.click(within(menu).getAllByRole('menuitem').find((item) => item.textContent.includes('剪后视频（与字幕对齐）')));
        await waitFor(() => expect(handed.length).toBe(1));
        expect(handed[0].href).toMatch(/\/jobs\/t1\/artifacts\/debreath_media$/);
        // Handed over under a filename, not under the path it is stored at.
        expect(handed[0].download).toBe('lecture_debreath.mp4');
        // The browser saves it from the address; the page never holds the file.
        expect(fetchedWhole).toEqual([]);
    });

    it('says the cut file is unreadable rather than letting the recording stand in', async () => {
        lastResult = cutResult({artifacts: {}});
        mount();

        const bar = await screen.findByTestId('cut-flow-bar');
        expect(bar.textContent).toMatch(/剪后文件读不到了/);
        expect(screen.getByRole('button', {name: /重新处理/})).toBeTruthy();
    });

    // 2026-10-22: one dropped request on load used to mark a present cut file as
    // unreadable until the page was reloaded.
    it('plays a cut file whose first read got no answer, without calling it unreadable', async () => {
        lastResult = cutResult();
        let calls = 0;
        globalThis.fetch = vi.fn(async () => {
            calls += 1;
            if (calls === 1) throw new TypeError('Failed to fetch');
            return new Response('x', {status: 206, headers: {'content-type': 'video/mp4'}});
        });
        const {container} = mount();

        await waitFor(() => {
            const media = container.querySelector('video, audio');
            expect(media?.getAttribute('src') || '').toMatch(/\/jobs\/t1\/artifacts\/debreath_media$/);
        }, {timeout: 3000});
        expect(screen.queryByTestId('cut-flow-bar')).toBeNull();
    });

    it('offers to process the recording again when the cut file is really gone', async () => {
        lastResult = cutResult();
        globalThis.fetch = vi.fn(async () => new Response('gone', {status: 404}));
        mount();

        const button = await screen.findByRole('button', {name: /重新处理/});
        expect(screen.getByTestId('cut-flow-bar').textContent).toMatch(/剪后文件读不到了/);
        fireEvent.click(button);
        expect(await screen.findByRole('dialog')).toBeTruthy();
    });

    it('stays out of the way of a task that never went through the automatic flow', async () => {
        lastResult = cutResult({transcript_media: 'source', debreath: undefined, artifacts: {}});
        mount();

        await screen.findByText('edit.regenerate');
        expect(screen.queryByTestId('cut-flow-bar')).toBeNull();
    });
});

// Requirement: subtitles made from the cut file line up with the cut video only.
// Whoever downloads them must be able to tell, from the menu and from the file
// name, which video they belong to, and must be offered that video.
describe('the transcript download menu for a task transcribed from the cut file', () => {
    let saved;
    beforeEach(() => {
        lastResult = null;
        credentialStatus = null;
        saved = [];
        artifactFetch = async (taskId, kind, filename) => new File(['x'], filename || kind, {type: 'video/mp4'});
        URL.createObjectURL = () => 'blob:menu';
        URL.revokeObjectURL = () => {};
        vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
            saved.push(this.download);
        });
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
    });

    const openTranscriptMenu = async () => {
        const trigger = await screen.findByRole('button', {name: '导出'});
        fireEvent.click(trigger);
        return screen.findByRole('menu');
    };

    it('labels the subtitles as the cut version and offers the cut video next to them', async () => {
        lastResult = cutResult();
        mount();
        const menu = await openTranscriptMenu();
        const labels = within(menu).getAllByRole('menuitem').map((item) => item.textContent);
        expect(labels.some((text) => text.includes('dl.srt') && text.includes('对应剪后视频'))).toBe(true);
        expect(labels.some((text) => text.includes('dl.vtt') && text.includes('对应剪后视频'))).toBe(true);
        expect(labels.some((text) => text.includes('剪后视频（与字幕对齐）'))).toBe(true);
        expect(labels.some((text) => text.includes('原视频') && text.includes('对不上'))).toBe(true);
    });

    it('names the subtitle file after the cut version', async () => {
        lastResult = cutResult();
        mount();
        const menu = await openTranscriptMenu();
        const srt = within(menu).getAllByRole('menuitem').find((item) => item.textContent.includes('dl.srt'));
        fireEvent.click(srt);
        await waitFor(() => expect(saved.length).toBe(1));
        expect(saved[0]).toBe('lecture_剪后版.srt');
    });

    it('hands over the cut video from the menu', async () => {
        lastResult = cutResult();
        mount();
        const menu = await openTranscriptMenu();
        fireEvent.click(within(menu).getAllByRole('menuitem').find((item) => item.textContent.startsWith('剪后视频')));
        // The player fetches the same file for itself; what matters is the file
        // handed to the browser as a download.
        await waitFor(() => expect(saved).toEqual(['lecture_debreath.mp4']));
    });

    it('leaves the menu as it was for a transcript of the original recording', async () => {
        lastResult = cutResult({transcript_media: 'source', debreath: undefined, artifacts: {}});
        mount();
        const menu = await openTranscriptMenu();
        const labels = within(menu).getAllByRole('menuitem').map((item) => item.textContent);
        expect(labels.join('|')).not.toMatch(/剪后/);
        expect(labels.some((text) => text.startsWith('dl.sourceVideo'))).toBe(true);
        fireEvent.click(within(menu).getAllByRole('menuitem').find((item) => item.textContent.includes('dl.srt')));
        await waitFor(() => expect(saved.length).toBe(1));
        expect(saved[0]).toBe('lecture.srt');
    });
});

// Requirement: changing the prompt does nothing to a note Claude writes from the
// frames, so the prompt dialog says so while Claude is the writer.
describe('the prompt dialog while Claude writes the note', () => {
    beforeEach(() => {
        lastResult = cutResult({transcript_media: 'source', debreath: undefined, artifacts: {}});
        artifactFetch = async (taskId, kind, filename) => new File(['x'], filename || kind, {type: 'video/mp4'});
    });

    afterEach(() => {
        runtimeConfig.writesItsOwnNote = undefined;
        credentialStatus = null;
        vi.restoreAllMocks();
        cleanup();
    });

    it('points to the user\'s agent to rewrite with the prompt', async () => {
        runtimeConfig.writesItsOwnNote = true;
        credentialStatus = {visual_note_available: true};
        mount();
        // The prompt lives in the header's "more" menu.
        fireEvent.click(await screen.findByRole('button', {name: '更多操作'}));
        fireEvent.click(await screen.findByRole('menuitem', {name: /prompt\.collapsed/}));
        const note = await screen.findByTestId('prompt-scope-note');
        await waitFor(() => expect(note.textContent).toMatch(/Claude 结合画面写笔记/));
        expect(note.textContent).toMatch(/交给你的 agent 按提示词重写/);
    });

    it('adds nothing when a text model writes the note', async () => {
        credentialStatus = {deepseek_api_key_configured: true};
        mount();
        fireEvent.click(await screen.findByRole('button', {name: '更多操作'}));
        fireEvent.click(await screen.findByRole('menuitem', {name: /prompt\.collapsed/}));
        await screen.findByRole('dialog');
        expect(screen.queryByTestId('prompt-scope-note')).toBeNull();
    });
});
