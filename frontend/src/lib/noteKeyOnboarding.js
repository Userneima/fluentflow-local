// The first-run note setup: when it opens by itself, and what it offers.
//
// A new install transcribes with no setup at all but writes no note until the
// person brings a model key of their own, and the start page used to say only
// "no key, no note". The setup explains what a key is, where to get one and
// roughly what it costs, then saves and checks it in place.
//
// It opens by itself once: never after the person has closed it, and never
// once any model key is saved (even one for a provider not currently picked —
// that person already knows what a key is; the banner and Settings cover the
// rest).

export const NOTE_KEY_ONBOARDING_DISMISSED = 'fluentflow_note_key_onboarding_dismissed';

const MODEL_KEY_FIELDS = [
    'deepseek_api_key_configured',
    'openai_api_key_configured',
    'dashscope_api_key_configured',
    'qwen_api_key_configured',
    'anthropic_api_key_configured',
];

export const anyModelKeyConfigured = (credentialStatus) => (
    MODEL_KEY_FIELDS.some((field) => !!(credentialStatus || {})[field])
);

const storage = () => {
    try { return typeof window !== 'undefined' ? window.localStorage : null; } catch (_) { return null; }
};

export const noteKeyOnboardingDismissed = () => {
    try { return storage()?.getItem(NOTE_KEY_ONBOARDING_DISMISSED) === '1'; } catch (_) { return false; }
};

export const dismissNoteKeyOnboarding = () => {
    try { storage()?.setItem(NOTE_KEY_ONBOARDING_DISMISSED, '1'); } catch (_) { /* private mode: it just opens again */ }
};

// Whether the start page opens the setup by itself. `credentialStatus` is null
// until /credentials/status has answered, and nothing is decided before that.
export const shouldAutoOpenNoteKeyOnboarding = ({writer, credentialStatus, dismissed}) => (
    credentialStatus !== null
    && credentialStatus !== undefined
    && writer?.kind === 'none'
    && !anyModelKeyConfigured(credentialStatus)
    && !dismissed
);

// The two ways to get a note, recommended first. Anthropic is offered only when
// this build writes the note from the frames itself; otherwise its key would
// not make a note at all. Links are the ones the settings page already uses
// for creating keys, plus each provider's own pricing page.
export const noteKeyChoices = ({writesItsOwnNote = false} = {}) => {
    const choices = [
        {
            id: 'deepseek',
            secretKey: 'deepseek_api_key',
            title: 'DeepSeek（推荐先用这个）',
            what: '只根据文字写笔记。',
            cost: '按用量计费，录像越长花得越多；每次具体多少以官方价格页为准。',
            keyUrl: 'https://platform.deepseek.com/api_keys',
            pricingUrl: 'https://api-docs.deepseek.com/quick_start/pricing',
        },
    ];
    if (writesItsOwnNote) {
        choices.push({
            id: 'anthropic',
            secretKey: 'anthropic_api_key',
            title: 'Anthropic（Claude，结合画面写）',
            what: '会看录像里的截图（比如幻灯片、白板）再写，效果更好。',
            cost: '按用量计费，每份笔记明显比 DeepSeek 贵，具体看官方价格。',
            keyUrl: 'https://console.anthropic.com/settings/keys',
            pricingUrl: 'https://www.anthropic.com/pricing',
        });
    }
    return choices;
};

export const NOTE_KEY_INTRO = '转录在本机完成、不花钱；笔记要用一个大模型来写，需要你自己的 API Key（相当于这个模型服务的账号密码，费用记在你自己的账户上）。';
export const NOTE_KEY_DONE = '好了，处理完的录像会自动写笔记。';
