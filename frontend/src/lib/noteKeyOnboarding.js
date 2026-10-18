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

import {TEXT_PROVIDERS} from './textProviders.js';
import {mcpServerCommand} from './noteWriter.js';

export const NOTE_KEY_ONBOARDING_DISMISSED = 'fluentflow_note_key_onboarding_dismissed';

const MODEL_KEY_FIELDS = [
    ...TEXT_PROVIDERS.flatMap((item) => item.configuredFlags),
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

// The model keys that make FluentFlow write the note itself, recommended
// first. The person's own agent comes before all of them on the card (owner,
// 2026-10-18); among keys Qwen leads, since most users in China cannot get an
// Anthropic key. Anthropic is offered only when this build writes the note
// from the frames itself; otherwise its key would not make a note at all.
// Pricing pages are linked only where the address was checked.
const PRICING_URLS = {
    deepseek: 'https://api-docs.deepseek.com/quick_start/pricing',
};

export const noteKeyChoices = ({writesItsOwnNote = false} = {}) => {
    const choices = TEXT_PROVIDERS.filter((item) => item.id !== 'openai').map((item) => ({
        id: item.id,
        secretKey: item.secretKey,
        label: item.labelZh,
        title: item.recommended ? `${item.labelZh}（推荐）` : item.labelZh,
        what: '只根据说出来的话写笔记，不看画面。',
        cost: '按用量计费，录像越长花得越多；具体以官方价格为准。',
        keyUrl: item.consoleUrl,
        pricingUrl: PRICING_URLS[item.id] || '',
        textProvider: true,
    }));
    if (writesItsOwnNote) {
        choices.push({
            id: 'anthropic',
            secretKey: 'anthropic_api_key',
            label: 'Anthropic',
            title: 'Anthropic（Claude，结合画面写）',
            what: '会看录像里的截图（比如幻灯片、白板）再写，效果更好；国内账号一般较难申请。',
            cost: '按用量计费，每份笔记明显比上面几家贵，具体看官方价格。',
            keyUrl: 'https://console.anthropic.com/settings/keys',
            pricingUrl: 'https://www.anthropic.com/pricing',
            textProvider: false,
        });
    }
    return choices;
};

export const NOTE_KEY_INTRO = '转录在本机完成、不花钱。笔记可以交给你自己的 agent 用它的订阅来写，也可以填一个模型的 API Key，让 FluentFlow 自己写（费用记在你自己的账户上）。';

// What the card hands over for the agent route: connect this FluentFlow once,
// then give it recordings. ``connection`` is the service's own paths
// (runtime-config); without them the agent is pointed at the Agent 接入 page.
export const agentSetupPrompt = (connection = {}) => {
    const server = mcpServerCommand(connection);
    const connect = server
        ? `把本机的 FluentFlow 接入成 MCP 服务，名字叫 fluentflow，命令是 ${server.command}，参数是 ${server.script}，不用填令牌。`
        : '把本机的 FluentFlow 接入成 MCP 服务（FluentFlow 设置页的「Agent 接入」里有命令）。';
    return `${connect}接好后，我给你视频链接或本机的录像，你用 FluentFlow 提交、等它转录完，再用 get_agent_note_material 取材料、按里面的要求写笔记、用 save_agent_note 存回去。`;
};
export const NOTE_KEY_DONE = '好了，处理完的录像会自动写笔记。';
