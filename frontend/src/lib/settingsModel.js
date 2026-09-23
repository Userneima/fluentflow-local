export const SENSITIVE_SETTING_KEYS = ['deepseekApiKey', 'openaiApiKey', 'dashscopeApiKey', 'qwenApiKey', 'larkAppId', 'larkAppSecret'];
// Keys an older build could leave in stored settings. elevenLabsApiKey belonged
// to a cloud transcription engine this app does not have; it is dropped, never sent.
export const LEGACY_REMOVED_SETTING_KEYS = ['hotwordLibrary', 'hotwordLibraries', 'reviewMode', 'reviewUseAi', 'elevenLabsApiKey'];
export const DEFAULT_DEEPSEEK_MODEL = 'deepseek-reasoner';
export const DEFAULT_OPENAI_MODEL = 'gpt-5.4-mini';
export const DEFAULT_QWEN_MODEL = 'qwen3.7-plus';
export const SUPPORTED_FRONTEND_NOTE_MODES = new Set(['auto', 'direct', 'high_fidelity', 'chapter_coverage']);
export const NOTE_MODE_OPTIONS = [
    {value: 'auto', labelEn: 'Auto', labelZh: '自动选择'},
    {value: 'direct', labelEn: 'Direct context', labelZh: '直接上下文'},
    {value: 'high_fidelity', labelEn: 'High-fidelity', labelZh: '高保真笔记'},
    {value: 'chapter_coverage', labelEn: 'Chapter coverage', labelZh: '完整覆盖笔记'},
];

// Which way in the start page opens on. Both entries stay available either way;
// this only decides which one is already selected, because someone who works from
// files on this machine should not have to click past a link box every time.
export const SOURCE_MODES = ['link', 'upload'];
export const DEFAULT_SOURCE_MODE = 'link';

export const normalizeSourceMode = (value) => (
    SOURCE_MODES.includes(String(value || '').trim()) ? String(value).trim() : DEFAULT_SOURCE_MODE
);

export const LARK_EXPORT_ROUTE_OPENAPI = 'openapi';
export const LARK_EXPORT_ROUTE_LOCAL_CLI = 'local_cli';

// Route values an older build could store for a Feishu account-OAuth export.
// This build has no such route, so a stored value remaps to the app-credential
// route instead of leaving an existing user's saved setting on a dead route.
const LEGACY_HOSTED_OAUTH_ROUTE_ALIASES = ['user_oauth', 'feishu_user', 'feishu_user_oauth', 'lark_user_oauth'];

export const normalizeLarkExportRoute = (value, legacyViaCli=false) => {
    const route = String(value || '').trim();
    if (route === LARK_EXPORT_ROUTE_LOCAL_CLI || route === 'lark_cli') return LARK_EXPORT_ROUTE_LOCAL_CLI;
    if (LEGACY_HOSTED_OAUTH_ROUTE_ALIASES.includes(route)) return LARK_EXPORT_ROUTE_OPENAPI;
    if (route === LARK_EXPORT_ROUTE_OPENAPI || route === 'lark_openapi') return LARK_EXPORT_ROUTE_OPENAPI;
    return legacyViaCli ? LARK_EXPORT_ROUTE_LOCAL_CLI : LARK_EXPORT_ROUTE_OPENAPI;
};

export const larkExportRouteFromSettings = (settings={}) => (
    normalizeLarkExportRoute(settings.larkExportRoute, !!settings.larkViaCli)
);

export const isLocalLarkExportRoute = (route) => normalizeLarkExportRoute(route) === LARK_EXPORT_ROUTE_LOCAL_CLI;

export const normalizeAiModel = (provider, model) => {
    const p = provider === 'openai' ? 'openai' : (provider === 'qwen' ? 'qwen' : 'deepseek');
    const value = String(model || '').trim();
    if (p === 'openai') {
        return value && value.startsWith('gpt-') ? value : DEFAULT_OPENAI_MODEL;
    }
    if (p === 'qwen') {
        return value || DEFAULT_QWEN_MODEL;
    }
    return value && value !== 'deepseek-chat' ? value : DEFAULT_DEEPSEEK_MODEL;
};

export const sanitizeSettings = (settings={}) => {
    const next = {...settings};
    SENSITIVE_SETTING_KEYS.forEach((key) => delete next[key]);
    LEGACY_REMOVED_SETTING_KEYS.forEach((key) => delete next[key]);
    const provider = next.aiProvider === 'openai' ? 'openai' : (next.aiProvider === 'qwen' ? 'qwen' : 'deepseek');
    next.aiProvider = provider;
    next.aiModel = normalizeAiModel(provider, next.aiModel);
    if (!SUPPORTED_FRONTEND_NOTE_MODES.has(next.noteMode)) {
        next.noteMode = 'auto';
    }
    next.sttModel = normalizeSttModel(next.sttModel);
    // Speaker separation defaults to on for every material: a meeting is the
    // case that needs it, nobody opens Settings before uploading one, and a
    // single-speaker recording just yields one speaker. Only the absent key is
    // filled in — someone who switched it off stays switched off.
    next.speakerDiarization = next.speakerDiarization === undefined || next.speakerDiarization === null
        ? true
        : !!next.speakerDiarization;
    next.defaultSourceMode = normalizeSourceMode(next.defaultSourceMode);
    next.larkExportRoute = larkExportRouteFromSettings(next);
    next.larkViaCli = isLocalLarkExportRoute(next.larkExportRoute);
    return next;
};

export const sensitivePatchFromSettings = (settings={}) => ({
    deepseek_api_key: settings.deepseekApiKey || '',
    openai_api_key: settings.openaiApiKey || '',
    dashscope_api_key: settings.dashscopeApiKey || settings.qwenApiKey || '',
    lark_app_id: settings.larkAppId || '',
    lark_app_secret: settings.larkAppSecret || '',
});

export const noteModeLabel = (mode, lang) => {
    const found = NOTE_MODE_OPTIONS.find((item) => item.value === (mode || 'auto'));
    return found ? (lang === 'zh' ? found.labelZh : found.labelEn) : (mode || 'auto');
};

export const DEFAULT_STT_MODEL = 'large-v3';

// Transcription always runs locally with one model, so there is no model
// choice to negotiate; the transcription route itself lives in lib/sttPolicy.js.
export const normalizeSttModel = (_model) => (
    DEFAULT_STT_MODEL
);
