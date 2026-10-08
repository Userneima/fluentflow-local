import {describe, expect, it, vi} from 'vitest';
import {
    COOKIES_MIGRATED_STORAGE_KEY,
    allowMiuistoreFromPreferences,
    syncVideoLinkPreferences,
} from './videoLinkPrefs.js';

// Written from the requirement: the browser-login choice for video links is
// kept by the local service, so a link an AI tool submits uses it too. Someone
// who picked a browser before this change must not have to pick it again, and
// the old stored value must be copied over once, not on every start.

const memoryStorage = (initial = {}) => {
    const data = {...initial};
    return {
        getItem: (key) => (key in data ? data[key] : null),
        setItem: (key, value) => { data[key] = String(value); },
        data,
    };
};

const harness = ({preferences, settings = {}, storage = memoryStorage(), failGet = false, failSave = false}) => {
    let stored = {...settings};
    const savePreferences = vi.fn(async (patch) => {
        if (failSave) throw new Error('HTTP 500');
        return patch;
    });
    const getPreferences = vi.fn(async () => {
        if (failGet) throw new Error('Failed to fetch');
        return preferences;
    });
    const run = () => syncVideoLinkPreferences({
        getPreferences,
        savePreferences,
        loadSettings: () => stored,
        saveSettings: (next) => { stored = next; },
        storage,
    });
    return {run, savePreferences, storage, stored: () => stored};
};

describe('the browser-login choice on start', () => {
    it('copies a choice made before the change to the service, once', async () => {
        const h = harness({preferences: {}, settings: {videoCookiesBrowser: 'safari'}});
        const result = await h.run();
        expect(h.savePreferences).toHaveBeenCalledWith({video_cookies_browser: 'safari'});
        expect(result.videoCookiesBrowser).toBe('safari');
        expect(h.storage.data[COOKIES_MIGRATED_STORAGE_KEY]).toBe('1');
    });

    it('does not copy it again once it has been copied', async () => {
        const storage = memoryStorage({[COOKIES_MIGRATED_STORAGE_KEY]: '1'});
        // The service no longer holds a choice (turned off elsewhere): it wins.
        const h = harness({preferences: {}, settings: {videoCookiesBrowser: 'safari'}, storage});
        const result = await h.run();
        expect(h.savePreferences).not.toHaveBeenCalled();
        expect(result.videoCookiesBrowser).toBe('');
        expect(h.stored().videoCookiesBrowser).toBe('');
    });

    it('takes the service choice over the stored one, and mirrors it locally', async () => {
        const h = harness({preferences: {video_cookies_browser: 'chrome'}, settings: {videoCookiesBrowser: 'safari'}});
        const result = await h.run();
        expect(h.savePreferences).not.toHaveBeenCalled();
        expect(result.videoCookiesBrowser).toBe('chrome');
        // The link box and retries read stored settings; they must agree.
        expect(h.stored().videoCookiesBrowser).toBe('chrome');
    });

    it('takes "off" from the service as a choice too', async () => {
        const h = harness({preferences: {video_cookies_browser: ''}, settings: {videoCookiesBrowser: 'safari'}});
        const result = await h.run();
        expect(result.videoCookiesBrowser).toBe('');
        expect(h.stored().videoCookiesBrowser).toBe('');
    });

    it('keeps the stored choice when the service cannot be reached', async () => {
        const h = harness({failGet: true, settings: {videoCookiesBrowser: 'edge'}});
        const result = await h.run();
        expect(h.savePreferences).not.toHaveBeenCalled();
        expect(result.videoCookiesBrowser).toBe('edge');
        expect(result.reachable).toBe(false);
        expect(h.stored().videoCookiesBrowser).toBe('edge');
    });

    it('tries the copy again next start when the service refused it', async () => {
        const h = harness({preferences: {}, settings: {videoCookiesBrowser: 'safari'}, failSave: true});
        await h.run();
        expect(h.storage.data[COOKIES_MIGRATED_STORAGE_KEY]).toBeUndefined();
    });
});

describe('the Douyin third-party fallback switch', () => {
    it('is on unless the service says it was switched off', () => {
        expect(allowMiuistoreFromPreferences({})).toBe(true);
        expect(allowMiuistoreFromPreferences(null)).toBe(true);
        expect(allowMiuistoreFromPreferences({allow_miuistore: true})).toBe(true);
        expect(allowMiuistoreFromPreferences({allow_miuistore: false})).toBe(false);
    });
});
