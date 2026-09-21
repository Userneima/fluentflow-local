import {describe, expect, it, vi} from 'vitest';
import {
    RELOAD_COOLDOWN_MS,
    RELOAD_MARKER_KEY,
    installStaleBuildRecovery,
    isStaleChunkError,
    recoverFromStaleBuild,
} from './staleBuildRecovery.js';

// A page that is still running a previous build asks for chunk filenames the
// current build no longer has. These are the shapes that failure takes.
const STALE_MESSAGES = [
    'Failed to fetch dynamically imported module: http://127.0.0.1:8000/assets/settings-Cc3dBdjv.js',
    'error loading dynamically imported module: /assets/editor-B3eui8KG.js',
    'Importing a module script failed.',
    'Unable to preload CSS for /assets/local-B2MImnha.css',
];

const fakeStorage = (initial = {}) => {
    const store = new Map(Object.entries(initial));
    return {
        getItem: (key) => (store.has(key) ? store.get(key) : null),
        setItem: (key, value) => store.set(key, String(value)),
        removeItem: (key) => store.delete(key),
        read: (key) => (store.has(key) ? store.get(key) : null),
    };
};

describe('stale chunk detection', () => {
    it('recognizes every chunk-load failure the browsers report', () => {
        for (const message of STALE_MESSAGES) {
            expect(isStaleChunkError(new Error(message)), message).toBe(true);
            // Some browsers reject with a bare string rather than an Error.
            expect(isStaleChunkError(message), message).toBe(true);
        }
    });

    it('leaves ordinary application errors alone', () => {
        expect(isStaleChunkError(new TypeError("Cannot read properties of undefined (reading 'map')"))).toBe(false);
        expect(isStaleChunkError(new Error('HTTP 500 /jobs'))).toBe(false);
        expect(isStaleChunkError(null)).toBe(false);
        expect(isStaleChunkError(undefined)).toBe(false);
    });
});

describe('one-shot reload', () => {
    it('reloads once and records when it did', () => {
        const storage = fakeStorage();
        const reload = vi.fn();

        expect(recoverFromStaleBuild({storage, reload, now: () => 5_000})).toBe(true);
        expect(reload).toHaveBeenCalledTimes(1);
        expect(storage.read(RELOAD_MARKER_KEY)).toBe('5000');
    });

    it('refuses to reload again inside the cooldown, so a broken build cannot loop', () => {
        const storage = fakeStorage({[RELOAD_MARKER_KEY]: '5000'});
        const reload = vi.fn();

        expect(recoverFromStaleBuild({storage, reload, now: () => 5_000 + RELOAD_COOLDOWN_MS - 1})).toBe(false);
        expect(reload).not.toHaveBeenCalled();
    });

    it('recovers again once the cooldown has passed, so a later rebuild is still handled', () => {
        const storage = fakeStorage({[RELOAD_MARKER_KEY]: '5000'});
        const reload = vi.fn();

        expect(recoverFromStaleBuild({storage, reload, now: () => 5_000 + RELOAD_COOLDOWN_MS})).toBe(true);
        expect(reload).toHaveBeenCalledTimes(1);
    });

    it('survives a storage that refuses to answer', () => {
        const hostile = {
            getItem: () => {
                throw new Error('storage disabled');
            },
            setItem: () => {
                throw new Error('storage disabled');
            },
        };
        const reload = vi.fn();

        // No storage means no loop protection, but the page must still recover.
        expect(recoverFromStaleBuild({storage: hostile, reload, now: () => 1})).toBe(true);
        expect(reload).toHaveBeenCalledTimes(1);
    });
});

describe('listener installation', () => {
    const fakeTarget = () => {
        const listeners = new Map();
        return {
            addEventListener: (type, handler) => listeners.set(type, handler),
            removeEventListener: (type) => listeners.delete(type),
            emit: (type, event) => listeners.get(type)?.(event),
            types: () => [...listeners.keys()],
        };
    };

    it("recovers from Vite's preload failures, which never reach React", () => {
        const target = fakeTarget();
        const reload = vi.fn();
        installStaleBuildRecovery({target, storage: fakeStorage(), reload, now: () => 1});

        expect(target.types()).toContain('vite:preloadError');
        target.emit('vite:preloadError', {payload: new Error('Unable to preload CSS for /assets/local-B2MImnha.css')});
        expect(reload).toHaveBeenCalledTimes(1);
    });

    it('recovers from an unhandled rejection carrying a stale import', () => {
        const target = fakeTarget();
        const reload = vi.fn();
        installStaleBuildRecovery({target, storage: fakeStorage(), reload, now: () => 1});

        target.emit('unhandledrejection', {reason: new Error(STALE_MESSAGES[0])});
        expect(reload).toHaveBeenCalledTimes(1);
    });

    it('ignores unhandled rejections that are the app\'s own bugs', () => {
        const target = fakeTarget();
        const reload = vi.fn();
        installStaleBuildRecovery({target, storage: fakeStorage(), reload, now: () => 1});

        target.emit('unhandledrejection', {reason: new Error('HTTP 500 /jobs')});
        expect(reload).not.toHaveBeenCalled();
    });

    it('can be uninstalled', () => {
        const target = fakeTarget();
        const uninstall = installStaleBuildRecovery({target, storage: fakeStorage(), reload: () => {}, now: () => 1});

        uninstall();
        expect(target.types()).toEqual([]);
    });
});
