export const SENSITIVE_SETTING_KEYS = ['deepseekApiKey', 'openaiApiKey', 'dashscopeApiKey', 'qwenApiKey', 'anthropicApiKey', 'larkAppId', 'larkAppSecret', 'elevenLabsApiKey'];
export const LEGACY_REMOVED_SETTING_KEYS = ['hotwordLibrary', 'hotwordLibraries', 'reviewMode', 'reviewUseAi'];
export const DEFAULT_DEEPSEEK_MODEL = 'deepseek-reasoner';
export const DEFAULT_OPENAI_MODEL = 'gpt-5.4-mini';
export const DEFAULT_QWEN_MODEL = 'qwen3.7-plus';
export const DEFAULT_ANTHROPIC_MODEL = 'claude-opus-5';

// One table per provider instead of a chained ternary in every function that
// needs to know about providers. A ternary chain silently routes an unknown
// value to its last branch, which is how a newly added provider ends up being
// saved as deepseek with a deepseek model name.
export const AI_PROVIDERS = Object.freeze({
    deepseek: {defaultModel: DEFAULT_DEEPSEEK_MODEL, secretKey: 'deepseek_api_key'},
    openai: {defaultModel: DEFAULT_OPENAI_MODEL, secretKey: 'openai_api_key'},
    qwen: {defaultModel: DEFAULT_QWEN_MODEL, secretKey: 'dashscope_api_key'},
    anthropic: {defaultModel: DEFAULT_ANTHROPIC_MODEL, secretKey: 'anthropic_api_key'},
});
export const DEFAULT_AI_PROVIDER = 'deepseek';

export const normalizeAiProvider = (provider) => (
    Object.hasOwn(AI_PROVIDERS, String(provider || '')) ? String(provider) : DEFAULT_AI_PROVIDER
);

export const aiProviderSecretKey = (provider) => AI_PROVIDERS[normalizeAiProvider(provider)].secretKey;
export const SUPPORTED_FRONTEND_NOTE_MODES = new Set(['auto', 'direct', 'high_fidelity', 'chapter_coverage']);
export const NOTE_MODE_OPTIONS = [
    {value: 'auto', labelEn: 'Auto', labelZh: '自动选择'},
    {value: 'direct', labelEn: 'Direct context', labelZh: '直接上下文'},
    {value: 'high_fidelity', labelEn: 'High-fidelity', labelZh: '高保真笔记'},
    {value: 'chapter_coverage', labelEn: 'Chapter coverage', labelZh: '完整覆盖笔记'},
];

export const LARK_EXPORT_ROUTE_OPENAPI = 'openapi';
export const LARK_EXPORT_ROUTE_LOCAL_CLI = 'local_cli';

// Stored values of the hosted account-OAuth export route. These are migration
// inputs only: when no OAuth route is registered (the local default), a
// previously stored value remaps to the fallback so a fresh or migrated local
// install never lands on a dead route.
const LEGACY_HOSTED_OAUTH_ROUTE_ALIASES = ['user_oauth', 'feishu_user', 'feishu_user_oauth', 'lark_user_oauth'];

// Edition-owned Lark route policy. The DEFAULT is the local edition: the
// user's own app credentials are the fallback and no account-OAuth route
// exists. The hosted composition root restores its historical behavior at
// boot by registering HOSTED_LARK_EXPORT_POLICY (hostedSettingsModel.js).
const LOCAL_LARK_ROUTE_POLICY = Object.freeze({
    fallbackRoute: LARK_EXPORT_ROUTE_OPENAPI,
    userOAuthRoute: null,
    userOAuthOption: null,
});

let larkRoutePolicy = LOCAL_LARK_ROUTE_POLICY;

// Full replace; call with no argument to reset to the local default.
export const configureLarkExportRoutes = ({fallbackRoute, userOAuthRoute, userOAuthOption} = {}) => {
    larkRoutePolicy = {
        fallbackRoute: fallbackRoute || LOCAL_LARK_ROUTE_POLICY.fallbackRoute,
        userOAuthRoute: userOAuthRoute || null,
        userOAuthOption: userOAuthOption || null,
    };
};

export const isUserOAuthLarkRouteAvailable = () => !!larkRoutePolicy.userOAuthRoute;

// Extra selectable route options the edition policy registered (today: the
// hosted account-OAuth route with its labels and hint copy).
export const extraLarkExportRouteOptions = () => (
    larkRoutePolicy.userOAuthRoute && larkRoutePolicy.userOAuthOption
        ? [{value: larkRoutePolicy.userOAuthRoute, ...larkRoutePolicy.userOAuthOption}]
        : []
);

export const normalizeLarkExportRoute = (value, legacyViaCli=false) => {
    const route = String(value || '').trim();
    if (route === LARK_EXPORT_ROUTE_LOCAL_CLI || route === 'lark_cli') return LARK_EXPORT_ROUTE_LOCAL_CLI;
    if (LEGACY_HOSTED_OAUTH_ROUTE_ALIASES.includes(route)) {
        return larkRoutePolicy.userOAuthRoute || larkRoutePolicy.fallbackRoute;
    }
    if (route === LARK_EXPORT_ROUTE_OPENAPI || route === 'lark_openapi') return LARK_EXPORT_ROUTE_OPENAPI;
    return legacyViaCli ? LARK_EXPORT_ROUTE_LOCAL_CLI : larkRoutePolicy.fallbackRoute;
};

export const larkExportRouteFromSettings = (settings={}) => (
    normalizeLarkExportRoute(settings.larkExportRoute, !!settings.larkViaCli)
);

export const isLocalLarkExportRoute = (route) => normalizeLarkExportRoute(route) === LARK_EXPORT_ROUTE_LOCAL_CLI;
export const isUserOAuthLarkExportRoute = (route) => (
    !!larkRoutePolicy.userOAuthRoute && normalizeLarkExportRoute(route) === larkRoutePolicy.userOAuthRoute
);

export const normalizeAiModel = (provider, model) => {
    const p = normalizeAiProvider(provider);
    const value = String(model || '').trim();
    if (p === 'openai') {
        return value && value.startsWith('gpt-') ? value : DEFAULT_OPENAI_MODEL;
    }
    if (p === 'anthropic') {
        // Reject a model left over from another provider: sending e.g.
        // deepseek-reasoner to Anthropic is a 404 the user reads as "Claude
        // is broken" rather than "the model box still says deepseek".
        return value && value.startsWith('claude-') ? value : DEFAULT_ANTHROPIC_MODEL;
    }
    if (p === 'deepseek') {
        return value && value !== 'deepseek-chat' ? value : DEFAULT_DEEPSEEK_MODEL;
    }
    return value || AI_PROVIDERS[p].defaultModel;
};

export const sanitizeSettings = (settings={}) => {
    const next = {...settings};
    SENSITIVE_SETTING_KEYS.forEach((key) => delete next[key]);
    LEGACY_REMOVED_SETTING_KEYS.forEach((key) => delete next[key]);
    const provider = normalizeAiProvider(next.aiProvider);
    next.aiProvider = provider;
    next.aiModel = normalizeAiModel(provider, next.aiModel);
    if (!SUPPORTED_FRONTEND_NOTE_MODES.has(next.noteMode)) {
        next.noteMode = 'auto';
    }
    next.sttModel = normalizeSttModel(next.sttModel);
    next.larkExportRoute = larkExportRouteFromSettings(next);
    next.larkViaCli = isLocalLarkExportRoute(next.larkExportRoute);
    return next;
};

export const sensitivePatchFromSettings = (settings={}) => ({
    deepseek_api_key: settings.deepseekApiKey || '',
    openai_api_key: settings.openaiApiKey || '',
    dashscope_api_key: settings.dashscopeApiKey || settings.qwenApiKey || '',
    anthropic_api_key: settings.anthropicApiKey || '',
    lark_app_id: settings.larkAppId || '',
    lark_app_secret: settings.larkAppSecret || '',
    elevenlabs_api_key: settings.elevenLabsApiKey || '',
});

export const noteModeLabel = (mode, lang) => {
    const found = NOTE_MODE_OPTIONS.find((item) => item.value === (mode || 'auto'));
    return found ? (lang === 'zh' ? found.labelZh : found.labelEn) : (mode || 'auto');
};

export const DEFAULT_STT_MODEL = 'medium';

// The cloud STT provider policy lives behind the edition seam in
// `lib/sttPolicy.js` (hosted implementation in the cloud-only
// `hostedSettingsModel.js`). This module keeps only the user-owned settings
// model: AI/Feishu credentials, note modes, Lark route helpers, and the local
// STT model. The local edition always transcribes locally, so it needs no
// cloud provider negotiation here.
export const normalizeSttModel = (_model) => (
    DEFAULT_STT_MODEL
);
