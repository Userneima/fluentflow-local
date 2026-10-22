import {normalizeTextProvider} from './textProviders.js';

// The choices the service keeps for every task that does not name its own
// (decision 6.31): the text model that writes notes, speaker labels, auto
// export to Feishu. The page reads and writes them in the service, and no
// longer sends them with a submission, so a task from the page and one from an
// agent get the same ones.
//
// They used to live in this browser's stored settings. On the first start
// after that changed, a stored value is copied to the service where the
// service has none, and the browser's copy is then removed.

// settings key -> preference name
export const SAVED_CHOICE_PREFS = {
    aiProvider: 'ai_provider',
    aiModel: 'ai_model',
    speakerDiarization: 'speaker_diarization',
    exportToLark: 'export_to_lark',
};
export const SAVED_CHOICE_KEYS = Object.keys(SAVED_CHOICE_PREFS);

// The service's preferences as settings-shaped choices. A provider the
// service has not saved reads as the product default, as before.
export const choicesFromPreferences = (preferences = {}) => ({
    aiProvider: normalizeTextProvider(preferences?.ai_provider),
    aiModel: String(preferences?.ai_model || ''),
    speakerDiarization: !!preferences?.speaker_diarization,
    exportToLark: !!preferences?.export_to_lark,
});

// A settings-shaped patch split into what the service keeps (as preference
// names) and what stays in the browser.
export const splitChoicePatch = (patch = {}) => {
    const service = {};
    const local = {};
    for (const [key, value] of Object.entries(patch)) {
        if (SAVED_CHOICE_PREFS[key]) service[SAVED_CHOICE_PREFS[key]] = value;
        else local[key] = value;
    }
    return {service, local};
};

// What to copy to the service: the browser's stored values the service does
// not have yet. Null when the service could not be asked.
export const planChoiceMigration = ({preferences, stored}) => {
    if (!preferences || typeof preferences !== 'object') return null;
    const patch = {};
    for (const [key, pref] of Object.entries(SAVED_CHOICE_PREFS)) {
        if (!Object.prototype.hasOwnProperty.call(stored || {}, key)) continue;
        if (Object.prototype.hasOwnProperty.call(preferences, pref)) continue;
        patch[pref] = stored[key];
    }
    return patch;
};

// Copy once, then drop the browser's copy. Never throws; a service that
// cannot be reached, or a failed save, leaves the browser's copy for the next
// start.
export const migrateSavedChoices = async ({getPreferences, savePreferences, loadSettings, saveSettings}) => {
    const stored = loadSettings?.() || {};
    if (!SAVED_CHOICE_KEYS.some((key) => Object.prototype.hasOwnProperty.call(stored, key))) return {migrated: false};
    let preferences = null;
    try { preferences = await getPreferences?.(); } catch (_) { preferences = null; }
    const patch = planChoiceMigration({preferences, stored});
    if (!patch) return {migrated: false};
    if (Object.keys(patch).length > 0) {
        try { await savePreferences?.(patch); } catch (_) { return {migrated: false}; }
    }
    const next = {...(loadSettings?.() || stored)};
    SAVED_CHOICE_KEYS.forEach((key) => delete next[key]);
    saveSettings?.(next);
    return {migrated: true, copied: patch};
};

// The saved choices as settings, after moving any copy still in this browser:
// a page that reads them must not read the service before the move, or it
// shows the defaults for choices that are about to arrive.
export const loadSavedChoices = async (sync) => {
    await migrateSavedChoices(sync);
    return choicesFromPreferences(await sync.getPreferences());
};
