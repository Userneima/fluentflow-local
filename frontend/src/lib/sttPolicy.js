// Transcription route policy.
//
// There is exactly one transcription route: local faster-whisper. Pages import
// these functions (via shared.jsx) instead of naming a provider themselves.

const LOCAL_PROVIDER = 'local';

const localRuntimeConfig = (config = {}) => ({
    publicMode: false,
    allowedSttProviders: [LOCAL_PROVIDER],
    defaultSttProvider: LOCAL_PROVIDER,
    showMaintainerSettings: config.show_maintainer_settings !== false,
    limits: config.limits || {},
    jobRetryFromStoredSource: config.features?.job_retry_from_stored_source === true,
    // This build writes the note itself, from the cut media, so the pipeline's
    // own note stage never runs. Every setting that only steers that stage steers
    // nothing, and a page offering them is offering controls with no wire behind
    // them. Env-switchable server-side, so it is asked for rather than assumed.
    writesItsOwnNote: config.features?.writes_its_own_note === true,
});

export const normalizeSttProvider = () => LOCAL_PROVIDER;
export const isCloudSttProvider = () => false;
export const isCloudSttConfigured = () => true;
// What to SHOW as selected in the transcription picker.
export const effectiveSttProvider = () => LOCAL_PROVIDER;
// What to SEND when submitting a task. There is one route, so submitting it
// explicitly is always correct and there is no server-side default to defer to.
export const submittedSttProvider = () => LOCAL_PROVIDER;
export const cloudSttMissingMessage = () => '';
export const defaultRuntimeConfig = () => localRuntimeConfig();
export const normalizeRuntimeConfig = (config = {}) => localRuntimeConfig(config);
// Options for the settings-page transcription picker: [{value, label,
// description, disabled}]. ctx is {lang, t, runtimeConfig}.
export const sttRouteOptions = ({lang, t}) => [{
    value: LOCAL_PROVIDER,
    label: t('set.providerLocal'),
    description: lang === 'zh'
        ? '转录在本机完成，音频不会离开这台设备。'
        : 'Transcription runs on this device; audio never leaves it.',
    disabled: false,
}];
// Display label for a stored provider value, or null when unknown (callers
// show their own fallback copy).
export const sttProviderLabel = (provider, lang) => (
    String(provider || '') === LOCAL_PROVIDER
        ? (lang === 'zh' ? '本地转写' : 'Local STT')
        : null
);
