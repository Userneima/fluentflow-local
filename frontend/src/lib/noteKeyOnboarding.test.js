// @vitest-environment jsdom

// Requirements for the first-run note setup, written from the user problem
// (a new user must bring a model key and was never told what that is, where to
// get one, or what it costs):
//   1. It opens by itself only when nothing can write a note, nothing has been
//      saved yet, and the person has not closed it before.
//   2. It explains "API Key" once, offers DeepSeek first as the recommended
//      choice, and offers Claude only where a Claude key actually yields a note.
//   3. It states no price figure (none has been measured); the cost line says it
//      is billed by usage, and every link is a provider page the settings page already uses or the
//      provider's own pricing page.

import {afterEach, describe, expect, it} from 'vitest';
import {
    NOTE_KEY_INTRO,
    anyModelKeyConfigured,
    dismissNoteKeyOnboarding,
    noteKeyChoices,
    agentSetupPrompt,
    noteKeyOnboardingDismissed,
    shouldAutoOpenNoteKeyOnboarding,
} from './noteKeyOnboarding.js';

const NONE = {kind: 'none'};

describe('when the note setup opens by itself', () => {
    afterEach(() => localStorage.clear());

    it('opens on a fresh install with no key', () => {
        expect(shouldAutoOpenNoteKeyOnboarding({writer: NONE, credentialStatus: {}, dismissed: false})).toBe(true);
    });

    it('waits until the key status is known', () => {
        expect(shouldAutoOpenNoteKeyOnboarding({writer: NONE, credentialStatus: null, dismissed: false})).toBe(false);
    });

    it('stays closed once the person has closed it', () => {
        dismissNoteKeyOnboarding();
        expect(noteKeyOnboardingDismissed()).toBe(true);
        expect(shouldAutoOpenNoteKeyOnboarding({writer: NONE, credentialStatus: {}, dismissed: noteKeyOnboardingDismissed()})).toBe(false);
    });

    it('stays closed once any model key is saved, even for a provider not picked', () => {
        const status = {openai_api_key_configured: true};
        expect(anyModelKeyConfigured(status)).toBe(true);
        expect(shouldAutoOpenNoteKeyOnboarding({writer: NONE, credentialStatus: status, dismissed: false})).toBe(false);
    });

    it('stays closed when something already writes the note', () => {
        expect(shouldAutoOpenNoteKeyOnboarding({writer: {kind: 'claude'}, credentialStatus: {}, dismissed: false})).toBe(false);
    });
});

describe('what the note setup says', () => {
    it("offers the person's own agent and their own key, and says who pays", () => {
        expect(NOTE_KEY_INTRO).toContain('agent');
        expect(NOTE_KEY_INTRO).toContain('API Key');
        expect(NOTE_KEY_INTRO).toContain('自己的账户');
    });

    // Owner, 2026-10-18: most users in China cannot get an Anthropic key.
    it('puts Qwen first as the recommended key, then DeepSeek, Kimi and MiMo', () => {
        const choices = noteKeyChoices({writesItsOwnNote: false});
        expect(choices.map((c) => c.id)).toEqual(['qwen', 'deepseek', 'kimi', 'mimo']);
        expect(choices[0].title).toContain('推荐');
        expect(choices.slice(1).some((c) => c.title.includes('推荐'))).toBe(false);
    });

    it('offers Claude only when this build writes notes from the frames, last, and says it is hard to get', () => {
        const ids = noteKeyChoices({writesItsOwnNote: true}).map((c) => c.id);
        expect(ids[ids.length - 1]).toBe('anthropic');
        expect(noteKeyChoices({writesItsOwnNote: true}).find((c) => c.id === 'anthropic').what).toContain('较难申请');
        expect(noteKeyChoices({writesItsOwnNote: false}).map((c) => c.id)).not.toContain('anthropic');
    });

    it('says it is billed by usage and points to the official prices, with no figure we cannot back up', () => {
        for (const choice of noteKeyChoices({writesItsOwnNote: true})) {
            expect(choice.cost).not.toMatch(/[0-9]/);
            expect(choice.cost).not.toMatch(/[$¥]/);
            expect(choice.cost).toContain('官方价格');
        }
        expect(noteKeyChoices()[0].cost).toContain('按用量计费');
    });

    it("links to each provider's own key page, and to prices only where checked", () => {
        const choices = noteKeyChoices({writesItsOwnNote: true});
        expect(choices.map((c) => c.keyUrl)).toEqual([
            'https://bailian.console.aliyun.com/',
            'https://platform.deepseek.com/api_keys',
            'https://platform.moonshot.cn/console/api-keys',
            'https://platform.xiaomimimo.com/#/console/api-keys',
            'https://console.anthropic.com/settings/keys',
        ]);
        expect(choices.map((c) => c.pricingUrl).filter(Boolean)).toEqual([
            'https://api-docs.deepseek.com/quick_start/pricing',
            'https://www.anthropic.com/pricing',
        ]);
    });

    it("hands the agent this machine's server to connect, with no token", () => {
        const prompt = agentSetupPrompt({repoRoot: '/Users/me/fluentflow-local', pythonExecutable: '/usr/bin/python3'});
        expect(prompt).toContain('/Users/me/fluentflow-local/scripts/fluentflow_mcp_server.py');
        expect(prompt).toContain('get_agent_note_material');
        expect(prompt).not.toMatch(/ACCESS_TOKEN|token=/i);
        expect(agentSetupPrompt({})).toContain('Agent 接入');
    });
});
