import {describe, expect, it} from 'vitest';
import {localRouteRegistry} from './localRoutes.jsx';
import {
    FORBIDDEN_SURFACE_CAPABILITY_KEYS,
    LOCAL_FRONTEND_CAPABILITIES,
} from './localCapabilities.js';

// This is the local product boundary. Keep it here with the route registry,
// rather than importing a hosted-repository export manifest.
const contract = {
    required_routes: ['/', '/media-text', '/agent', '/editor', '/settings', '/workspace/api', '/about'],
    forbidden_routes: ['/admin', '/auth', '/account', '/guest-trial', '/pricing'],
    forbidden_surfaces: [
        'login or registration', 'guest trial', 'pricing or quota',
        'cloud transcription', 'OSS upload', 'cross-device sync',
        'hosted Feishu OAuth',
    ],
};
const paths = localRouteRegistry.map((entry) => entry.path);

describe('local route registry', () => {
    it('serves every required route', () => {
        for (const route of contract.required_routes) {
            expect(paths, `required local route missing: ${route}`).toContain(route);
        }
    });

    it('registers no forbidden route', () => {
        for (const route of contract.forbidden_routes) {
            const offenders = paths.filter((path) => path === route || path.startsWith(`${route}/`));
            expect(offenders, `forbidden route family present: ${route}`).toEqual([]);
        }
    });

    it('redirects the root straight to the processing workspace', () => {
        const root = localRouteRegistry.find((entry) => entry.path === '/');
        expect(root?.redirectTo).toBe('/media-text');
        // A rendered-but-hidden landing page would be an element, not a
        // declarative redirect.
        expect(root?.element).toBeUndefined();
    });

    it('sends unknown paths back to the workspace', () => {
        const fallback = localRouteRegistry.find((entry) => entry.path === '*');
        expect(fallback?.redirectTo).toBe('/media-text');
    });
});

describe('local capability registry', () => {
    it('declares every forbidden surface as unavailable', () => {
        for (const surface of contract.forbidden_surfaces) {
            const key = FORBIDDEN_SURFACE_CAPABILITY_KEYS[surface];
            expect(key, `unmapped forbidden surface: ${surface}`).toBeTruthy();
            expect(LOCAL_FRONTEND_CAPABILITIES[key], `capability must be false: ${surface}`).toBe(false);
        }
    });

    it('keeps the local shell account-free', () => {
        expect(LOCAL_FRONTEND_CAPABILITIES.accounts).toBe(false);
        expect(LOCAL_FRONTEND_CAPABILITIES.edition).toBe('local');
    });
});
