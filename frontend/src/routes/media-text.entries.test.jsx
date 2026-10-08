// @vitest-environment jsdom

// The four ways a recording gets in: dragged in (upload), a link, files picked
// through the system dialog (by path), and a whole folder (by path).
//
// What the person should get: the settings they chose apply whichever way they
// used. Auto-export to Feishu, the provider and model, the prompt, auto
// illustration, voice enhancement and speaker separation reach the service the
// same way from every entry. Checked on the wire (the real request bodies), not
// on the page's internal option objects, because a setting dropped while
// serialising is exactly how the dialog and folder entries lost them before.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

// GET /speaker-diarization/status as the app provider holds it.
let diarizationStatus = null;
let history = [];

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        history,
        diarizationStatus,
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
        setLastSourceFile: () => {},
        addLarkExport: () => {},
        runtimeConfig: {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}},
        setPendingUploadAbort: () => {},
        abortPendingUpload: () => {},
        backendDown: false,
        reportBackendError: () => {},
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {...actual, useI18n: () => ({t: (key) => key, lang: 'zh'})};
});

const {default: MediaText} = await import('./media-text.jsx');

const SETTINGS = {
    exportToLark: true,
    larkExportRoute: 'local_cli',
    aiProvider: 'qwen',
    aiModel: 'qwen-max',
    promptPreset: 'custom',
    customPromptText: '只列三条要点',
    noteMode: 'auto',
    autoIllustrate: true,
    voiceEnhance: true,
    speakerDiarization: true,
    sttSpeed: 'balanced',
};

// Request bodies seen on the wire, by entry.
let sent = {};
const json = (data, status = 200) => ({ok: status < 300, status, json: async () => data});

const fetchStub = vi.fn(async (url, init = {}) => {
    const path = String(url);
    const body = init.body && typeof init.body === 'string' ? JSON.parse(init.body) : null;
    if (path.includes('/local/choose-media')) return json({files: [{path: '/rec/a.mp4', usable: true}]});
    if (path.includes('/local/choose-folder')) return json({path: '/rec', count: 2});
    if (path.includes('/local/locate-dropped')) return json({found: false});
    if (path.includes('/queue/process-local-files')) { sent.paths = body; return json({count: 1, queued: []}); }
    if (path.includes('/queue/process-folder')) { sent.folder = body; return json({count: 2, queued: []}); }
    if (path.includes('/video-sources/jobs')) { sent.link = body.options; return json({job: {task_id: 'L1', status: 'queued'}}); }
    return json({});
});

class FakeXhr {
    constructor() { this.upload = {}; this.status = 0; this.responseText = ''; }
    open() {}
    setRequestHeader() {}
    send(fd) {
        sent.upload = Object.fromEntries([...fd.entries()].filter(([key]) => key !== 'files'));
        this.status = 200;
        this.responseText = '{"queued":[]}';
        this.onload?.();
    }
    abort() {}
}

const mount = () => render(
    <MemoryRouter initialEntries={['/media-text?mode=media']}>
        <Routes>
            <Route path="/media-text" element={<MediaText/>}/>
            <Route path="/agent" element={<p>agent page</p>}/>
        </Routes>
    </MemoryRouter>,
);

const submitThroughEveryEntry = async () => {
    // Dragged in, not found on disk: uploaded.
    mount();
    const file = new File(['x'], 'talk.mp4', {type: 'video/mp4'});
    fireEvent.drop(screen.getByText('拖放或选择音视频文件'), {dataTransfer: {files: [file]}});
    await waitFor(() => expect(sent.upload).toBeTruthy());
    cleanup();

    // Picked through the system dialog.
    mount();
    fireEvent.click(screen.getByText('拖放或选择音视频文件'));
    await waitFor(() => expect(sent.paths).toBeTruthy());
    cleanup();

    // A whole folder.
    mount();
    fireEvent.click(screen.getByText('处理整个文件夹'));
    await waitFor(() => expect(sent.folder).toBeTruthy());
    cleanup();

    // A link.
    mount();
    fireEvent.click(screen.getByText('链接'));
    fireEvent.change(screen.getByRole('textbox'), {target: {value: 'https://www.bilibili.com/video/BV1xx411c7mD'}});
    await act(async () => { fireEvent.click(screen.getByTestId('submit-video-link')); });
    await waitFor(() => expect(sent.link).toBeTruthy());
};

// What is not a user setting: which file, and (on the by-path routes, which
// always transcribe on this machine) the engine and language; the link's
// browser login is about downloading, which only a link does.
const settingsOnly = (body) => {
    const rest = {...body};
    ['paths', 'path', 'stt_provider', 'stt_language', 'cookies_from_browser'].forEach((key) => delete rest[key]);
    return rest;
};

describe('every entry sends the same settings', () => {
    beforeEach(() => {
        sent = {};
        localStorage.setItem('fluentflow_settings', JSON.stringify({...SETTINGS, defaultSourceMode: 'upload'}));
        vi.stubGlobal('fetch', fetchStub);
        vi.stubGlobal('XMLHttpRequest', FakeXhr);
        vi.spyOn(window, 'confirm').mockReturnValue(true);
    });
    afterEach(() => {
        cleanup();
        localStorage.clear();
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('upload, link, picked files and a folder carry an identical option set', async () => {
        await submitThroughEveryEntry();
        const upload = settingsOnly(sent.upload);
        expect(settingsOnly(sent.paths)).toEqual(upload);
        expect(settingsOnly(sent.folder)).toEqual(upload);
        expect(settingsOnly(sent.link)).toEqual(upload);

        // And that set is the user's settings, Feishu export included.
        expect(upload).toMatchObject({
            export_to_lark: 'true',
            lark_export_route: 'local_cli',
            ai_provider: 'qwen',
            ai_model: 'qwen-max',
            system_prompt: '只列三条要点',
            prompt_preset: 'custom',
            generate_visuals: 'true',
            voice_enhance: 'true',
            speaker_diarization: 'true',
        });
    });

    it('says "speaker separation off" out loud on the by-path entries, where a missing field means on', async () => {
        localStorage.setItem('fluentflow_settings', JSON.stringify({...SETTINGS, speakerDiarization: false, defaultSourceMode: 'upload'}));
        await submitThroughEveryEntry();
        expect(sent.paths.speaker_diarization).toBe('false');
        expect(sent.folder.speaker_diarization).toBe('false');
        expect(sent.upload.speaker_diarization).toBeUndefined();
    });

    // Requirement: a build without the speaker separation component does not
    // ask for it, so no task carries a request that is always skipped.
    it('does not ask for speaker separation when this build cannot do it', async () => {
        diarizationStatus = {available: false, dependency_installed: false};
        try {
            await submitThroughEveryEntry();
        } finally {
            diarizationStatus = null;
        }
        expect(sent.upload.speaker_diarization).toBeUndefined();
        expect(sent.paths.speaker_diarization).toBe('false');
        expect(sent.folder.speaker_diarization).toBe('false');
        expect(sent.link.speaker_diarization).toBeUndefined();
    });
});

// Requirement: 「最近任务」 names a cancelled task 已取消, as the task list does.
describe('recent tasks on the start page', () => {
    afterEach(() => { cleanup(); history = []; });

    it('labels each state the way the task list does', () => {
        history = [
            {id: 'a', name: '取消的', status: 'cancelled', timestamp: Date.now()},
            {id: 'b', name: '失败的', status: 'failed', timestamp: Date.now()},
            {id: 'c', name: '完成的', status: 'completed', timestamp: Date.now()},
            {id: 'd', name: '进行中的', status: 'processing', timestamp: Date.now()},
        ];
        mount();
        const badge = (name) => screen.getByText(name).parentElement.querySelector('span').textContent;
        expect(badge('取消的')).toBe('已取消');
        expect(badge('失败的')).toBe('失败');
        expect(badge('完成的')).toBe('已完成');
        expect(badge('进行中的')).toBe('处理中');
    });
});

// The two requests the page makes to the service for these fixes, on the wire.
describe('what the page asks the service', () => {
    afterEach(() => vi.unstubAllGlobals());
    const realApi = async () => {
        const {useApi} = await vi.importActual('../app/shared.jsx');
        const {renderHook} = await import('@testing-library/react');
        return renderHook(() => useApi()).result.current;
    };

    it('"再处理一次" sends allow_duplicate beside the options, never inside them', async () => {
        const calls = [];
        vi.stubGlobal('fetch', vi.fn(async (url, init) => { calls.push(JSON.parse(init.body)); return json({job: {task_id: 'n'}}); }));
        const api = await realApi();
        await api.createVideoSourceJob('https://b23.tv/a', {aiProvider: 'qwen', allowDuplicate: true});
        await api.createVideoSourceJob('https://b23.tv/a', {aiProvider: 'qwen'});
        expect(calls[0].allow_duplicate).toBe(true);
        expect(calls[0].options.allow_duplicate).toBeUndefined();
        expect(calls[1].allow_duplicate).toBeUndefined();
    });

    it('asks whether Claude can use the frames without starting a rewrite, and treats a failure as unknown', async () => {
        const seen = [];
        vi.stubGlobal('fetch', vi.fn(async (url, init) => {
            seen.push({url: String(url), body: JSON.parse(init.body)});
            return json({eligible: false, reason: '这个任务没有剪后视频。'});
        }));
        const api = await realApi();
        expect(await api.getVisualNoteAvailability('t 1')).toEqual({available: false, reason: '这个任务没有剪后视频。', running: false});
        expect(seen[0].url).toMatch(/\/jobs\/t%201\/visual-note$/);
        expect(seen[0].body).toEqual({preview: true});

        vi.stubGlobal('fetch', vi.fn(async () => json({detail: 'no'}, 500)));
        expect(await api.getVisualNoteAvailability('t1')).toEqual({available: null, reason: null});
    });
});
