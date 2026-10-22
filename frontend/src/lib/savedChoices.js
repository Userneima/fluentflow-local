import {normalizeTextProvider, textProviderKeyConfigured} from './textProviders.js';

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

// The service's preferences as settings-shaped choices. `textModel` is what
// the service says a task naming no model will use (GET /preferences
// text_model): the saved provider, or the first with a key when that one has
// none. The page shows the saved provider when there is one, and the one in
// use otherwise; it never guesses a default of its own.
export const choicesFromPreferences = (preferences = {}, textModel = null) => {
    const saved = String(preferences?.ai_provider || '');
    const inUse = String(textModel?.provider || '');
    return {
        aiProvider: normalizeTextProvider(saved || inUse),
        aiModel: String(saved ? (preferences?.ai_model || '') : (textModel?.model || '')),
        // Who will actually write a text-model note; the start page and the
        // confirm dialogs name this one.
        aiProviderInUse: normalizeTextProvider(inUse || saved),
        aiModelInUse: String(inUse ? (textModel?.model || '') : (preferences?.ai_model || '')),
        speakerDiarization: !!preferences?.speaker_diarization,
        exportToLark: !!preferences?.export_to_lark,
    };
};

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
// not have yet. The provider and its model go together: when the service has
// either, neither is copied, so a model is never paired with another
// provider. A provider without a key is not copied either: older pages wrote
// DeepSeek into every browser whether or not it was chosen, and a saved
// provider decides who writes (decision 6.31). Null when the service could not
// be asked.
export const planChoiceMigration = ({preferences, stored, providerHasKey = () => false}) => {
    if (!preferences || typeof preferences !== 'object') return null;
    const has = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
    const patch = {};
    for (const key of ['speakerDiarization', 'exportToLark']) {
        const pref = SAVED_CHOICE_PREFS[key];
        if (has(stored, key) && !has(preferences, pref)) patch[pref] = stored[key];
    }
    const serviceHasModel = has(preferences, 'ai_provider') || has(preferences, 'ai_model');
    const provider = String(stored?.aiProvider || '').trim().toLowerCase();
    if (!serviceHasModel && provider && providerHasKey(provider)) {
        patch.ai_provider = provider;
        if (has(stored, 'aiModel')) patch.ai_model = stored.aiModel;
    }
    return patch;
};

// Copy once, then drop the browser's copy. Never throws. A service that
// cannot be reached leaves the browser's copy for the next start; a value the
// service refuses (an unknown provider) is dropped and the rest are saved.
export const migrateSavedChoices = async ({getPreferences, savePreferences, getCredentialsStatus, loadSettings, saveSettings}) => {
    const stored = loadSettings?.() || {};
    if (!SAVED_CHOICE_KEYS.some((key) => Object.prototype.hasOwnProperty.call(stored, key))) return {migrated: false};
    let preferences = null;
    let credentials = null;
    try {
        preferences = await getPreferences?.();
        credentials = await getCredentialsStatus?.();
    } catch (_) {
        return {migrated: false};
    }
    if (!credentials) return {migrated: false};
    const patch = planChoiceMigration({
        preferences,
        stored,
        providerHasKey: (provider) => textProviderKeyConfigured(credentials, provider),
    });
    if (!patch) return {migrated: false};
    const copied = {};
    if (Object.keys(patch).length > 0) {
        try {
            await savePreferences?.(patch);
            Object.assign(copied, patch);
        } catch (_) {
            // One value at a time, so one the service refuses does not keep
            // the others here forever. The provider and its model stay together.
            const groups = Object.keys(patch).filter((key) => key !== 'ai_model')
                .map((key) => (key === 'ai_provider' && 'ai_model' in patch
                    ? {ai_provider: patch.ai_provider, ai_model: patch.ai_model}
                    : {[key]: patch[key]}));
            let reachable = false;
            for (const group of groups) {
                try {
                    await savePreferences?.(group);
                    Object.assign(copied, group);
                    reachable = true;
                } catch (err) {
                    if (!/^HTTP 4|不支持/.test(String(err?.message || ''))) continue;
                    reachable = true;
                }
            }
            if (!reachable) return {migrated: false};
        }
    }
    const next = {...(loadSettings?.() || stored)};
    SAVED_CHOICE_KEYS.forEach((key) => delete next[key]);
    saveSettings?.(next);
    return {migrated: true, copied};
};

// The saved choices as settings, after moving any copy still in this browser:
// a page that reads them must not read the service before the move, or it
// shows the defaults for choices that are about to arrive.
export const loadSavedChoices = async (sync) => {
    await migrateSavedChoices(sync);
    const textModel = sync.getTextModelInUse ? await sync.getTextModelInUse().catch(() => null) : null;
    return choicesFromPreferences(await sync.getPreferences(), textModel);
};
