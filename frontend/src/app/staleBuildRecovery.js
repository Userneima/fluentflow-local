// Recovery from a page that outlived its build.
//
// The built frontend is split into content-hashed chunks and the route imports
// in localRoutes.jsx are lazy, so a page fetches most of its own code later,
// by filename, from the build it was loaded from. `vite.local.config.mjs` sets
// `emptyOutDir: true`, so rebuilding deletes those filenames. A page that is
// still running an older build therefore asks for chunks the server no longer
// has: the import rejects with "Failed to fetch dynamically imported module"
// and — with nothing catching it — the route renders nothing at all.
//
// The page cannot repair its own module graph. Reloading it can: the entry
// document is served `no-store` (backend/routers/local_spa.py), so a reload
// fetches the current index and with it the current chunk names.
//
// So the policy, in one place: recognize a stale-chunk failure, reload once,
// and if a reload just happened, stop and say so rather than loop. Callers
// inject storage/reload/clock; nothing here reaches for globals on its own.

const STALE_CHUNK_PATTERNS = [
    'failed to fetch dynamically imported module',
    'error loading dynamically imported module',
    'importing a module script failed',
    'failed to load module script',
    'unable to preload css',
];

export const RELOAD_MARKER_KEY = 'fluentflow_stale_build_reloaded_at';

// Long enough that a reload which lands on the same broken build stops instead
// of looping; short enough that a rebuild later in the same session is still
// recovered from automatically.
export const RELOAD_COOLDOWN_MS = 10_000;

const messageOf = (error) => {
    if (typeof error === 'string') return error;
    if (error && typeof error.message === 'string') return error.message;
    return '';
};

export const isStaleChunkError = (error) => {
    const message = messageOf(error).toLowerCase();
    if (!message) return false;
    return STALE_CHUNK_PATTERNS.some((pattern) => message.includes(pattern));
};

// A storage that throws (private mode, disabled storage) must not stop the
// recovery: losing loop protection is better than leaving a dead page.
const readMarker = (storage) => {
    try {
        return Number.parseInt(storage?.getItem(RELOAD_MARKER_KEY) ?? '', 10);
    } catch (_) {
        return Number.NaN;
    }
};

const writeMarker = (storage, at) => {
    try {
        storage?.setItem(RELOAD_MARKER_KEY, String(at));
    } catch (_) {
        // Ignore: see readMarker.
    }
};

// Returns true when a reload was triggered, false when one just happened and
// the caller should explain the mismatch instead.
export const recoverFromStaleBuild = ({storage, reload, now = () => Date.now()}) => {
    const at = now();
    const lastReloadAt = readMarker(storage);
    if (Number.isFinite(lastReloadAt) && at - lastReloadAt < RELOAD_COOLDOWN_MS) return false;

    writeMarker(storage, at);
    reload();
    return true;
};

// Vite's preload failures (a route's CSS or a modulepreload) reject outside
// React, so no error boundary ever sees them; these listeners cover that path.
export const installStaleBuildRecovery = ({
    target = typeof window === 'undefined' ? null : window,
    storage = typeof sessionStorage === 'undefined' ? null : sessionStorage,
    reload = () => window.location.reload(),
    now = () => Date.now(),
} = {}) => {
    if (!target) return () => {};

    const recover = () => recoverFromStaleBuild({storage, reload, now});
    const onPreloadError = (event) => {
        // Vite carries the cause in `payload`; fall back to the event itself.
        if (isStaleChunkError(event?.payload ?? event)) recover();
    };
    const onRejection = (event) => {
        if (isStaleChunkError(event?.reason)) recover();
    };

    target.addEventListener('vite:preloadError', onPreloadError);
    target.addEventListener('unhandledrejection', onRejection);

    return () => {
        target.removeEventListener('vite:preloadError', onPreloadError);
        target.removeEventListener('unhandledrejection', onRejection);
    };
};
