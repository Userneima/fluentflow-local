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
// Replaced by a test that needs the status to arrive late or fail.
let credentialsStatus = async () => credentials;
const saveCredentials = vi.fn(async () => ({}));
// GET /workflow-status: what the service says about each step.
const STEP_IDS = ['intake', 'cutting', 'transcription', 'notes', 'outputs', 'agent'];
const workflowFrom = (overrides = {}) => ({
    attention: overrides.attention || [],
    steps: STEP_IDS.map((id) => ({
        id, title: {zh: `步骤 ${id}`, en: id}, summary: {zh: overrides[id] || `${id} 的现状`, en: id}, level: 'ok',
        ...(id === 'notes' ? {writer: overrides.writer || 'none', claude_channel: overrides.claudeChannel || ''} : {}),
        ...(id === 'cutting' ? {judge: overrides.judge || 'loudness'} : {}),
    })),
});
// The service saying Claude (on the subscription) writes the note and judges the cut.
const CLAUDE_AT_WORK = {writer: 'claude', claudeChannel: 'subscription', judge: 'claude'};
let workflow = workflowFrom();
let workflowStatus = async () => workflow;
const getWorkflowStatus = vi.fn((choices) => workflowStatus(choices));
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
            getCredentialsStatus: () => credentialsStatus(),
            saveCredentials,
            getSpeakerDiarizationStatus: async () => diarization,
            getWorkflowStatus,
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

// Each step's options and the accounts section open from their header button.
const open = (...ids) => {
    for (const id of ids) fireEvent.click(document.querySelector(`#${id} > button`));
};

describe('local settings page', () => {
    beforeEach(() => {
        stored = {};
        servicePreferences = {};
        diarization = {available: false, dependency_installed: true, models_local: false, requested_backend: 'sherpa'};
        credentials = {};
        credentialsStatus = async () => credentials;
        workflow = workflowFrom();
        workflowStatus = async () => workflow;
        getWorkflowStatus.mockClear();
        saveCredentials.mockClear();
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
        open('accounts');
        fireEvent.click(await screen.findByRole('button', {name: /第一次填 Key/}));
        expect(screen.getByText(/笔记可以交给你自己的 agent/)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', {name: /以后再说/}));
        expect(screen.queryByText(/笔记可以交给你自己的 agent/)).toBeNull();
    });

    // Decision 6.24: one line per step a recording goes through, in order,
    // then the accounts; no hosted-only or retired sections.
    it('shows one line per step, in the order a recording goes, then the accounts', async () => {
        render(<Settings/>);
        const ids = Array.from(document.querySelectorAll('main section[id]')).map((node) => node.id);
        expect(ids).toEqual([...STEP_IDS, 'accounts']);
        expect(await screen.findByText('cutting 的现状')).toBeTruthy();
        expect(screen.queryByText('数据')).toBeNull();
    });

    // Each line is what the service says, not something the page works out.
    it('shows what the service says about each step, and passes it the choices kept here', async () => {
        stored = {defaultSourceMode: 'upload', voiceEnhance: true};
        workflow = workflowFrom({notes: 'Claude 结合画面写'});
        render(<Settings/>);
        expect(await screen.findByText('Claude 结合画面写')).toBeTruthy();
        expect(getWorkflowStatus).toHaveBeenCalledWith({default_source: 'upload', voice_enhance: '1'});
    });

    // Decision 6.31: the text model, speaker labels and auto export are read
    // from and saved to the service, not this browser.
    it('reads the text model and switches from the service and saves changes there', async () => {
        servicePreferences = {ai_provider: 'kimi', speaker_diarization: true, export_to_lark: true};
        stored = {aiProvider: 'deepseek'};
        render(<Settings/>);
        open('accounts');
        expect(await screen.findByText('Kimi（月之暗面） API Key')).toBeTruthy();
        fireEvent.change(screen.getByDisplayValue('Kimi（月之暗面）'), {target: {value: 'qwen'}});
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({ai_provider: 'qwen', ai_model: ''}));
        // The stale browser copy was dropped (the service already had a value)
        // and the change was not written back to the browser.
        expect('aiProvider' in stored).toBe(false);
        cleanup();
        render(<Settings/>);
        open('outputs');
        await waitFor(() => expect(document.getElementById('settingsExportToLark').checked).toBe(true));
        fireEvent.click(document.getElementById('settingsExportToLark'));
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({export_to_lark: false}));
    });

    it('says loading until the service answers, and says so when it cannot', async () => {
        workflowStatus = () => new Promise(() => {});
        render(<Settings/>);
        expect(document.querySelector('#cutting p').textContent).toBe('正在读取…');
        cleanup();
        workflowStatus = async () => { throw new Error('down'); };
        render(<Settings/>);
        await waitFor(() => expect(document.querySelector('#cutting p').textContent).toMatch(/读不到现状/));
    });

    it('lists what needs the person at the top, and takes them to the step that fixes it', async () => {
        workflow = workflowFrom({attention: [{id: 'no-writer', target: 'accounts', text: {zh: '现在只出转录稿', en: ''}}]});
        render(<Settings/>);
        expect((await screen.findByTestId('settings-attention')).textContent).toMatch(/现在只出转录稿/);
        fireEvent.click(screen.getByRole('button', {name: '去处理'}));
        expect(await screen.findByText('第一次填 Key？看看 Key 是什么、去哪里申请、大概花多少钱')).toBeTruthy();
    });

    it('says at the top when this run uses the fake model, with nothing to click', async () => {
        workflow = workflowFrom({attention: [{id: 'fake-model', action: 'none', text: {zh: '这次启动开着假模型', en: ''}}]});
        render(<Settings/>);
        const box = await screen.findByTestId('settings-attention');
        expect(box.textContent).toMatch(/这次启动开着假模型/);
        expect(box.querySelector('button')).toBeNull();
    });

    it('shows nothing at the top when nothing needs the person', async () => {
        render(<Settings/>);
        await screen.findByText('cutting 的现状');
        expect(screen.queryByTestId('settings-attention')).toBeNull();
    });

    it('says whether an agent is connected once, on the step line, with the connect commands under it', async () => {
        workflow = workflowFrom({agent: '已有 agent 连上。'});
        render(<Settings/>);
        await screen.findByText('已有 agent 连上。');
        open('agent');
        expect(screen.getAllByText(/agent 连上/)).toHaveLength(1);
        expect(document.querySelector('#agent h1')).toBeNull();
        expect(screen.getByText('Claude Code')).toBeTruthy();
    });

    it('keeps keys closed until asked for, and opens them for a link to add one', async () => {
        render(<Settings/>);
        expect(screen.queryByText('DeepSeek API Key')).toBeNull();
        cleanup();
        window.history.replaceState(null, '', '#notes');
        render(<Settings/>);
        expect(await screen.findByText('DeepSeek API Key')).toBeTruthy();
        window.history.replaceState(null, '', '#');
    });

    // Checklist 2026-10-22 (lane 3, item 2): before the service answers, the
    // page must not claim a key is missing or show fields that the answer
    // will hide; it says it is loading.
    it('says nothing about any key until the service has answered', async () => {
        let answer;
        credentialsStatus = () => new Promise((resolve) => { answer = resolve; });
        workflow = workflowFrom(CLAUDE_AT_WORK);
        render(<Settings/>);
        open('accounts');
        expect(screen.getByTestId('note-keys-loading').textContent).toMatch(/正在读取/);
        expect(screen.queryByText(/未配置/)).toBeNull();
        expect(screen.queryByText(/已配置/)).toBeNull();
        expect(screen.queryByText('set.dashscopeKey')).toBeNull();
        expect(screen.queryByText(/填好上面的 Anthropic API Key/)).toBeNull();
        answer({anthropic_api_key_configured: true, visual_note_available: true});
        expect(await screen.findByText(/已配置/)).toBeTruthy();
        expect(screen.queryByTestId('note-keys-loading')).toBeNull();
        expect(screen.queryByText('set.dashscopeKey')).toBeNull();
    });

    it('says the key status could not be read instead of saying keys are missing', async () => {
        credentialsStatus = async () => { throw new Error('down'); };
        render(<Settings/>);
        open('accounts');
        await waitFor(() => expect(screen.getByTestId('note-keys-loading').textContent).toMatch(/读不到 Key 的状态/));
        expect(screen.queryByText(/未配置/)).toBeNull();
    });

    // Checklist item 4: one save for the section's keys. Typing and leaving a
    // field saves nothing (a half-pasted key must not replace a working one);
    // the button sends everything typed in one go.
    it('saves the typed keys together, only when asked', async () => {
        render(<Settings/>);
        open('accounts');
        await screen.findByText('DeepSeek API Key');
        const textKey = screen.getAllByPlaceholderText('粘贴 API Key')[0];
        fireEvent.change(textKey, {target: {value: ' sk-half'}});
        fireEvent.blur(textKey);
        const visionAddress = screen.getByPlaceholderText('https://api.moonshot.cn/v1');
        fireEvent.change(visionAddress, {target: {value: 'https://api.moonshot.cn/v1'}});
        fireEvent.blur(visionAddress);
        expect(saveCredentials).not.toHaveBeenCalled();
        expect(screen.getByText('有没保存的改动')).toBeTruthy();
        const saveButtons = screen.getAllByRole('button', {name: '保存'});
        fireEvent.click(saveButtons[0]);
        await waitFor(() => expect(saveCredentials).toHaveBeenCalledTimes(1));
        expect(saveCredentials).toHaveBeenCalledWith({deepseek_api_key: 'sk-half', vision_base_url: 'https://api.moonshot.cn/v1'});
        expect(await screen.findByText('已保存')).toBeTruthy();
    });

    it('has one save button for the note keys, not one per field', async () => {
        render(<Settings/>);
        open('accounts');
        await screen.findByText('看图模型（可选）');
        const saves = screen.getAllByRole('button', {name: '保存'});
        // The note keys' button, and the Feishu app's (shown while that route can be used).
        expect(saves).toHaveLength(2);
    });

    it('saves with Enter in a key field', async () => {
        render(<Settings/>);
        open('accounts');
        await screen.findByText('DeepSeek API Key');
        const textKey = screen.getAllByPlaceholderText('粘贴 API Key')[0];
        fireEvent.change(textKey, {target: {value: 'sk-whole'}});
        fireEvent.keyDown(textKey, {key: 'Enter'});
        await waitFor(() => expect(saveCredentials).toHaveBeenCalledWith({deepseek_api_key: 'sk-whole'}));
    });

    // Checklist item 7: past exports are history, not a setting.
    it('does not list past exports', () => {
        render(<Settings/>);
        expect(screen.queryByText('set.larkHistory')).toBeNull();
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
        open('transcription');
        expect(screen.queryByText('转录路线')).toBeNull();
    });

    it('shows the device-side controls without asking whether local transcription is allowed', async () => {
        render(<Settings/>);
        open('intake');
        expect(screen.getByText('视频链接下载登录态')).toBeTruthy();
        cleanup();
        render(<Settings/>);
        open('transcription');
        expect(await screen.findByText('区分不同讲话人')).toBeTruthy();
    });

    // Requirement: when the component is missing, the row says what to run,
    // offers no switch, and asks for no token.
    it('says how to install speaker separation when the component is missing', async () => {
        diarization = {available: false, dependency_installed: false, models_local: false, requested_backend: 'sherpa'};
        render(<Settings/>);
        open('transcription');
        expect(await screen.findByText(/还没装讲话人区分组件。重新运行一次安装脚本/)).toBeTruthy();
        expect(document.getElementById('settingsSpeakerDiarization')).toBeNull();
        expect(screen.queryByText(/模型令牌（Hugging Face）/)).toBeNull();
    });

    // Requirement: the default backend needs no token; a missing model names
    // the script that downloads it.
    it('names the download script when the models are missing, and asks for no token', async () => {
        render(<Settings/>);
        open('transcription', 'accounts');
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
        open('transcription', 'accounts');
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
        open('transcription', 'accounts');
        expect(await screen.findByText('pyannote 模型令牌（Hugging Face）')).toBeTruthy();
        expect(screen.getByTestId('diarization-fallback').textContent).toMatch(/改用默认的 sherpa-onnx/);
    });

    it('names the Feishu app credentials in Chinese', async () => {
        render(<Settings/>);
        open('accounts');
        expect((await screen.findByText('飞书应用 ID'))).toBeTruthy();
        expect(screen.getByText('飞书应用密钥')).toBeTruthy();
        expect(screen.queryByText('FEISHU APP ID')).toBeNull();
        expect(screen.queryByText('FEISHU APP SECRET')).toBeNull();
    });

    // Checklist item 3: the notes section says who writes the note; that
    // transcription needs no key belongs to the transcription section.

    it('does not promise a text-model note when no text-model key is filled in', async () => {
        render(<Settings/>);
        open('accounts');
        expect(await screen.findByText(/两个都不填就只有转录稿，没有笔记/)).toBeTruthy();
        expect(screen.queryByText(/由上面的文本模型根据转录稿写/)).toBeNull();
    });

    it('calls Qwen by one name', async () => {
        servicePreferences = {ai_provider: 'qwen'};
        render(<Settings/>);
        open('accounts');
        expect(await screen.findByRole('option', {name: '通义千问（阿里云百炼）'})).toBeTruthy();
        expect(screen.queryByRole('option', {name: 'Qwen'})).toBeNull();
    });

    // Owner, 2026-10-18: Qwen first, and Kimi and MiMo offered beside
    // DeepSeek. Checklist item 6: the list shows what is chosen, so it carries
    // no "recommended" mark on another provider.
    it('offers Qwen first, then DeepSeek, Kimi, MiMo and OpenAI, with no recommended mark', async () => {
        render(<Settings/>);
        open('accounts');
        await screen.findByText('DeepSeek API Key');
        const names = screen.getAllByRole('option').map((node) => node.textContent);
        const providers = names.filter((name) => /通义|DeepSeek|Kimi|MiMo|OpenAI/.test(name));
        expect(providers.slice(0, 5)).toEqual([
            '通义千问（阿里云百炼）', 'DeepSeek', 'Kimi（月之暗面）', '小米 MiMo', 'OpenAI',
        ]);
        expect(names.some((name) => /推荐/.test(name) && /通义|DeepSeek|Kimi|MiMo|OpenAI/.test(name))).toBe(false);
    });

    it('asks for the key of the chosen provider, with its console', async () => {
        servicePreferences = {ai_provider: 'kimi'};
        render(<Settings/>);
        open('accounts');
        expect(await screen.findByText('Kimi（月之暗面） API Key')).toBeTruthy();
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
        open('notes', 'accounts');
        await waitFor(() => expect(screen.getByText('给笔记自动配图')).toBeTruthy());
        expect(screen.getByText('set.dashscopeKey')).toBeTruthy();
    });

    it('hides them while Claude writes the note', async () => {
        runtimeConfig.writesItsOwnNote = true;
        credentials = {anthropic_api_key_configured: true, visual_note_available: true};
        workflow = workflowFrom(CLAUDE_AT_WORK);
        render(<Settings/>);
        open('notes', 'accounts');
        await waitFor(() => expect(screen.queryByText('给笔记自动配图')).toBeNull());
        expect(screen.queryByText('set.dashscopeKey')).toBeNull();
    });

    it('shows them when this build never writes its own note', async () => {
        runtimeConfig.writesItsOwnNote = false;
        credentials = {anthropic_api_key_configured: true, visual_note_available: true};
        render(<Settings/>);
        open('notes');
        await screen.findByText('给笔记自动配图');
    });

    // Requirement (to-do, 2026-10-16): the picture model that judges the cut
    // without Claude is set up here, not only in .env, and says which one is used.
    it('offers the picture model and says which model it uses once all three are in', async () => {
        credentials = {vision_api_key_configured: true, vision_base_url: 'https://api.moonshot.cn/v1', vision_model: 'kimi-k2.6'};
        render(<Settings/>);
        open('accounts');
        await screen.findByText('看图模型（可选）');
        await waitFor(() => expect(screen.getByText(/配的是 kimi-k2.6/)).toBeTruthy());
    });

    it('says the picture model is not set up while a field is missing', async () => {
        credentials = {vision_api_key_configured: false, vision_base_url: 'https://api.moonshot.cn/v1', vision_model: ''};
        render(<Settings/>);
        open('accounts');
        await waitFor(() => expect(screen.getByText(/还没配好/)).toBeTruthy());
    });

    // Owner, 2026-10-16: a provider retired the model a user had saved and
    // nothing said so. The settings page says it once the provider's list is in.
    it('says when the saved model is no longer in the provider list', async () => {
        credentials = {deepseek_api_key_configured: true};
        servicePreferences = {ai_provider: 'deepseek', ai_model: 'deepseek-v3-old'};
        render(<Settings/>);
        open('accounts');
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
        open('intake');
        const select = screen.getByDisplayValue('关闭（不读取浏览器登录态）');
        fireEvent.change(select, {target: {value: 'safari'}});
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({video_cookies_browser: 'safari'}));
        expect(stored.videoCookiesBrowser).toBe('safari');
    });

    it('shows the service choice rather than an older one stored here', async () => {
        stored = {videoCookiesBrowser: 'safari'};
        servicePreferences = {video_cookies_browser: 'chrome'};
        render(<Settings/>);
        open('intake');
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
        open('outputs');
        fireEvent.change(screen.getByDisplayValue('set.larkRouteAuto'), {target: {value: 'openapi'}});
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({lark_export_route: 'openapi'}));
        expect(stored).toMatchObject({larkExportRoute: 'openapi', larkViaCli: false});
    });

    it('saves the Feishu folder to the service when the field is left', async () => {
        render(<Settings/>);
        open('outputs');
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
        open('outputs');
        await waitFor(() => expect(screen.getByDisplayValue('set.larkRouteOpenapi')).toBeTruthy());
        expect(document.getElementById('settingsLarkFolder').value).toBe('https://x.feishu.cn/drive/folder/fldcnXYZ');
    });
    // Requirement (2026-10-13): a cut leaves the machine room unless the user
    // chooses otherwise; the choice reaches the service, which runs ffmpeg; and
    // at no limit the page says that stutter or pops in a video playing beside
    // it come from the busy computer, not the product.
    it('leaves room by default and says nothing about stutter there', async () => {
        render(<Settings/>);
        open('cutting');
        expect(screen.getByText('电脑占用')).toBeTruthy();
        expect(screen.getByRole('button', {name: /留出余量/}).getAttribute('aria-pressed')).toBe('true');
        expect(screen.queryByText(/卡顿、爆音/)).toBeNull();
    });

    it('saves the CPU choice to the service and warns at no limit', async () => {
        render(<Settings/>);
        open('cutting');
        fireEvent.click(screen.getByRole('button', {name: /不限制/}));
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({cpu_usage: 'full'}));
        expect(screen.getByText(/卡顿、爆音.*文件本身没有问题/)).toBeTruthy();
    });

    it('shows the CPU choice the service holds', async () => {
        servicePreferences = {cpu_usage: 'light'};
        render(<Settings/>);
        open('cutting');
        await waitFor(() => expect(screen.getByRole('button', {name: /省着用/}).getAttribute('aria-pressed')).toBe('true'));
    });
    // Requirement (2026-10-14): the processing preset sits beside the CPU
    // share in settings, defaults to picture first, and reaches the service.
    it('offers the processing preset beside the CPU share and saves it', async () => {
        render(<Settings/>);
        open('cutting');
        expect(screen.getByText('处理偏好')).toBeTruthy();
        expect(screen.getByRole('button', {name: /^画质优先/}).getAttribute('aria-pressed')).toBe('true');
        fireEvent.click(screen.getByRole('button', {name: /省空间/}));
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({processing_preset: 'size'}));
    });

    // Review 2026-10-22, item 6: speaker labels switched on while the
    // component is missing can be switched off, here and from the top.
    it('lets speaker labels be switched off when they cannot run', async () => {
        diarization = {available: false, dependency_installed: false, models_local: false, requested_backend: 'sherpa'};
        servicePreferences = {speaker_diarization: true};
        workflow = workflowFrom({attention: [{id: 'speakers', target: 'transcription', action: 'turn_off_speakers', text: {zh: '开了区分讲话人，但组件没装好', en: ''}}]});
        render(<Settings/>);
        open('transcription');
        await waitFor(() => expect(document.getElementById('settingsSpeakerDiarization').checked).toBe(true));
        const box = document.getElementById('settingsSpeakerDiarization');
        expect(box.disabled).toBe(false);
        fireEvent.click(await screen.findByRole('button', {name: '关掉区分讲话人'}));
        await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({speaker_diarization: false}));
    });

    // Item 4: while Claude works, the fallbacks say they are fallbacks and
    // the Anthropic key says it is not used on the subscription.
    it('says the model keys are only a fallback while Claude does the work', async () => {
        workflow = workflowFrom(CLAUDE_AT_WORK);
        render(<Settings/>);
        await screen.findByText('cutting 的现状');
        open('cutting');
        expect(screen.getByRole('button', {name: /备用：Claude 用不了时由看图模型判断/})).toBeTruthy();
        open('accounts');
        expect(await screen.findByText(/这个 Key 用不上/)).toBeTruthy();
        expect(screen.getByText(/Claude 用不了时才用它/)).toBeTruthy();
        expect(screen.getByText(/在 Claude 用不了时才用/)).toBeTruthy();
    });

    // Item 5: a saved key can be removed, after a confirmation, by sending
    // the service an empty value.
    it('removes a saved key after a confirmation', async () => {
        credentials = {deepseek_api_key_configured: true};
        const confirm = vi.spyOn(window, 'confirm');
        render(<Settings/>);
        open('accounts');
        await screen.findByText('DeepSeek API Key');
        confirm.mockReturnValueOnce(false);
        fireEvent.click(screen.getAllByRole('button', {name: '移除'})[0]);
        expect(saveCredentials).not.toHaveBeenCalled();
        confirm.mockReturnValueOnce(true);
        fireEvent.click(screen.getAllByRole('button', {name: '移除'})[0]);
        await waitFor(() => expect(saveCredentials).toHaveBeenCalledWith({deepseek_api_key: ''}));
        confirm.mockRestore();
    });

    it('offers no remove for a key that is not saved', async () => {
        render(<Settings/>);
        open('accounts');
        await screen.findByText('DeepSeek API Key');
        expect(screen.queryByRole('button', {name: '移除'})).toBeNull();
    });

    // Item 9: where "去处理" took the person is kept in the address.
    it('keeps the opened section in the address and opens it again from there', async () => {
        workflow = workflowFrom({attention: [{id: 'lark-app', target: 'accounts', text: {zh: '没填飞书应用', en: ''}}]});
        render(<Settings/>);
        fireEvent.click(await screen.findByRole('button', {name: '去处理'}));
        expect(window.location.hash).toBe('#accounts');
        cleanup();
        window.history.replaceState(null, '', '#step-cutting');
        render(<Settings/>);
        expect(await screen.findByText('电脑占用')).toBeTruthy();
        window.history.replaceState(null, '', '#');
    });

    // Item 7: whether Claude writes the note comes from the service, not the
    // page's own rule (which here would say Claude).
    it('shows the note style only when the service says Claude writes', async () => {
        credentials = {visual_note_available: true};
        workflow = workflowFrom({writer: 'text_model'});
        render(<Settings/>);
        await screen.findByText('cutting 的现状');
        open('notes');
        expect(screen.getByText('给笔记自动配图')).toBeTruthy();
    });

    // A choice still in this browser is moved before the page reads the
    // service, so the page shows it rather than the default it replaces.
    it('shows a choice moved from this browser, not the default', async () => {
        stored = {aiProvider: 'kimi', speakerDiarization: true};
        credentials = {kimi_api_key_configured: true};
        servicePreferences = {};
        savePreferences.mockImplementation(async (patch) => { servicePreferences = {...servicePreferences, ...patch}; return {}; });
        render(<Settings/>);
        open('accounts');
        expect(await screen.findByText('Kimi（月之暗面） API Key')).toBeTruthy();
        expect(savePreferences).toHaveBeenCalledWith({ai_provider: 'kimi', speaker_diarization: true});
        expect('aiProvider' in stored).toBe(false);
        savePreferences.mockImplementation(async (patch) => ({...servicePreferences, ...patch}));
    });
});
