import {afterEach, describe, expect, it} from 'vitest';
import {
    cloudSttMissingMessage,
    defaultRuntimeConfig,
    effectiveSttProvider,
    isCloudSttConfigured,
    isCloudSttProvider,
    normalizeRuntimeConfig,
    normalizeSttProvider,
    registerSttPolicy,
    sttProviderLabel,
    sttRouteOptions,
} from './sttPolicy.js';

const t = (key) => key;

afterEach(() => {
    registerSttPolicy();
});

describe('default policy is the local edition', () => {
    it('knows exactly one transcription route', () => {
        expect(normalizeSttProvider('anything')).toBe('local');
        expect(effectiveSttProvider({sttProvider: 'cloud'}, defaultRuntimeConfig())).toBe('local');
        expect(isCloudSttProvider('cloud')).toBe(false);
        expect(isCloudSttConfigured('cloud', {})).toBe(true);
        expect(cloudSttMissingMessage('zh')).toBe('');
    });

    it('normalizes runtime config to local-only providers', () => {
        const config = normalizeRuntimeConfig({
            allowed_stt_providers: ['cloud', 'local'],
            default_stt_provider: 'cloud',
            features: {job_retry_from_stored_source: true},
            limits: {max_file_mb: 10},
        });
        expect(config.allowedSttProviders).toEqual(['local']);
        expect(config.defaultSttProvider).toBe('local');
        expect(config.publicMode).toBe(false);
        expect(config.guestTrial).toEqual({enabled: false});
        expect(config.jobRetryFromStoredSource).toBe(true);
        expect(config.directOssUpload).toBe(false);
        expect(config.limits).toEqual({max_file_mb: 10});
    });

    it('offers a single local route option and label', () => {
        const options = sttRouteOptions({lang: 'zh', t, runtimeConfig: defaultRuntimeConfig()});
        expect(options).toHaveLength(1);
        expect(options[0].value).toBe('local');
        expect(options[0].disabled).toBe(false);
        expect(sttProviderLabel('local', 'zh')).toBe('本地转写');
        // Unknown providers get no label; pages show their own fallback copy.
        expect(sttProviderLabel('cloud', 'zh')).toBe(null);
    });
});

describe('policy registration', () => {
    it('lets a composition root replace the policy and reset restores local', () => {
        registerSttPolicy({
            isCloudSttProvider: (provider) => provider === 'cloud',
            sttProviderLabel: () => 'Registered',
        });
        expect(isCloudSttProvider('cloud')).toBe(true);
        expect(sttProviderLabel('x', 'en')).toBe('Registered');
        // Unregistered functions keep the local default.
        expect(normalizeSttProvider('cloud')).toBe('local');
        registerSttPolicy();
        expect(isCloudSttProvider('cloud')).toBe(false);
    });
});
