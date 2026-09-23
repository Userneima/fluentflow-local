import {describe, expect, it} from 'vitest';
import {
    defaultRuntimeConfig,
    effectiveSttProvider,
    normalizeRuntimeConfig,
    normalizeSttProvider,
    sttProviderLabel,
    sttRouteOptions,
} from './sttPolicy.js';

const t = (key) => key;

describe('local transcription policy', () => {
    it('knows exactly one transcription route', () => {
        expect(normalizeSttProvider('anything')).toBe('local');
        expect(effectiveSttProvider({sttProvider: 'cloud'}, defaultRuntimeConfig())).toBe('local');
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
        expect(config.jobRetryFromStoredSource).toBe(true);
        expect(config.limits).toEqual({max_file_mb: 10});
    });

    it('treats a cloud engine left in old stored settings as local', () => {
        for (const legacy of ['elevenlabs', 'dashscope', 'cloud']) {
            expect(normalizeSttProvider(legacy)).toBe('local');
            expect(effectiveSttProvider({sttProvider: legacy}, defaultRuntimeConfig())).toBe('local');
        }
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
