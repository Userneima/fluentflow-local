import {describe, expect, it, vi} from 'vitest';
import {LARK_MIGRATED_STORAGE_KEY, syncLarkExportPreferences} from './larkExportPrefs.js';

// Written from the requirement: an export an AI tool asks for goes the same
// way, and into the same folder, as one started on this page. So the route and
// folder live in the service. Someone who chose them before this change must
// not have to choose again, and the old stored values are copied over once.

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
    const run = () => syncLarkExportPreferences({
        getPreferences: async () => {
            if (failGet) throw new Error('Failed to fetch');
            return preferences;
        },
        savePreferences,
        loadSettings: () => stored,
        saveSettings: (next) => { stored = next; },
        storage,
    });
    return {run, savePreferences, storage, stored: () => stored};
};

const FOLDER = 'https://x.feishu.cn/drive/folder/fldcnABC';

describe('the Feishu route and folder on start', () => {
    it('copies a route and folder chosen before the change to the service, once', async () => {
        const h = harness({preferences: {}, settings: {larkExportRoute: 'openapi', larkFolder: FOLDER}});
        const result = await h.run();
        expect(h.savePreferences).toHaveBeenCalledWith({lark_export_route: 'openapi', lark_folder_token: FOLDER});
        expect(result).toMatchObject({route: 'openapi', folder: FOLDER});
        expect(h.storage.data[LARK_MIGRATED_STORAGE_KEY]).toBe('1');
    });

    it('copies an old "use lark-cli" switch as the local-identity route', async () => {
        const h = harness({preferences: {}, settings: {larkViaCli: true}});
        await h.run();
        expect(h.savePreferences).toHaveBeenCalledWith({lark_export_route: 'local_cli'});
    });

    it('does not copy again once copied; the service is the source after that', async () => {
        const storage = memoryStorage({[LARK_MIGRATED_STORAGE_KEY]: '1'});
        const h = harness({preferences: {lark_export_route: 'local_cli'}, settings: {larkExportRoute: 'openapi', larkFolder: FOLDER}, storage});
        const result = await h.run();
        expect(h.savePreferences).not.toHaveBeenCalled();
        expect(result).toMatchObject({route: 'local_cli', folder: ''});
        // The page's own submit paths read stored settings; they must agree.
        expect(h.stored()).toMatchObject({larkExportRoute: 'local_cli', larkViaCli: true, larkFolder: ''});
    });

    it('takes the service values over older stored ones', async () => {
        const h = harness({
            preferences: {lark_export_route: 'openapi', lark_folder_token: FOLDER},
            settings: {larkExportRoute: 'auto'},
        });
        const result = await h.run();
        expect(h.savePreferences).not.toHaveBeenCalled();
        expect(result).toMatchObject({route: 'openapi', folder: FOLDER});
        expect(h.stored()).toMatchObject({larkExportRoute: 'openapi', larkViaCli: false, larkFolder: FOLDER});
    });

    it('keeps the stored values when the service cannot be reached', async () => {
        const h = harness({failGet: true, settings: {larkExportRoute: 'openapi', larkFolder: FOLDER}});
        const result = await h.run();
        expect(h.savePreferences).not.toHaveBeenCalled();
        expect(result.reachable).toBe(false);
        expect(h.stored()).toMatchObject({larkExportRoute: 'openapi', larkFolder: FOLDER});
    });

    it('tries the copy again next start when the service refused it', async () => {
        const h = harness({preferences: {}, settings: {larkExportRoute: 'openapi'}, failSave: true});
        const result = await h.run();
        expect(h.storage.data[LARK_MIGRATED_STORAGE_KEY]).toBeUndefined();
        // Meanwhile this page keeps using what was chosen here.
        expect(result.route).toBe('openapi');
    });
});
