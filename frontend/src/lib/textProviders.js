// The text-model providers a note can be written with, in the order the page
// offers them; the backend's twin is backend/core/text_providers.py. Qwen
// comes first (owner, 2026-10-18): most users in China cannot get an Anthropic
// key, and Qwen, DeepSeek, Kimi and MiMo are what they have.
export const TEXT_PROVIDERS = [
    {
        id: 'qwen',
        labelZh: '通义千问（阿里云百炼）',
        labelEn: 'Qwen (Alibaba Cloud Bailian)',
        // The stored key's name; the older qwen_api_key is the same key.
        secretKey: 'dashscope_api_key',
        configuredFlags: ['dashscope_api_key_configured', 'qwen_api_key_configured'],
        consoleUrl: 'https://bailian.console.aliyun.com/',
        recommended: true,
    },
    {
        id: 'deepseek',
        labelZh: 'DeepSeek',
        labelEn: 'DeepSeek',
        secretKey: 'deepseek_api_key',
        configuredFlags: ['deepseek_api_key_configured'],
        consoleUrl: 'https://platform.deepseek.com/api_keys',
    },
    {
        id: 'kimi',
        labelZh: 'Kimi（月之暗面）',
        labelEn: 'Kimi (Moonshot AI)',
        secretKey: 'kimi_api_key',
        configuredFlags: ['kimi_api_key_configured'],
        consoleUrl: 'https://platform.moonshot.cn/console/api-keys',
    },
    {
        id: 'mimo',
        labelZh: '小米 MiMo',
        labelEn: 'Xiaomi MiMo',
        secretKey: 'mimo_api_key',
        configuredFlags: ['mimo_api_key_configured'],
        consoleUrl: 'https://platform.xiaomimimo.com/#/console/api-keys',
    },
    {
        id: 'openai',
        labelZh: 'OpenAI',
        labelEn: 'OpenAI',
        secretKey: 'openai_api_key',
        configuredFlags: ['openai_api_key_configured'],
        consoleUrl: 'https://platform.openai.com/api-keys',
    },
];

// A stored or unknown value lands on the product default, DeepSeek, so a
// setting written by an older build keeps the provider it had.
export const DEFAULT_TEXT_PROVIDER = 'deepseek';

export const textProvider = (id) => (
    TEXT_PROVIDERS.find((item) => item.id === String(id || '').trim().toLowerCase())
    || TEXT_PROVIDERS.find((item) => item.id === DEFAULT_TEXT_PROVIDER)
);

export const normalizeTextProvider = (id) => textProvider(id).id;

export const textProviderLabel = (id, lang = 'zh') => {
    const item = textProvider(id);
    return lang === 'zh' ? item.labelZh : item.labelEn;
};

export const textProviderKeyConfigured = (status, id) => (
    !!status && textProvider(id).configuredFlags.some((flag) => !!status[flag])
);
