// The Feishu export route and folder, kept by the local service rather than
// only in this browser, so an export an AI tool asks for (MCP) goes the same
// way and lands in the same folder as one started here.
//
// Both used to live only in this browser's stored settings. On the first start
// after that changed, the stored values are copied to the service once; from
// then on the service is the source and the stored copy only mirrors it (the
// submit paths in this page still read stored settings).

import {isLocalLarkExportRoute, larkExportRouteFromSettings, normalizeLarkExportRoute} from './settingsModel.js';

export const LARK_ROUTE_PREF = 'lark_export_route';
export const LARK_FOLDER_PREF = 'lark_folder_token';
export const LARK_MIGRATED_STORAGE_KEY = 'fluentflow_lark_export_migrated';

const has = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const folderText = (value) => String(value ?? '').trim();

// The stored-settings patch that mirrors a route choice.
export const larkRouteSettingsPatch = (route) => {
    const normalized = normalizeLarkExportRoute(route);
    return {larkExportRoute: normalized, larkViaCli: isLocalLarkExportRoute(normalized)};
};

// Decide the route and folder from what the service holds and what this
// browser stored. `preferences` is null when the service could not be asked.
export const planLarkExportSync = ({preferences, settings = {}, migrated}) => {
    const localRoute = larkExportRouteFromSettings(settings);
    const localFolder = folderText(settings.larkFolder);
    if (!preferences || typeof preferences !== 'object') {
        return {route: localRoute, folder: localFolder, migratePatch: null, markMigrated: false};
    }
    const hasRoute = has(preferences, LARK_ROUTE_PREF);
    const hasFolder = has(preferences, LARK_FOLDER_PREF);
    const migratePatch = {};
    if (!migrated) {
        if (!hasRoute) migratePatch[LARK_ROUTE_PREF] = localRoute;
        if (!hasFolder && localFolder) migratePatch[LARK_FOLDER_PREF] = localFolder;
    }
    // After the one copy, what the service lacks means "not chosen": the
    // defaults, not whatever this browser happens to remember.
    const route = hasRoute
        ? normalizeLarkExportRoute(preferences[LARK_ROUTE_PREF])
        : (migrated ? normalizeLarkExportRoute('') : localRoute);
    const folder = hasFolder
        ? folderText(preferences[LARK_FOLDER_PREF])
        : (migrated ? '' : localFolder);
    const needsCopy = Object.keys(migratePatch).length > 0;
    return {route, folder, migratePatch: needsCopy ? migratePatch : null, markMigrated: !needsCopy && !migrated};
};

const readMigrated = (storage) => {
    try { return storage?.getItem(LARK_MIGRATED_STORAGE_KEY) === '1'; } catch (_) { return false; }
};
const writeMigrated = (storage) => {
    try { storage?.setItem(LARK_MIGRATED_STORAGE_KEY, '1'); } catch (_) { /* storage unavailable */ }
};

// Read the service's route and folder, copy the old stored values over if this
// is the first time, and mirror the result into stored settings. Never throws;
// a service that cannot be reached leaves the stored values as they were.
// `preferences` may be passed in when the caller already fetched them.
export const syncLarkExportPreferences = async ({getPreferences, savePreferences, loadSettings, saveSettings, storage, preferences: given}) => {
    const settings = loadSettings?.() || {};
    let preferences = given === undefined ? null : given;
    if (given === undefined) {
        try {
            const data = await getPreferences?.();
            preferences = data && typeof data === 'object' ? data : null;
        } catch (_) {
            preferences = null;
        }
    }
    const plan = planLarkExportSync({preferences, settings, migrated: readMigrated(storage)});
    if (plan.migratePatch) {
        try {
            await savePreferences?.(plan.migratePatch);
            writeMigrated(storage);
        } catch (_) { /* try again on the next start */ }
    } else if (plan.markMigrated) {
        writeMigrated(storage);
    }
    if (preferences) {
        const current = loadSettings?.() || settings;
        if (larkExportRouteFromSettings(current) !== plan.route || folderText(current.larkFolder) !== plan.folder) {
            saveSettings?.({...current, ...larkRouteSettingsPatch(plan.route), larkFolder: plan.folder});
        }
    }
    return {route: plan.route, folder: plan.folder, reachable: !!preferences};
};
