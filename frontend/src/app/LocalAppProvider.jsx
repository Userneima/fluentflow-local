import {useEffect, useMemo, useRef, useState} from 'react';
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
    accountJobsCacheKey,
    entryToJob,
    jobToCurrentJob,
    jobToHistoryEntry,
    jobVisibleInHistory,
    readCachedAccountJobs,
    reconcileTaskList,
    writeCachedAccountJobs,
} from '../lib/jobMappers.js';
import {normalizeTaskState, TASK_STATE_QUEUED, TASK_STATE_RUNNING} from '../lib/taskState.js';

const LOCAL_SCOPE = 'local';
const taskKey = (job) => String(job?.task_id || job?.result?.task_id || '').trim();

// Local composition deliberately owns no account, guest, or user-switch
// state. Its stable `local` cache is only a browser recovery aid for the
// single loopback workspace.
export const LocalAppProvider = ({children}) => {
    const [tasks, setTasks] = useState([]);
    const [larkExports, setLarkExports] = useState(() => {
        try { return JSON.parse(localStorage.getItem('fluentflow_lark_exports') || '[]'); } catch (_) { return []; }
    });
    const [currentJob, setCurrentJob] = useState(null);
    const [lastResult, setLastResult] = useState(null);
    const [lastSourceFile, setLastSourceFile] = useState(null);
    const [runtimeConfig, setRuntimeConfig] = useState(defaultRuntimeConfig);
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

    useEffect(() => {
        let active = true;
        apiFetch(`${API_BASE}/runtime-config`)
            .then((response) => response.ok ? response.json() : null)
            .then((data) => { if (data && active) setRuntimeConfig(normalizeRuntimeConfig(data)); })
            .catch(() => {});
        try {
            const rawSettings = JSON.parse(localStorage.getItem('fluentflow_settings') || '{}');
            if (SENSITIVE_SETTING_KEYS.some((key) => rawSettings[key])) {
                apiFetch(`${API_BASE}/credentials`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(sensitivePatchFromSettings(rawSettings)),
                }).finally(() => localStorage.setItem('fluentflow_settings', JSON.stringify(sanitizeSettings(rawSettings))));
            }
        } catch (_) {}

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
            .catch(() => {});
        return () => { active = false; };
    }, []);

    useEffect(() => {
        if (hydratedRef.current) writeCachedAccountJobs(LOCAL_SCOPE, tasks);
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
    const ingestJobs = (fetched) => {
        if (Array.isArray(fetched) && fetched.length) setTasks((current) => reconcileInto(current, {fetched}));
    };
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
    const clearHistory = () => {
        tombstonesRef.current = new Set();
        setTasks([]);
        persistLarkExports([]);
        localStorage.removeItem('fluentflow_history');
        localStorage.removeItem(accountJobsCacheKey(LOCAL_SCOPE));
    };
    const addLarkExport = (entry) => persistLarkExports([entry, ...larkExports].slice(0, 50));
    const stats = {
        totalMinutes: Math.round(history.reduce((total, item) => total + (item.durationMin || 0), 0)),
        notesGenerated: history.filter((item) => item.status === 'completed').length,
    };

    return <AppCtx.Provider value={{tasks, history, ingestJobs, markCancelled, revertCancelled, restoreTask, addToHistory, removeFromHistory, clearHistory, currentJob, setCurrentJob, lastResult, setLastResult, lastSourceFile, setLastSourceFile, stats, larkExports, addLarkExport, runtimeConfig, setPendingUploadAbort, abortPendingUpload}}>{children}</AppCtx.Provider>;
};
