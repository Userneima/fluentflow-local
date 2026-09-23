import {describe, expect, it} from 'vitest';
import {
    LARK_EXPORT_ROUTE_LOCAL_CLI,
    LARK_EXPORT_ROUTE_OPENAPI,
    normalizeLarkExportRoute,
    sanitizeSettings,
} from './settingsModel.js';

describe('Lark export route', () => {
    it('falls back to app credentials and remaps stored OAuth values', () => {
        // Fresh install: fallback default is app credentials, not OAuth.
        expect(normalizeLarkExportRoute('')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        // Previously STORED hosted-OAuth values remap instead of dead-ending.
        expect(normalizeLarkExportRoute('user_oauth')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        expect(normalizeLarkExportRoute('feishu_user_oauth')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        // Explicit local routes are untouched.
        expect(normalizeLarkExportRoute('local_cli')).toBe(LARK_EXPORT_ROUTE_LOCAL_CLI);
        expect(normalizeLarkExportRoute('openapi')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        expect(normalizeLarkExportRoute('', true)).toBe(LARK_EXPORT_ROUTE_LOCAL_CLI);
    });
});

describe('speaker diarization default', () => {
    it('turns speaker separation on when the setting was never set', () => {
        expect(sanitizeSettings({}).speakerDiarization).toBe(true);
    });

    it('keeps an explicit opt-out', () => {
        expect(sanitizeSettings({speakerDiarization: false}).speakerDiarization).toBe(false);
    });

    it('keeps an explicit opt-in', () => {
        expect(sanitizeSettings({speakerDiarization: true}).speakerDiarization).toBe(true);
    });
});
