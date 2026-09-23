import {useCallback, useEffect, useRef, useState} from 'react';
import {useApi, useI18n} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {markBackendJob} from './taskState.js';

// Shared job-list fetch + polling for the /tasks and /agent record pages.
//
// Both pages fetched the backend + local job lists, ingested them into
// AppProvider, and re-polled on an interval. The interval effect must call a
// stable ref (not loadJobs directly): loadJobs' identity changes every render
// because getJobs/ingestJobs are not memoized, so an effect that depended on it
// and ran immediately would refetch on every render — an infinite fetch loop
// that floods the backend and flickers the refresh-failed toast (2026-07-08).
//
// Keeping this in one hook stops the two pages from drifting or reintroducing
// that bug. Page-specific differences are passed in as options:
//  - hasLiveJobs: poll fast (5s) while a job is live, otherwise slow (30s).
//  - refreshFailedZh / refreshFailedEn: the page's refresh-failed wording.

const FULL_REFRESH_MS = 120000;

export function useJobPolling({hasLiveJobs, refreshFailedZh, refreshFailedEn}) {
    const {lang} = useI18n();
    const {tasks: jobs, ingestJobs} = useApp();
    const {getJobs} = useApi();

    const [loading, setLoading] = useState(() => jobs.length === 0);
    const [error, setError] = useState(null);

    // The list is every task, so re-reading all of it every 5s while one task
    // runs would re-summarise hundreds of results on the backend each time.
    // After one full read, a poll asks only for rows written since the newest
    // `updated_at` it has seen (the backend's own stamp, never this clock).
    // Reconcile merges and never drops a row for being absent, so a partial
    // batch is safe; deletions go through tombstones. A full read still runs
    // every FULL_REFRESH_MS, and on the refresh button, to pick up the few
    // writes that do not move `updated_at` (retention cleanup, title repair).
    const cursorRef = useRef('');
    const lastFullRef = useRef(0);
    const loadJobs = useCallback(async (options = {}) => {
        const wantsFull = options?.full === true
            || !cursorRef.current
            || Date.now() - lastFullRef.current > FULL_REFRESH_MS;
        let fetchedJobs = [];
        let failed = false;
        try {
            const fetched = await getJobs({
                sttProvider: 'local',
                updatedSince: wantsFull ? '' : cursorRef.current,
            });
            fetchedJobs = (Array.isArray(fetched) ? fetched : []).map(markBackendJob);
            if (wantsFull) lastFullRef.current = Date.now();
            fetchedJobs.forEach((job) => {
                const stamp = String(job?.updated_at || '');
                if (stamp && (!cursorRef.current || Date.parse(stamp) > Date.parse(cursorRef.current))) {
                    cursorRef.current = stamp;
                }
            });
        } catch (_) {
            failed = true;
        }
        // Push the batch into AppProvider's single list; reconcile there applies
        // owner scoping, tombstones, cancelled pins, and freshness.
        if (fetchedJobs.length) ingestJobs(fetchedJobs);
        setError(failed ? (lang === 'zh' ? refreshFailedZh : refreshFailedEn) : null);
        setLoading(false);
    }, [getJobs, lang, ingestJobs, refreshFailedZh, refreshFailedEn]);

    const loadJobsRef = useRef(loadJobs);
    useEffect(() => { loadJobsRef.current = loadJobs; }, [loadJobs]);

    useEffect(() => {
        let stale = false;
        const run = async () => { if (!stale) await loadJobsRef.current(); };
        run();
        const timer = setInterval(run, hasLiveJobs ? 5000 : 30000);
        return () => {
            stale = true;
            clearInterval(timer);
        };
    }, [hasLiveJobs]);

    return {loading, setLoading, error, setError, loadJobs, loadJobsRef};
}
