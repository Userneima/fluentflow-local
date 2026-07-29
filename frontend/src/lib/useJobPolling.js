import {useCallback, useEffect, useRef, useState} from 'react';
import {useApi, useAuth, useI18n} from '../app/shared.jsx';
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
//  - errorOnAllOnly: /agent warns only when every fetch fails; /tasks warns on
//    any failure.
//  - refreshFailedZh / refreshFailedEn: the page's refresh-failed wording.
export function useJobPolling({hasLiveJobs, errorOnAllOnly = false, refreshFailedZh, refreshFailedEn}) {
    const {lang} = useI18n();
    const {authMode, user} = useAuth();
    const {tasks: jobs, ingestJobs} = useApp();
    const {getJobs} = useApi();

    const canUseTaskCache = authMode !== 'accounts' || !!user?.id;
    const [loading, setLoading] = useState(() => canUseTaskCache && jobs.length === 0);
    const [error, setError] = useState(null);

    const loadJobs = useCallback(async () => {
        if (!canUseTaskCache) {
            setLoading(false);
            return;
        }
        const results = await Promise.allSettled([
            getJobs(100),
            getJobs(100, {sttProvider: 'local'}),
        ]);
        const fetchedJobs = results
            .filter((result) => result.status === 'fulfilled')
            .flatMap((result) => Array.isArray(result.value) ? result.value : [])
            .map(markBackendJob);
        const failedFetches = results.filter((result) => result.status === 'rejected');
        const shouldWarn = errorOnAllOnly ? failedFetches.length === results.length : failedFetches.length > 0;
        // Push the batch into AppProvider's single list; reconcile there applies
        // owner scoping, tombstones, cancelled pins, and freshness.
        if (fetchedJobs.length) ingestJobs(fetchedJobs);
        setError(shouldWarn ? (lang === 'zh' ? refreshFailedZh : refreshFailedEn) : null);
        setLoading(false);
    }, [canUseTaskCache, getJobs, lang, ingestJobs, errorOnAllOnly, refreshFailedZh, refreshFailedEn]);

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

    return {loading, setLoading, error, setError, loadJobs, loadJobsRef, canUseTaskCache};
}
