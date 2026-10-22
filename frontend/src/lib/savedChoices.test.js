// Decision 6.31 (owner, 2026-10-23): the note's text model, speaker labels and
// auto export move from this browser to the service. A value stored here is
// copied only where the service has none, and the browser's copy is then
// removed; a service that cannot be reached leaves everything as it was.

import {describe, expect, it, vi} from 'vitest';
import {migrateSavedChoices, splitChoicePatch} from './savedChoices.js';

const harness = ({stored, preferences, saveFails = false}) => {
    let settings = {...stored};
    const savePreferences = vi.fn(async () => { if (saveFails) throw new Error('down'); return {}; });
    return {
        sync: {
            getPreferences: async () => { if (preferences === null) throw new Error('down'); return preferences; },
            savePreferences,
            loadSettings: () => settings,
            saveSettings: (next) => { settings = next; },
        },
        savePreferences,
        settings: () => settings,
    };
};

describe('moving the saved choices to the service', () => {
    it('copies only what the service does not have, then drops the browser copy', async () => {
        const h = harness({
            stored: {aiProvider: 'kimi', aiModel: 'kimi-x', speakerDiarization: true, exportToLark: true, noteMode: 'auto'},
            preferences: {ai_provider: 'qwen', export_to_lark: false},
        });
        await migrateSavedChoices(h.sync);
        expect(h.savePreferences).toHaveBeenCalledWith({ai_model: 'kimi-x', speaker_diarization: true});
        expect(h.settings()).toEqual({noteMode: 'auto'});
    });

    it('copies nothing when the service already has every choice', async () => {
        const h = harness({
            stored: {aiProvider: 'kimi', speakerDiarization: true},
            preferences: {ai_provider: 'qwen', ai_model: '', speaker_diarization: false, export_to_lark: false},
        });
        await migrateSavedChoices(h.sync);
        expect(h.savePreferences).not.toHaveBeenCalled();
        expect(h.settings()).toEqual({});
    });

    it('keeps the browser copy when the service cannot be reached or the save fails', async () => {
        const unreachable = harness({stored: {aiProvider: 'kimi'}, preferences: null});
        await migrateSavedChoices(unreachable.sync);
        expect(unreachable.settings()).toEqual({aiProvider: 'kimi'});
        const failing = harness({stored: {aiProvider: 'kimi'}, preferences: {}, saveFails: true});
        await migrateSavedChoices(failing.sync);
        expect(failing.settings()).toEqual({aiProvider: 'kimi'});
    });

    it('sends a changed choice to the service and keeps the rest in the browser', () => {
        expect(splitChoicePatch({aiProvider: 'qwen', aiModel: '', voiceEnhance: true})).toEqual({
            service: {ai_provider: 'qwen', ai_model: ''},
            local: {voiceEnhance: true},
        });
    });
});
