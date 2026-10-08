// @vitest-environment jsdom

// The editor, mounted, looking for the one line that says what the upload
// already did to this recording.
//
// CutFlowBar had its own tests and no page rendered it, which is the failure this
// file exists to make loud: a component test proves the bar works, not that the
// reader ever sees it. So this one asserts against the real page.

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
            uploadJobPlaybackAudio: async () => ({}),
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

    it('points to the text-model choice in 重生笔记, where the prompt is used', async () => {
        runtimeConfig.writesItsOwnNote = true;
        credentialStatus = {visual_note_available: true};
        mount();
        const button = await screen.findByRole('button', {name: /prompt\.collapsed/});
        await waitFor(() => expect(button.getAttribute('title')).toMatch(/Claude 结合画面写笔记/));
        fireEvent.click(button);
        const note = await screen.findByTestId('prompt-scope-note');
        expect(note.textContent).toMatch(/重生笔记/);
        expect(note.textContent).toMatch(/改用文本模型按文字重写/);
    });

    it('adds nothing when a text model writes the note', async () => {
        credentialStatus = {deepseek_api_key_configured: true};
        mount();
        fireEvent.click(await screen.findByRole('button', {name: /prompt\.collapsed/}));
        await screen.findByRole('dialog');
        expect(screen.queryByTestId('prompt-scope-note')).toBeNull();
    });
});
