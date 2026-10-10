// @vitest-environment jsdom

// The local settings page, rendered. The rest of this split's guards search
// source text, which is exactly what missed the last edition mix-up: a page can
// contain all the right strings and still render the wrong controls (or throw).
// This one mounts the real page and looks at what a person would see.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';

const runtimeConfig = {
    allowedSttProviders: ['local'],
    defaultSttProvider: 'local',
    showMaintainerSettings: true,
    writesItsOwnNote: true,
    limits: {},
};

let stored = {};
let servicePreferences = {};
// GET /speaker-diarization/status. The installed build has no pyannote.
let diarization = {available: false, dependency_installed: true, models_local: false};
let credentials = {};
const savePreferences = vi.fn(async (patch) => ({...servicePreferences, ...patch}));

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        clearHistory: () => {},
        history: [],
        larkExports: [],
        runtimeConfig,
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({
            loadSettings: () => stored,
            saveSettings: (next) => { stored = next; },
        }),
        useApi: () => ({
            getCredentialsStatus: async () => credentials,
            saveCredentials: async () => ({}),
            getSpeakerDiarizationStatus: async () => diarization,
            checkVideoCookies: async () => ({ok: true}),
            getPreferences: async () => servicePreferences,
            savePreferences,
            noteStyle,
        }),
    };
});

// One object, as the real useApi returns, so the note style panel's effects run once.
const noteStyle = {
    read: async () => ({skill: {text: '', version: 'v', is_default: true}, candidates: [], unread_edits: 0, proposal: null, history: []}),
    previewTasks: async () => ({tasks: []}),
};

const {default: Settings} = await import('./settings.jsx');

describe('local settings page', () => {
    beforeEach(() => {
        stored = {};
        servicePreferences = {};
        diarization = {available: false, dependency_installed: true, models_local: false};
        credentials = {};
        savePreferences.mockClear();
        localStorage.clear();
        runtimeConfig.writesItsOwnNote = true;
    });

    // No globals in this project's vitest config, so auto-cleanup is off.
    afterEach(cleanup);

    it('reopens the first-run note setup from the notes section, even after it was closed', async () => {
        // Requirement: the setup that explains what a key is, where to get one
        // and what it costs can be opened again from Settings → 笔记.
        localStorage.setItem('fluentflow_note_key_onboarding_dismissed', '1');
        render(<Settings/>);
        fireEvent.click(screen.getByRole('button', {name: /第一次填 Key/}));
        expect(screen.getByText(/相当于这个模型服务的账号密码/)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', {name: /以后再说/}));
        expect(screen.queryByText(/相当于这个模型服务的账号密码/)).toBeNull();
    });

    it('renders the sections this edition has', () => {
        render(<Settings/>);
        expect(screen.getByText('转录')).toBeTruthy();
        expect(screen.getByText('开始处理')).toBeTruthy();
        expect(screen.getByText('导出')).toBeTruthy();
        // The browser-cache "clear history" row was removed: the list reloads
        // from the service, so clearing it looked destructive and changed nothing.
        expect(screen.queryByText('数据')).toBeNull();
        expect(screen.getByText('高级 · 其他凭证')).toBeTruthy();
    });

    it('puts the note key first, where a first-time user looks', () => {
        render(<Settings/>);
        const headings = screen.getAllByRole('heading', {level: 2}).map((node) => node.textContent);
        expect(headings[0]).toBe('笔记');
        expect(screen.getByText('set.deepseekKey')).toBeTruthy();
    });

    it('offers no transcription-route choice, because there is only one route', () => {
        render(<Settings/>);
        expect(screen.queryByText('转录路线')).toBeNull();
        expect(screen.getByText('把音视频变成文字的方式。转录在本机完成。')).toBeTruthy();
    });

    it('shows the device-side controls without asking whether local transcription is allowed', async () => {
        render(<Settings/>);
        expect(screen.getByText('set.sttSpeed')).toBeTruthy();
        expect(screen.getByText('视频链接下载登录态')).toBeTruthy();
        expect(await screen.findByText('讲话人区分令牌（Hugging Face）')).toBeTruthy();
    });

    // Requirement: a build without the speaker separation component says so in
    // one line, offers no switch, and asks for no token it could never use.
    it('says speaker separation is not in this build when the component is missing', async () => {
        diarization = {available: false, dependency_installed: false, models_local: false};
        stored = {speakerDiarization: true};
        render(<Settings/>);
        expect(await screen.findByText('这个版本没有带讲话人区分组件，暂时用不了。')).toBeTruthy();
        expect(document.getElementById('settingsSpeakerDiarization')).toBeNull();
        expect(screen.queryByText('讲话人区分令牌（Hugging Face）')).toBeNull();
    });

    it('asks for the token only while the component is there and the model is not', async () => {
        render(<Settings/>);
        expect(await screen.findByText(/要先在下方「高级」里填讲话人区分令牌/)).toBeTruthy();
        expect(document.getElementById('settingsSpeakerDiarization').disabled).toBe(true);
        cleanup();
        diarization = {available: true, dependency_installed: true, models_local: true};
        render(<Settings/>);
        await waitFor(() => expect(document.getElementById('settingsSpeakerDiarization').disabled).toBe(false));
        expect(screen.queryByText('讲话人区分令牌（Hugging Face）')).toBeNull();
    });

    it('names the Feishu app credentials in Chinese', () => {
        render(<Settings/>);
        expect(screen.getByText('飞书应用 ID')).toBeTruthy();
        expect(screen.getByText('飞书应用密钥')).toBeTruthy();
        expect(screen.queryByText('FEISHU APP ID')).toBeNull();
        expect(screen.queryByText('FEISHU APP SECRET')).toBeNull();
    });

    // Requirement: the notes section opens with what is always true, and the
    // writer sentence alone says who writes the note, so the two cannot clash.
    it('opens the notes section with the transcription fact only', () => {
        render(<Settings/>);
        const intro = screen.getByText(/^转录在本机完成，不需要 Key。/);
        expect(intro.textContent).not.toMatch(/处理完就会自动写笔记/);
    });

    it('does not promise a text-model note when no text-model key is filled in', () => {
        render(<Settings/>);
        expect(screen.queryByText(/由上面的文本模型根据转录稿写/)).toBeNull();
        expect(screen.getByText(/两个都不填就只有转录稿，没有笔记/)).toBeTruthy();
    });

    it('calls Qwen by one name', () => {
        stored = {aiProvider: 'qwen'};
        render(<Settings/>);
        expect(screen.getByRole('option', {name: '通义千问（阿里云百炼）'})).toBeTruthy();
        expect(screen.queryByRole('option', {name: 'Qwen'})).toBeNull();
    });

    // Requirement: auto-illustrate and the Qwen key are hidden only when they
    // steer nothing, i.e. when Claude writes the note. A build that writes its
    // own note but has no Claude channel falls back to the text note, where
    // both still apply and cost money, so they must stay visible.
    it('keeps auto-illustrate and the Qwen key while the text model writes the note', async () => {
        runtimeConfig.writesItsOwnNote = true;
        credentials = {deepseek_api_key_configured: true, visual_note_available: false};
        render(<Settings/>);
        await waitFor(() => expect(screen.getByText('给笔记自动配图')).toBeTruthy());
        expect(screen.getByText('set.dashscopeKey')).toBeTruthy();
    });

    it('hides them while Claude writes the note', async () => {
        runtimeConfig.writesItsOwnNote = true;
        credentials = {anthropic_api_key_configured: true, visual_note_available: true};
        render(<Settings/>);
        await waitFor(() => expect(screen.queryByText('给笔记自动配图')).toBeNull());
        expect(screen.queryByText('set.dashscopeKey')).toBeNull();
    });

    it('shows them when this build never writes its own note', async () => {
        runtimeConfig.writesItsOwnNote = false;
        credentials = {anthropic_api_key_configured: true, visual_note_available: true};
        render(<Settings/>);
        await screen.findByText('给笔记自动配图');
    });

    it('carries no hosted account surface', () => {
        render(<Settings/>);
        expect(screen.queryByText('设备与云端')).toBeNull();
        expect(screen.queryByText('账号')).toBeNull();
    });

    // Links an AI tool submits are downloaded by the service, which only knows
    // what it was told: the choice has to reach it, not just this browser.
    it('saves the browser-login choice to the service', async () => {
        render(<Settings/>);
        const select = screen.getByDisplayValue('关闭（不读取浏览器登录态）');
        fireEvent.change(select, {target: {value: 'safari'}});
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({video_cookies_browser: 'safari'}));
        expect(stored.videoCookiesBrowser).toBe('safari');
    });

    it('shows the service choice rather than an older one stored here', async () => {
        stored = {videoCookiesBrowser: 'safari'};
        servicePreferences = {video_cookies_browser: 'chrome'};
        render(<Settings/>);
        await waitFor(() => expect(screen.getByDisplayValue('Chrome')).toBeTruthy());
    });

    it('offers the Douyin fallback as a switch that says what it sends', async () => {
        servicePreferences = {allow_miuistore: true};
        render(<Settings/>);
        expect(screen.getByText('抖音备用解析')).toBeTruthy();
        expect(screen.getByText(/只发链接本身/)).toBeTruthy();
        const toggle = screen.getByLabelText(/抖音备用解析/);
        await waitFor(() => expect(toggle.checked).toBe(true));
        fireEvent.click(toggle);
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({allow_miuistore: false}));
        expect(toggle.checked).toBe(false);
    });

    // Requirement: the one switch that sends something to a third party is a
    // row of its own, not a sub-item of the browser-login row.
    it('gives the Douyin fallback its own row', () => {
        render(<Settings/>);
        const row = screen.getByLabelText(/抖音备用解析/).closest('label');
        expect(row.textContent).not.toMatch(/视频链接下载登录态/);
        const cookiesRow = screen.getByText('视频链接下载登录态').parentElement;
        expect(cookiesRow.contains(row)).toBe(false);
    });

    // Requirement: the Douyin fallback has three states. Never answered shows
    // as off and says when the question will come.
    it('shows a never-answered Douyin fallback as off, saying when it will be asked', async () => {
        servicePreferences = {};
        render(<Settings/>);
        expect(await screen.findByText('还没选，第一次提交抖音链接时会问。')).toBeTruthy();
        expect(screen.getByLabelText(/抖音备用解析/).checked).toBe(false);
    });

    it('shows an answered "no" as off without the not-chosen note', async () => {
        servicePreferences = {allow_miuistore: false};
        render(<Settings/>);
        // Let the service's answer arrive before looking.
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
        expect(screen.getByLabelText(/抖音备用解析/).checked).toBe(false);
        expect(screen.queryByText('还没选，第一次提交抖音链接时会问。')).toBeNull();
    });

    // Requirement: exports an AI tool asks for use the route and folder chosen
    // here, so both reach the service, not just this browser.
    it('saves the Feishu route to the service', async () => {
        render(<Settings/>);
        fireEvent.change(screen.getByDisplayValue('set.larkRouteAuto'), {target: {value: 'openapi'}});
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({lark_export_route: 'openapi'}));
        expect(stored).toMatchObject({larkExportRoute: 'openapi', larkViaCli: false});
    });

    it('saves the Feishu folder to the service when the field is left', async () => {
        render(<Settings/>);
        const field = document.getElementById('settingsLarkFolder');
        fireEvent.change(field, {target: {value: ' https://x.feishu.cn/drive/folder/fldcnABC '}});
        expect(savePreferences).not.toHaveBeenCalledWith(expect.objectContaining({lark_folder_token: expect.anything()}));
        fireEvent.blur(field);
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({lark_folder_token: 'https://x.feishu.cn/drive/folder/fldcnABC'}));
        expect(stored.larkFolder).toBe('https://x.feishu.cn/drive/folder/fldcnABC');
    });

    it('shows the route and folder the service holds', async () => {
        localStorage.setItem('fluentflow_lark_export_migrated', '1');
        servicePreferences = {lark_export_route: 'openapi', lark_folder_token: 'https://x.feishu.cn/drive/folder/fldcnXYZ'};
        render(<Settings/>);
        await waitFor(() => expect(screen.getByDisplayValue('set.larkRouteOpenapi')).toBeTruthy());
        expect(document.getElementById('settingsLarkFolder').value).toBe('https://x.feishu.cn/drive/folder/fldcnXYZ');
    });
});
