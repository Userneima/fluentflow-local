// Edition STT policy seam.
//
// The DEFAULT policy is the local edition: exactly one transcription route
// (local faster-whisper) and no cloud-provider concept anywhere in this
// module. The hosted composition root restores the historical hosted behavior
// by calling `registerSttPolicy(HOSTED_STT_POLICY)` at boot (app.jsx), before
// the first render, so every consumer below behaves identically on the hosted
// product. Pages import these functions (via shared.jsx) and never name a
// cloud provider in their own source.

const LOCAL_PROVIDER = 'local';

const localRuntimeConfig = (config = {}) => ({
    publicMode: false,
    allowedSttProviders: [LOCAL_PROVIDER],
    defaultSttProvider: LOCAL_PROVIDER,
    showMaintainerSettings: config.show_maintainer_settings !== false,
    limits: config.limits || {},
    guestTrial: {enabled: false},
    jobRetryFromStoredSource: config.features?.job_retry_from_stored_source === true,
    directOssUpload: false,
});

const LOCAL_STT_POLICY = {
    normalizeSttProvider: () => LOCAL_PROVIDER,
    isCloudSttProvider: () => false,
    isCloudSttConfigured: () => true,
    effectiveSttProvider: () => LOCAL_PROVIDER,
    cloudSttMissingMessage: () => '',
    defaultRuntimeConfig: () => localRuntimeConfig(),
    normalizeRuntimeConfig: localRuntimeConfig,
    sttRouteOptions: ({lang, t}) => [{
        value: LOCAL_PROVIDER,
        label: t('set.providerLocal'),
        description: lang === 'zh'
            ? '转录在本机完成，音频不会离开这台设备。'
            : 'Transcription runs on this device; audio never leaves it.',
        disabled: false,
    }],
    sttProviderLabel: (provider, lang) => (
        String(provider || '') === LOCAL_PROVIDER
            ? (lang === 'zh' ? '本地转写' : 'Local STT')
            : null
    ),
};

let sttPolicy = LOCAL_STT_POLICY;

// Call with no argument to reset to the local default (tests rely on this).
export const registerSttPolicy = (policy = {}) => {
    sttPolicy = {...LOCAL_STT_POLICY, ...policy};
};

export const normalizeSttProvider = (provider) => sttPolicy.normalizeSttProvider(provider);
export const isCloudSttProvider = (provider) => sttPolicy.isCloudSttProvider(provider);
export const isCloudSttConfigured = (provider, status) => sttPolicy.isCloudSttConfigured(provider, status);
export const effectiveSttProvider = (settings = {}, runtimeConfig) => (
    sttPolicy.effectiveSttProvider(settings, runtimeConfig || sttPolicy.defaultRuntimeConfig())
);
export const cloudSttMissingMessage = (lang) => sttPolicy.cloudSttMissingMessage(lang);
export const defaultRuntimeConfig = () => sttPolicy.defaultRuntimeConfig();
export const normalizeRuntimeConfig = (config = {}) => sttPolicy.normalizeRuntimeConfig(config);
// Options for the settings-page transcription picker: [{value, label,
// description, disabled}]. ctx is {lang, t, runtimeConfig}.
export const sttRouteOptions = (ctx) => sttPolicy.sttRouteOptions(ctx);
// Display label for a stored provider value, or null when unknown (callers
// show their own fallback copy).
export const sttProviderLabel = (provider, lang) => sttPolicy.sttProviderLabel(provider, lang);
