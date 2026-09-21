// @vitest-environment jsdom

// The editor, mounted, looking for the one line that says what the upload
// already did to this recording.
//
// CutFlowBar had its own tests and no page rendered it, which is the failure this
// file exists to make loud: a component test proves the bar works, not that the
// reader ever sees it. So this one asserts against the real page.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const runtimeConfig = {publicMode: false, allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let lastResult = null;
let artifactFetch = null;

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
            uploadJobPlaybackAudio: async () => ({}),
            recordEvent: () => {},
            getJob: async () => ({}),
            saveTranscriptEdit: async () => ({}),
            saveSummaryEdit: async () => ({}),
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

describe('the editor shows the cut-flow record', () => {
    beforeEach(() => {
        lastResult = null;
        artifactFetch = async (taskId, kind, filename) => new File(['x'], filename || kind, {type: 'video/mp4'});
        URL.createObjectURL = () => 'blob:cut';
        URL.revokeObjectURL = () => {};
        // jsdom treats the download anchor's click as a navigation and warns; the
        // handing-over itself is what this file checks, not the browser's part of it.
        vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
    });

    it('renders the bar with the numbers for a task that cut itself', async () => {
        lastResult = cutResult();
        mount();

        const bar = await screen.findByTestId('cut-flow-bar');
        expect(bar.textContent).toMatch(/已自动去掉气口/);
        expect(bar.textContent).toMatch(/剪掉 4 处/);
        expect(bar.textContent).toMatch(/播放、字幕、笔记都是这一份剪后版本/);
    });

    it('offers the cut file itself, and asks for the right artifact', async () => {
        lastResult = cutResult();
        const asked = [];
        artifactFetch = async (taskId, kind, filename) => {
            asked.push({taskId, kind, filename});
            return new File(['x'], filename || kind, {type: 'video/mp4'});
        };
        mount();

        const button = await screen.findByRole('button', {name: /下载剪后文件/});
        // The player has already fetched the same artifact by now; what this is
        // about is the fetch the button causes.
        asked.length = 0;
        button.click();
        await waitFor(() => expect(asked.length).toBe(1));
        expect(asked[0].kind).toBe('debreath_media');
        // Handed over under a filename, not under the path it is stored at.
        expect(asked[0].filename).toBe('lecture_debreath.mp4');
    });

    it('says the cut file is unreadable rather than letting the recording stand in', async () => {
        lastResult = cutResult({artifacts: {}});
        mount();

        const bar = await screen.findByTestId('cut-flow-bar');
        expect(bar.textContent).toMatch(/剪后文件读不到了/);
        expect(bar.textContent).toMatch(/不能拿原文件顶上/);
    });

    it('stays out of the way of a task that never went through the automatic flow', async () => {
        lastResult = cutResult({transcript_media: 'source', debreath: undefined, artifacts: {}});
        mount();

        await screen.findByText('edit.regenerate');
        expect(screen.queryByTestId('cut-flow-bar')).toBeNull();
    });
});
