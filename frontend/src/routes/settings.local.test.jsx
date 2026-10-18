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
// GET /speaker-diarization/status: sherpa-onnx installed, models not downloaded.
let diarization = {available: false, dependency_installed: true, models_local: false, requested_backend: 'sherpa'};
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
            getProviderModels,
        }),
    };
});

// One object, as the real useApi returns, so the note style panel's effects run once.
const noteStyle = {
    read: async () => ({skill: {text: '', version: 'v', is_default: true}, candidates: [], unread_edits: 0, proposal: null, history: []}),
    previewTasks: async () => ({tasks: []}),
};

const getProviderModels = async (provider) => (provider === 'deepseek' ? ['deepseek-flash', 'deepseek-v4-pro'] : ['gpt-9-mini', 'gpt-9']);

const {default: Settings} = await import('./settings.jsx');

describe('local settings page', () => {
    beforeEach(() => {
        stored = {};
        servicePreferences = {};
        diarization = {available: false, dependency_installed: true, models_local: false, requested_backend: 'sherpa'};
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
        expect(screen.getByText(/笔记可以交给你自己的 agent/)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', {name: /以后再说/}));
        expect(screen.queryByText(/笔记可以交给你自己的 agent/)).toBeNull();
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
        expect(screen.getByText('DeepSeek API Key')).toBeTruthy();
    });

    // Requirement (2026-10-12): transcription always runs at the accurate
    // setting, so there is no speed to choose.
    it('offers no transcription speed choice', () => {
        render(<Settings/>);
        expect(screen.queryByText('set.sttSpeed')).toBeNull();
        expect(screen.queryByText('转录速度')).toBeNull();
    });

    it('offers no transcription-route choice, because there is only one route', () => {
        render(<Settings/>);
        expect(screen.queryByText('转录路线')).toBeNull();
        expect(screen.getByText('把音视频变成文字的方式。转录在本机完成。')).toBeTruthy();
    });

    it('shows the device-side controls without asking whether local transcription is allowed', async () => {
        render(<Settings/>);
        expect(screen.getByText('视频链接下载登录态')).toBeTruthy();
        expect(await screen.findByText('区分不同讲话人')).toBeTruthy();
    });

    // Requirement: when the component is missing, the row says what to run,
    // offers no switch, and asks for no token.
    it('says how to install speaker separation when the component is missing', async () => {
        diarization = {available: false, dependency_installed: false, models_local: false, requested_backend: 'sherpa'};
        stored = {speakerDiarization: true};
        render(<Settings/>);
        expect(await screen.findByText(/还没装讲话人区分组件。重新运行一次安装脚本/)).toBeTruthy();
        expect(document.getElementById('settingsSpeakerDiarization')).toBeNull();
        expect(screen.queryByText(/模型令牌（Hugging Face）/)).toBeNull();
    });

    // Requirement: the default backend needs no token; a missing model names
    // the script that downloads it.
    it('names the download script when the models are missing, and asks for no token', async () => {
        render(<Settings/>);
        expect(await screen.findByText(/scripts\/fetch_diarization_models\.py/)).toBeTruthy();
        expect(document.getElementById('settingsSpeakerDiarization').disabled).toBe(true);
        expect(screen.queryByText(/模型令牌（Hugging Face）/)).toBeNull();
        expect(screen.queryByText(/Hugging Face/)).toBeNull();
    });

    // Requirement (2026-10-15): installed and ready, the switch works but is off
    // until the user turns it on.
    it('offers the switch, off by default, once sherpa-onnx and its models are in place', async () => {
        diarization = {available: true, dependency_installed: true, models_local: true, requested_backend: 'sherpa', backend: 'sherpa-onnx'};
        render(<Settings/>);
        await waitFor(() => expect(document.getElementById('settingsSpeakerDiarization').disabled).toBe(false));
        expect(document.getElementById('settingsSpeakerDiarization').checked).toBe(false);
        expect(screen.queryByText(/模型令牌（Hugging Face）/)).toBeNull();
    });

    // Requirement: the pyannote token appears only for the optional pyannote
    // backend, installed but without staged models; a fallback is explained.
    it('asks for the pyannote token only when pyannote is chosen and its models are missing', async () => {
        diarization = {
            available: true, dependency_installed: true, models_local: true, backend: 'sherpa-onnx',
            requested_backend: 'pyannote',
            fallback_reason: '设置里选了 pyannote，但它的模型文件和 Hugging Face 令牌都没有，这次改用默认的 sherpa-onnx。',
            pyannote: {available: false, dependency_installed: true, models_local: false, auth_configured: false},
        };
        render(<Settings/>);
        expect(await screen.findByText('pyannote 模型令牌（Hugging Face）')).toBeTruthy();
        expect(screen.getByTestId('diarization-fallback').textContent).toMatch(/改用默认的 sherpa-onnx/);
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
        expect(screen.getByRole('option', {name: '通义千问（阿里云百炼）（推荐）'})).toBeTruthy();
        expect(screen.queryByRole('option', {name: 'Qwen'})).toBeNull();
    });

    // Owner, 2026-10-18: Qwen first and recommended, and Kimi and MiMo offered
    // beside DeepSeek, for users who cannot get an Anthropic key.
    it('offers Qwen first, then DeepSeek, Kimi, MiMo and OpenAI', () => {
        render(<Settings/>);
        const names = screen.getAllByRole('option').map((node) => node.textContent);
        const providers = names.filter((name) => /通义|DeepSeek|Kimi|MiMo|OpenAI/.test(name));
        expect(providers.slice(0, 5)).toEqual([
            '通义千问（阿里云百炼）（推荐）', 'DeepSeek', 'Kimi（月之暗面）', '小米 MiMo', 'OpenAI',
        ]);
    });

    it('asks for the key of the chosen provider, with its console', () => {
        stored = {aiProvider: 'kimi'};
        render(<Settings/>);
        expect(screen.getByText('Kimi（月之暗面） API Key')).toBeTruthy();
        expect(screen.getByRole('link', {name: '去创建 Key'}).getAttribute('href')).toBe('https://platform.moonshot.cn/console/api-keys');
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

    // Requirement (to-do, 2026-10-16): the picture model that judges the cut
    // without Claude is set up here, not only in .env, and says which one is used.
    it('offers the picture model and says which model it uses once all three are in', async () => {
        credentials = {vision_api_key_configured: true, vision_base_url: 'https://api.moonshot.cn/v1', vision_model: 'kimi-k2.6'};
        render(<Settings/>);
        await screen.findByText('看图模型（可选）');
        await waitFor(() => expect(screen.getByText(/现在用的是 kimi-k2.6/)).toBeTruthy());
    });

    it('says the picture model is not set up while a field is missing', async () => {
        credentials = {vision_api_key_configured: false, vision_base_url: 'https://api.moonshot.cn/v1', vision_model: ''};
        render(<Settings/>);
        await waitFor(() => expect(screen.getByText(/还没配好/)).toBeTruthy());
    });

    // Owner, 2026-10-16: a provider retired the model a user had saved and
    // nothing said so. The settings page says it once the provider's list is in.
    it('says when the saved model is no longer in the provider list', async () => {
        credentials = {deepseek_api_key_configured: true};
        stored = {aiProvider: 'deepseek', aiModel: 'deepseek-v3-old'};
        render(<Settings/>);
        await waitFor(() => expect(screen.getByText(/deepseek-v3-old 已不在这家服务商现在的模型列表里/)).toBeTruthy());
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

    // Requirement (2026-10-12): Douyin links always use the third-party
    // resolver, so there is no switch for it.
    it('offers no Douyin fallback switch', () => {
        render(<Settings/>);
        expect(screen.queryByText('抖音备用解析')).toBeNull();
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
    // Requirement (2026-10-13): a cut leaves the machine room unless the user
    // chooses otherwise; the choice reaches the service, which runs ffmpeg; and
    // at no limit the page says that stutter or pops in a video playing beside
    // it come from the busy computer, not the product.
    it('leaves room by default and says nothing about stutter there', async () => {
        render(<Settings/>);
        expect(screen.getByText('电脑占用')).toBeTruthy();
        expect(screen.getByRole('button', {name: /留出余量/}).getAttribute('aria-pressed')).toBe('true');
        expect(screen.queryByText(/卡顿、爆音/)).toBeNull();
    });

    it('saves the CPU choice to the service and warns at no limit', async () => {
        render(<Settings/>);
        fireEvent.click(screen.getByRole('button', {name: /不限制/}));
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({cpu_usage: 'full'}));
        expect(screen.getByText(/卡顿、爆音.*文件本身没有问题/)).toBeTruthy();
    });

    it('shows the CPU choice the service holds', async () => {
        servicePreferences = {cpu_usage: 'light'};
        render(<Settings/>);
        await waitFor(() => expect(screen.getByRole('button', {name: /省着用/}).getAttribute('aria-pressed')).toBe('true'));
    });
    // Requirement (2026-10-14): the processing preset sits beside the CPU
    // share in settings, defaults to picture first, and reaches the service.
    it('offers the processing preset beside the CPU share and saves it', async () => {
        render(<Settings/>);
        expect(screen.getByText('处理偏好')).toBeTruthy();
        expect(screen.getByRole('button', {name: /^画质优先/}).getAttribute('aria-pressed')).toBe('true');
        fireEvent.click(screen.getByRole('button', {name: /省空间/}));
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({processing_preset: 'size'}));
    });
});
