// @vitest-environment jsdom

// The subtitle tab of the start page, mounted. Requirement: a subtitle or
// transcript file is turned into a note by the text model only (Claude's frame
// note needs the video). Someone who filled in only an Anthropic key would
// otherwise submit, wait, and get no note without being told why. So the tab
// says what is missing, points to Settings, and does not take the file.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

let credentials = {};
const summarizeTranscriptFile = vi.fn(async () => ({transcript: 'x', summary: 'y'}));
const stableApi = {
    getCredentialsStatus: async () => credentials,
    summarizeTranscriptFile,
    createVideoSourceJob: vi.fn(),
    getJob: vi.fn(),
};

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        history: [],
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
        setLastSourceFile: () => {},
        addLarkExport: () => {},
        runtimeConfig: {allowedSttProviders: ['local'], defaultSttProvider: 'local', writesItsOwnNote: true, limits: {}},
        setPendingUploadAbort: () => {},
        abortPendingUpload: () => {},
        backendDown: false,
        reportBackendError: () => {},
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {...actual, useI18n: () => ({t: (key) => key, lang: 'zh'}), useApi: () => stableApi};
});

const {default: MediaText} = await import('./media-text.jsx');

const mount = (mode = 'subtitle') => render(
    <MemoryRouter initialEntries={[`/media-text?mode=${mode}`]}>
        <Routes>
            <Route path="/media-text" element={<MediaText/>}/>
            <Route path="/editor" element={<p>editor page</p>}/>
        </Routes>
    </MemoryRouter>,
);

const NOTICE = /用字幕生成笔记需要一个文本模型的 Key（例如 DeepSeek）/;
const ONLY_ANTHROPIC = {anthropic_api_key_configured: true, visual_note_available: true};
const srt = () => new File(['1\n00:00:00,000 --> 00:00:01,000\n你好\n'], 'talk.srt', {type: 'text/plain'});

describe('the subtitle tab without a text-model key', () => {
    beforeEach(() => {
        localStorage.clear();
        summarizeTranscriptFile.mockClear();
    });
    afterEach(() => {
        cleanup();
        localStorage.clear();
    });

    it('says a text-model key is needed and links to Settings', async () => {
        credentials = ONLY_ANTHROPIC;
        mount();
        expect(await screen.findByText(NOTICE)).toBeTruthy();
        expect(screen.getByRole('link', {name: '去设置填写'}).getAttribute('href')).toMatch(/^\/settings/);
    });

    it('does not take a subtitle file, chosen or dropped', async () => {
        credentials = ONLY_ANTHROPIC;
        mount();
        await screen.findByText(NOTICE);
        expect(screen.getByRole('button', {name: /选择字幕文件/}).disabled).toBe(true);
        fireEvent.drop(screen.getByText('拖放或选择字幕 / 文本文件').closest('section'), {dataTransfer: {files: [srt()]}});
        await waitFor(() => expect(screen.getAllByText(NOTICE).length).toBeGreaterThan(1));
        expect(summarizeTranscriptFile).not.toHaveBeenCalled();
    });

    it('says nothing and takes the file once the selected text model has its key', async () => {
        credentials = {...ONLY_ANTHROPIC, deepseek_api_key_configured: true};
        mount();
        await waitFor(() => expect(screen.getByRole('button', {name: /选择字幕文件/}).disabled).toBe(false));
        expect(screen.queryByText(NOTICE)).toBeNull();
        fireEvent.drop(screen.getByText('拖放或选择字幕 / 文本文件').closest('section'), {dataTransfer: {files: [srt()]}});
        await waitFor(() => expect(summarizeTranscriptFile).toHaveBeenCalledTimes(1));
    });

    it('leaves the video tab alone: Claude writes those notes', async () => {
        credentials = ONLY_ANTHROPIC;
        mount('media');
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(screen.queryByText(NOTICE)).toBeNull();
    });
});
