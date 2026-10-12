// The video-link choice that the local service, not the browser, keeps: which
// browser's login to read. It lives in the service's preferences so that a
// link submitted by an AI tool (MCP) is downloaded the same way as one pasted
// here.
//
// The browser choice used to live only in this browser's stored settings. On
// the first start after that changed, the stored value is copied to the
// service once; from then on the service is the source and the stored copy only
// mirrors it.

export const VIDEO_COOKIES_PREF = 'video_cookies_browser';
export const COOKIES_MIGRATED_STORAGE_KEY = 'fluentflow_video_cookies_migrated';

const KNOWN_BROWSERS = new Set(['chrome', 'edge', 'firefox', 'safari', 'brave', 'chromium', 'opera', 'vivaldi']);

export const normalizeCookiesBrowser = (value) => {
    const name = String(value ?? '').trim().toLowerCase();
    return KNOWN_BROWSERS.has(name) ? name : '';
};

// Decide the browser choice from what the service holds and what this browser
// stored. `preferences` is null when the service could not be asked.
export const planCookiesBrowserSync = ({preferences, localValue, migrated}) => {
    const local = normalizeCookiesBrowser(localValue);
    if (!preferences || typeof preferences !== 'object') {
        return {value: local, migrate: false, markMigrated: false};
    }
    if (Object.prototype.hasOwnProperty.call(preferences, VIDEO_COOKIES_PREF)) {
        return {value: normalizeCookiesBrowser(preferences[VIDEO_COOKIES_PREF]), migrate: false, markMigrated: true};
    }
    if (!migrated && local) return {value: local, migrate: true, markMigrated: false};
    return {value: migrated ? '' : local, migrate: false, markMigrated: false};
};

const readMigrated = (storage) => {
    try { return storage?.getItem(COOKIES_MIGRATED_STORAGE_KEY) === '1'; } catch (_) { return false; }
};
const writeMigrated = (storage) => {
    try { storage?.setItem(COOKIES_MIGRATED_STORAGE_KEY, '1'); } catch (_) { /* storage unavailable */ }
};

// Read the service's choices, migrate the old stored browser choice if this is
// the first time, and mirror the result into stored settings (the submit paths
// read it from there). Never throws; a service that cannot be reached leaves
// the stored choice as it was.
export const syncVideoLinkPreferences = async ({getPreferences, savePreferences, loadSettings, saveSettings, storage}) => {
    const settings = loadSettings?.() || {};
    let preferences = null;
    try {
        const data = await getPreferences?.();
        preferences = data && typeof data === 'object' ? data : null;
    } catch (_) {
        preferences = null;
    }
    const plan = planCookiesBrowserSync({
        preferences,
        localValue: settings.videoCookiesBrowser,
        migrated: readMigrated(storage),
    });
    if (plan.migrate) {
        try {
            await savePreferences?.({[VIDEO_COOKIES_PREF]: plan.value});
            writeMigrated(storage);
        } catch (_) { /* try again on the next start */ }
    } else if (plan.markMigrated) {
        writeMigrated(storage);
    }
    if (preferences && normalizeCookiesBrowser(settings.videoCookiesBrowser) !== plan.value) {
        saveSettings?.({...(loadSettings?.() || settings), videoCookiesBrowser: plan.value});
    }
    return {
        videoCookiesBrowser: plan.value,
        reachable: !!preferences,
    };
};
