// @vitest-environment jsdom

// The editor, mounted, for a task that cut itself before transcribing: it plays
// the cut file, and the header's status speaks up only when that file cannot
// be read, with the way to process it again (owner, 2026-10-22; the slim
// header, 界面优化清单线二第 8 条). Asserted against the real page.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
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
        expect(screen.queryByTestId('editor-header-status')).toBeNull();
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

    it('says the cut file is unreadable rather than letting the recording stand in', async () => {
        lastResult = cutResult({artifacts: {}});
        mount();

        const status = await screen.findByTestId('editor-header-status');
        expect(status.textContent).toMatch(/剪后文件读不到了/);
        expect(status.textContent).toMatch(/重新处理/);
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
        expect(screen.queryByTestId('editor-header-status')).toBeNull();
    });

    it('offers to process the recording again when the cut file is really gone', async () => {
        lastResult = cutResult();
        globalThis.fetch = vi.fn(async () => new Response('gone', {status: 404}));
        mount();

        const status = await screen.findByTestId('editor-header-status');
        await waitFor(() => expect(status.textContent).toMatch(/剪后文件读不到了，重新处理/));
        fireEvent.click(status);
        expect(await screen.findByRole('dialog')).toBeTruthy();
    });

    it('stays out of the way of a task that never went through the automatic flow', async () => {
        lastResult = cutResult({transcript_media: 'source', debreath: undefined, artifacts: {}});
        mount();

        await screen.findByTestId('editor-header');
        expect(screen.queryByTestId('editor-header-status')).toBeNull();
    });
});
