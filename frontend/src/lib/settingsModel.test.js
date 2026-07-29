import {afterEach, describe, expect, it} from 'vitest';
import {
    LARK_EXPORT_ROUTE_LOCAL_CLI,
    LARK_EXPORT_ROUTE_OPENAPI,
    configureLarkExportRoutes,
    extraLarkExportRouteOptions,
    isUserOAuthLarkExportRoute,
    isUserOAuthLarkRouteAvailable,
    normalizeLarkExportRoute,
} from './settingsModel.js';

afterEach(() => {
    configureLarkExportRoutes();
});

describe('local Lark route policy (default)', () => {
    it('declares app credentials as the fallback with no OAuth route', () => {
        expect(isUserOAuthLarkRouteAvailable()).toBe(false);
        expect(extraLarkExportRouteOptions()).toEqual([]);
        // Fresh install: fallback default is app credentials, not OAuth.
        expect(normalizeLarkExportRoute('')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        // Previously STORED hosted-OAuth values remap instead of dead-ending.
        expect(normalizeLarkExportRoute('user_oauth')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        expect(normalizeLarkExportRoute('feishu_user_oauth')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        expect(isUserOAuthLarkExportRoute('user_oauth')).toBe(false);
        // Explicit local routes are untouched.
        expect(normalizeLarkExportRoute('local_cli')).toBe(LARK_EXPORT_ROUTE_LOCAL_CLI);
        expect(normalizeLarkExportRoute('openapi')).toBe(LARK_EXPORT_ROUTE_OPENAPI);
        expect(normalizeLarkExportRoute('', true)).toBe(LARK_EXPORT_ROUTE_LOCAL_CLI);
    });
});

describe('hosted Lark route policy (registered)', () => {
    const HOSTED = {
        fallbackRoute: 'user_oauth',
        userOAuthRoute: 'user_oauth',
        userOAuthOption: {labelZh: '标签', labelEn: 'Label', hintZh: '提示', hintEn: 'Hint'},
    };

    it('keeps the historical account-OAuth fallback', () => {
        configureLarkExportRoutes(HOSTED);
        expect(isUserOAuthLarkRouteAvailable()).toBe(true);
        expect(normalizeLarkExportRoute('')).toBe('user_oauth');
        expect(normalizeLarkExportRoute('user_oauth')).toBe('user_oauth');
        expect(normalizeLarkExportRoute('feishu_user')).toBe('user_oauth');
        expect(normalizeLarkExportRoute('', true)).toBe(LARK_EXPORT_ROUTE_LOCAL_CLI);
        expect(isUserOAuthLarkExportRoute('user_oauth')).toBe(true);
        expect(isUserOAuthLarkExportRoute('openapi')).toBe(false);
        expect(extraLarkExportRouteOptions()).toEqual([
            {value: 'user_oauth', labelZh: '标签', labelEn: 'Label', hintZh: '提示', hintEn: 'Hint'},
        ]);
    });
});
