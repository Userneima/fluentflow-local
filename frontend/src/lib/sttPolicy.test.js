import {describe, expect, it} from 'vitest';
import {
    defaultRuntimeConfig,
    effectiveSttProvider,
    normalizeRuntimeConfig,
    normalizeSttProvider,
} from './sttPolicy.js';

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
});
