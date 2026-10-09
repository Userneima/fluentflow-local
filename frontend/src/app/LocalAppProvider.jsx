import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {API_BASE, apiFetch} from './apiConfig.js';
import {AppCtx} from './AppContext.jsx';
import {localExecutionHeaders} from '../lib/localExecution.js';
import {
    SENSITIVE_SETTING_KEYS,
    sanitizeSettings,
    sensitivePatchFromSettings,
} from '../lib/settingsModel.js';
import {defaultRuntimeConfig, normalizeRuntimeConfig} from '../lib/sttPolicy.js';
import {
    entryToJob,
    jobToCurrentJob,
    jobToHistoryEntry,
    jobVisibleInHistory,
    larkExportsFromJobs,
    readCachedAccountJobs,
    reconcileTaskList,
    writeCachedAccountJobs,
} from '../lib/jobMappers.js';
import {normalizeTaskState, TASK_STATE_QUEUED, TASK_STATE_RUNNING} from '../lib/taskState.js';
import {isBackendUnreachableError} from '../lib/backendHealth.js';
import {syncVideoLinkPreferences} from '../lib/videoLinkPrefs.js';
import {syncLarkExportPreferences} from '../lib/larkExportPrefs.js';

const LOCAL_SCOPE = 'local';
const taskKey = (job) => String(job?.task_id || job?.result?.task_id || '').trim();
// How often to knock on a service that has stopped answering. Nothing is
// polled while it answers: the ordinary job polling notices the next outage.
const HEALTH_RECHECK_MS = 10000;
// The task list is written to localStorage as a recovery aid. A hundred jobs
// serialised on every 5-second poll was a visible stutter; one write a second
// after the list settles is plenty for a cache nobody reads until the next load.
const CACHE_WRITE_DEBOUNCE_MS = 1000;

// Local composition deliberately owns no account, guest, or user-switch
// state. Its stable `local` cache is only a browser recovery aid for the
// single loopback workspace.
export const LocalAppProvider = ({children}) => {
    const [tasks, setTasks] = useState([]);
    const [manualLarkExports, setLarkExports] = useState(() => {
        try { return JSON.parse(localStorage.getItem('fluentflow_lark_exports') || '[]'); } catch (_) { return []; }
    });
    const [currentJob, setCurrentJob] = useState(null);
    const [lastResult, setLastResult] = useState(null);
    const [lastSourceFile, setLastSourceFile] = useState(null);
    const [runtimeConfig, setRuntimeConfig] = useState(defaultRuntimeConfig);
    // Whether this build can separate speakers at all. Pages that submit work
    // read it so a task does not ask for something that is always skipped.
    const [diarizationStatus, setDiarizationStatus] = useState(null);
    // null until the first answer; false is the only value that shows the
    // banner, so an unanswered first check does not flash it on every load.
    const [backendHealthy, setBackendHealthy] = useState(null);
    const hydratedRef = useRef(false);
    const tombstonesRef = useRef(new Set());
    const cancelledRef = useRef(new Set());
    const uploadAbortRef = useRef(null);

    const setPendingUploadAbort = (controller) => { uploadAbortRef.current = controller || null; };
    const abortPendingUpload = () => {
        const controller = uploadAbortRef.current;
        uploadAbortRef.current = null;
        if (!controller) return false;
        controller.abort();
        return true;
    };

    // One check, answered true or false. Every page used to call /health and
    // drop the answer; now the answer lives here and the pages read it.
    const checkBackendHealth = useCallback(async () => {
        let healthy = false;
        try {
            const response = await apiFetch(`${API_BASE}/health`);
            healthy = !!response?.ok;
        } catch (_) {
            healthy = false;
        }
        setBackendHealthy(healthy);
        return healthy;
    }, []);
    // Any fetch that failed at the network layer is evidence the service is
    // gone; a 4xx/5xx is not, it answered.
    const reportBackendError = useCallback((error) => {
        if (isBackendUnreachableError(error)) setBackendHealthy(false);
    }, []);

    useEffect(() => {
        let active = true;
        checkBackendHealth();
        apiFetch(`${API_BASE}/runtime-config`)
            .then((response) => response.ok ? response.json() : null)
            .then((data) => { if (data && active) setRuntimeConfig(normalizeRuntimeConfig(data)); })
            .catch((error) => { if (active) reportBackendError(error); });
        apiFetch(`${API_BASE}/speaker-diarization/status`)
            .then((response) => response.ok ? response.json() : null)
            .then((data) => { if (data && active) setDiarizationStatus(data); })
            .catch(() => {});
        try {
            const rawSettings = JSON.parse(localStorage.getItem('fluentflow_settings') || '{}');
            if (SENSITIVE_SETTING_KEYS.some((key) => rawSettings[key])) {
                apiFetch(`${API_BASE}/credentials`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(sensitivePatchFromSettings(rawSettings)),
                }).catch(() => {}).finally(() => localStorage.setItem('fluentflow_settings', JSON.stringify(sanitizeSettings(rawSettings))));
            }
        } catch (_) {}

        // The browser-login choice for video links and the Feishu export route
        // and folder live in the service now, so links submitted and exports
        // asked for by AI tools use them too. Copy the old stored choices over
        // once, and mirror the service's answers into stored settings.
        const preferenceSync = {
            getPreferences: async () => {
                const response = await apiFetch(`${API_BASE}/preferences`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                return data?.preferences && typeof data.preferences === 'object' ? data.preferences : {};
            },
            savePreferences: async (patch) => {
                const response = await apiFetch(`${API_BASE}/preferences`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(patch),
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            },
            loadSettings: () => {
                try { return JSON.parse(localStorage.getItem('fluentflow_settings') || '{}'); } catch (_) { return {}; }
            },
            saveSettings: (next) => {
                try { localStorage.setItem('fluentflow_settings', JSON.stringify(sanitizeSettings(next))); } catch (_) {}
            },
            storage: (() => { try { return localStorage; } catch (_) { return null; } })(),
        };
        // One after the other: both read and rewrite the same stored settings.
        syncVideoLinkPreferences(preferenceSync).then(() => syncLarkExportPreferences(preferenceSync));

        const cached = readCachedAccountJobs(LOCAL_SCOPE);
        tombstonesRef.current = new Set();
        cancelledRef.current = new Set();
        hydratedRef.current = true;
        setTasks(reconcileTaskList({cached, accountId: LOCAL_SCOPE}));
        apiFetch(`${API_BASE}/jobs`, {headers: localExecutionHeaders({sttProvider: 'local'})})
            .then(async (response) => response.ok ? response.json() : {})
            .then((data) => {
                if (!active) return;
                const fetched = Array.isArray(data?.jobs) ? data.jobs : [];
                const next = reconcileTaskList({fetched, cached, accountId: LOCAL_SCOPE});
                setTasks(next);
                const running = next.find((job) => [TASK_STATE_RUNNING, TASK_STATE_QUEUED].includes(normalizeTaskState(job)));
                if (running) setCurrentJob(jobToCurrentJob(running));
            })
            .catch((error) => { if (active) reportBackendError(error); });
        return () => { active = false; };
    }, [checkBackendHealth, reportBackendError]);

    // While the service is down, knock every ten seconds so the banner clears
    // on its own once FluentFlow Local is reopened.
    useEffect(() => {
        if (backendHealthy !== false) return undefined;
        const timer = setInterval(() => { checkBackendHealth(); }, HEALTH_RECHECK_MS);
        return () => clearInterval(timer);
    }, [backendHealthy, checkBackendHealth]);

    const pendingCacheRef = useRef(null);
    useEffect(() => {
        if (!hydratedRef.current) return undefined;
        pendingCacheRef.current = tasks;
        const flush = () => {
            if (pendingCacheRef.current === null) return;
            writeCachedAccountJobs(LOCAL_SCOPE, pendingCacheRef.current);
            pendingCacheRef.current = null;
        };
        const timer = setTimeout(flush, CACHE_WRITE_DEBOUNCE_MS);
        // A tab closed inside the debounce window would otherwise lose its
        // last change; pagehide is the last chance to write it.
        window.addEventListener('pagehide', flush);
        return () => {
            clearTimeout(timer);
            window.removeEventListener('pagehide', flush);
        };
    }, [tasks]);

    const history = useMemo(() => tasks.filter(jobVisibleInHistory).map(jobToHistoryEntry), [tasks]);
    const persistLarkExports = (entries) => {
        setLarkExports(entries);
        localStorage.setItem('fluentflow_lark_exports', JSON.stringify(entries));
    };
    const reconcileInto = (current, extra = {}) => reconcileTaskList({
        cached: current,
        tombstones: tombstonesRef.current,
        cancelled: cancelledRef.current,
        accountId: LOCAL_SCOPE,
        ...extra,
    });
    const addToHistory = (entry) => {
        const job = entryToJob(entry);
        if (job) setTasks((current) => reconcileInto(current, {optimistic: [job]}));
    };
    // Stable: the records page lists it as an effect dependency, and a new
    // identity per render would re-ingest the seeded job every time.
    const ingestJobs = useCallback((fetched) => {
        if (Array.isArray(fetched) && fetched.length) {
            setTasks((current) => reconcileTaskList({
                cached: current,
                tombstones: tombstonesRef.current,
                cancelled: cancelledRef.current,
                accountId: LOCAL_SCOPE,
                fetched,
            }));
        }
    }, []);
    const removeFromHistory = (taskId) => {
        if (!taskId) return;
        tombstonesRef.current.add(String(taskId));
        setTasks((current) => current.filter((job) => taskKey(job) !== String(taskId)));
    };
    const restoreTask = (taskId) => { if (taskId) tombstonesRef.current.delete(String(taskId)); };
    const markCancelled = (taskId) => {
        if (!taskId) return;
        cancelledRef.current.add(String(taskId));
        setTasks((current) => reconcileInto(current));
    };
    const revertCancelled = (taskId) => {
        if (!taskId) return;
        cancelledRef.current.delete(String(taskId));
        setTasks((current) => reconcileInto(current));
    };
    const addLarkExport = (entry) => persistLarkExports([entry, ...manualLarkExports].slice(0, 50));
    // Every document this app has made, not only the ones exported by hand:
    // automatic exports are recorded on the task, not in this browser.
    const larkExports = useMemo(() => larkExportsFromJobs(tasks, manualLarkExports), [tasks, manualLarkExports]);
    const stats = {
        totalMinutes: Math.round(history.reduce((total, item) => total + (item.durationMin || 0), 0)),
        notesGenerated: history.filter((item) => item.status === 'completed').length,
    };

    return <AppCtx.Provider value={{tasks, history, ingestJobs, markCancelled, revertCancelled, restoreTask, addToHistory, removeFromHistory, currentJob, setCurrentJob, lastResult, setLastResult, lastSourceFile, setLastSourceFile, stats, larkExports, addLarkExport, runtimeConfig, diarizationStatus, setPendingUploadAbort, abortPendingUpload, backendHealthy, backendDown: backendHealthy === false, checkBackendHealth, reportBackendError}}>{children}</AppCtx.Provider>;
};
