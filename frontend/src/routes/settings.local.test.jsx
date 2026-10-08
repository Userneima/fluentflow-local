// @vitest-environment jsdom

// The local settings page, rendered. The rest of this split's guards search
// source text, which is exactly what missed the last edition mix-up: a page can
// contain all the right strings and still render the wrong controls (or throw).
// This one mounts the real page and looks at what a person would see.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';

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
        }),
    };
});

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

    it('drops the note settings only while an upload writes the note itself', () => {
        render(<Settings/>);
        expect(screen.queryByText('给笔记自动配图')).toBeNull();
        cleanup();

        runtimeConfig.writesItsOwnNote = false;
        render(<Settings/>);
        expect(screen.getByText('给笔记自动配图')).toBeTruthy();
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
        expect(toggle.checked).toBe(true);
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
});
