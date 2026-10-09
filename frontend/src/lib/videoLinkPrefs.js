// The two video-link choices that the local service, not the browser, keeps:
// which browser's login to read, and whether Douyin may fall back to a
// third-party resolver. They live in the service's preferences so that a link
// submitted by an AI tool (MCP) is downloaded the same way as one pasted here.
//
// The browser choice used to live only in this browser's stored settings. On
// the first start after that changed, the stored value is copied to the
// service once; from then on the service is the source and the stored copy only
// mirrors it.

export const VIDEO_COOKIES_PREF = 'video_cookies_browser';
export const MIUISTORE_PREF = 'allow_miuistore';
export const COOKIES_MIGRATED_STORAGE_KEY = 'fluentflow_video_cookies_migrated';

const KNOWN_BROWSERS = new Set(['chrome', 'edge', 'firefox', 'safari', 'brave', 'chromium', 'opera', 'vivaldi']);

export const normalizeCookiesBrowser = (value) => {
    const name = String(value ?? '').trim().toLowerCase();
    return KNOWN_BROWSERS.has(name) ? name : '';
};

// Whether a Douyin link may go to the third-party resolver: true, false, or
// null when the person has never been asked. The service keeps the answer; a
// null is asked about before the first Douyin link is submitted.
export const miuistoreChoiceFromPreferences = (preferences) => {
    const value = preferences?.[MIUISTORE_PREF];
    if (value === true || value === 'true') return true;
    if (value === false || value === 'false') return false;
    return null;
};

// A Douyin share link or share text: douyin.com, v.douyin.com, iesdouyin.com.
const DOUYIN_HOST_RE = /(?:^|[^a-z0-9.-])(?:[a-z0-9-]+\.)*(?:iesdouyin|douyin)\.com(?![a-z0-9-])/i;
export const isDouyinLinkText = (text) => DOUYIN_HOST_RE.test(String(text ?? ''));

// Before a link goes in: does the person have to be asked about the
// third-party resolver first? Only for a Douyin link, and only while the
// service holds no answer. A service that could not be asked is not a reason
// to ask; the service decides from what it holds.
export const needsMiuistoreConsent = (input, preferences) => (
    !!preferences && typeof preferences === 'object'
    && isDouyinLinkText(input)
    && miuistoreChoiceFromPreferences(preferences) === null
);

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
        allowMiuistore: miuistoreChoiceFromPreferences(preferences),
        reachable: !!preferences,
    };
};
