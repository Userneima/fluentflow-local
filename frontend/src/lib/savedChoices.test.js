// Decision 6.31 (owner, 2026-10-23): the note's text model, speaker labels and
// auto export move from this browser to the service. A value stored here is
// copied only where the service has none, and the browser's copy is then
// removed; a service that cannot be reached leaves everything as it was.
// Review 2026-10-23: a provider is copied only when it has a key (older pages
// wrote DeepSeek into every browser); a provider and its model go together; a
// value the service refuses does not hold the others back. The page shows
// the provider the service says it will use, not a default of its own.

import {describe, expect, it, vi} from 'vitest';
import {choicesFromPreferences, migrateSavedChoices, splitChoicePatch} from './savedChoices.js';

const harness = ({stored, preferences, credentials = {}, refuse = null, down = false}) => {
    let settings = {...stored};
    let saved = {...(preferences || {})};
    const savePreferences = vi.fn(async (patch) => {
        if (down) throw new Error('Failed to fetch');
        if (refuse && Object.keys(patch).includes(refuse)) throw new Error('不支持的服务商：x');
        saved = {...saved, ...patch};
        return {};
    });
    return {
        sync: {
            getPreferences: async () => { if (preferences === null) throw new Error('down'); return saved; },
            getCredentialsStatus: async () => credentials,
            savePreferences,
            loadSettings: () => settings,
            saveSettings: (next) => { settings = next; },
        },
        savePreferences,
        settings: () => settings,
        saved: () => saved,
    };
};

describe('moving the saved choices to the service', () => {
    it('copies only what the service does not have, then drops the browser copy', async () => {
        const h = harness({
            stored: {aiProvider: 'kimi', aiModel: 'kimi-x', speakerDiarization: true, exportToLark: true, noteMode: 'auto'},
            preferences: {export_to_lark: false},
            credentials: {kimi_api_key_configured: true},
        });
        await migrateSavedChoices(h.sync);
        expect(h.saved()).toEqual({export_to_lark: false, speaker_diarization: true, ai_provider: 'kimi', ai_model: 'kimi-x'});
        expect(h.settings()).toEqual({noteMode: 'auto'});
    });

    it('does not copy a provider that has no key, and drops it from the browser', async () => {
        const h = harness({stored: {aiProvider: 'deepseek', speakerDiarization: false}, preferences: {}, credentials: {kimi_api_key_configured: true}});
        await migrateSavedChoices(h.sync);
        expect(h.saved()).toEqual({speaker_diarization: false});
        expect(h.settings()).toEqual({});
    });

    it('never pairs a model with another provider', async () => {
        const h = harness({
            stored: {aiProvider: 'openai', aiModel: 'gpt-4.1-mini'},
            preferences: {ai_provider: 'kimi'},
            credentials: {openai_api_key_configured: true, kimi_api_key_configured: true},
        });
        await migrateSavedChoices(h.sync);
        expect(h.saved()).toEqual({ai_provider: 'kimi'});
    });

    it('saves the rest when the service refuses one value', async () => {
        const h = harness({
            stored: {aiProvider: 'kimi', speakerDiarization: true},
            preferences: {},
            credentials: {kimi_api_key_configured: true},
            refuse: 'ai_provider',
        });
        await migrateSavedChoices(h.sync);
        expect(h.saved()).toEqual({speaker_diarization: true});
        expect(h.settings()).toEqual({});
    });

    it('keeps the browser copy when the service cannot be reached', async () => {
        const unreachable = harness({stored: {aiProvider: 'kimi'}, preferences: null});
        await migrateSavedChoices(unreachable.sync);
        expect(unreachable.settings()).toEqual({aiProvider: 'kimi'});
        const down = harness({stored: {speakerDiarization: true}, preferences: {}, down: true});
        await migrateSavedChoices(down.sync);
        expect(down.settings()).toEqual({speakerDiarization: true});
    });

    it('sends a changed choice to the service and keeps the rest in the browser', () => {
        expect(splitChoicePatch({aiProvider: 'qwen', aiModel: '', voiceEnhance: true})).toEqual({
            service: {ai_provider: 'qwen', ai_model: ''},
            local: {voiceEnhance: true},
        });
    });
});

describe('what the page shows', () => {
    it('shows the provider the service will use when none is saved', () => {
        const shown = choicesFromPreferences({}, {provider: 'kimi', model: ''});
        expect(shown.aiProvider).toBe('kimi');
        expect(shown.aiProviderInUse).toBe('kimi');
    });

    it('shows the saved provider, and names the one in use when that has no key', () => {
        const shown = choicesFromPreferences({ai_provider: 'deepseek'}, {provider: 'kimi', model: ''});
        expect(shown.aiProvider).toBe('deepseek');
        expect(shown.aiProviderInUse).toBe('kimi');
    });
});
