// Which records the page lists and how many: the merged list, the filters and
// the title search over it, the live counts, and where the pinned ones end.
import {useEffect, useMemo, useState} from 'react';
import {jobDisplayTitle, isPinnedJob} from '../app/shared.jsx';
import {normalizeTaskState, TASK_STATE_QUEUED, taskOutcome, noteCharsOf} from '../lib/taskState.js';
import {jobsFromCurrentJob, mergeJobs, taskIdForJob} from './agent-tasks-jobs.js';

export const RECORD_FILTERS = [
    {id: 'all', zh: '全部', en: 'All', match: () => true},
    // Records with a note in FluentFlow. Most records are an agent's batch of
    // short clips it only wanted the transcript of (the note it wrote lives
    // elsewhere); this view keeps the ones read here (2026-11-19, decision 6.77).
    {id: 'notes', zh: '有笔记', en: 'With notes', match: (job) => noteCharsOf(job) > 0},
    // Anything still producing a result: queued or running, and a finished
    // task whose note is being written or whose cut is being redone.
    {id: 'live', zh: '进行中', en: 'In progress', match: (job) => taskOutcome(job).kind === 'live'},
    {id: 'judge', zh: '待你判断', en: 'To review', match: (job) => taskOutcome(job).kind === 'judge'},
    // Failed, cancelled, or finished without the note or the cut.
    {id: 'problem', zh: '有问题', en: 'Problems', match: (job) => !['live', 'judge', 'done'].includes(taskOutcome(job).kind)},
];

// The 全部 / 有笔记 choice is kept in this browser, so the page opens the way it
// was left; the other filters are a look at something now and are not kept.
const KEPT_FILTERS = new Set(['all', 'notes']);
const FILTER_STORAGE_KEY = 'fluentflow_records_filter';
const readKeptFilter = () => {
    try {
        const value = window.localStorage.getItem(FILTER_STORAGE_KEY);
        return KEPT_FILTERS.has(value) ? value : 'all';
    } catch {
        return 'all';
    }
};
const keepFilter = (value) => {
    if (!KEPT_FILTERS.has(value)) return;
    try { window.localStorage.setItem(FILTER_STORAGE_KEY, value); } catch { /* not kept; the page still works */ }
};

export const useRecordList = ({currentJob, jobs, lang, location, highlightTaskId}) => {
    const queueUploadJob = currentJob?.queueUpload ? currentJob : null;
    const currentJobRecords = useMemo(() => jobsFromCurrentJob(currentJob), [currentJob]);
    // The shared list already reflects history + cache; merge in the live
    // currentJob record for immediate progress display.
    // Every task is listed and counted; there is no display window.
    const displayJobs = useMemo(() => (
        mergeJobs(currentJobRecords, jobs)
    ), [currentJobRecords, jobs]);
    // Same rule as the 进行中 filter, so the count, the filter and the polling agree.
    const liveJobs = useMemo(() => displayJobs.filter((job) => taskOutcome(job).kind === 'live'), [displayJobs]);
    // The name of whichever task a waiting one is behind. Looked up here because
    // only the list knows both rows; the waiting job's own record carries the id
    // and nothing a reader would recognise.
    const titleByTaskId = useMemo(() => {
        const byId = new Map();
        displayJobs.forEach((job) => {
            const id = taskIdForJob(job);
            if (id) byId.set(id, jobDisplayTitle(job, lang));
        });
        return byId;
    }, [displayJobs, lang]);
    const queueAheadName = (job) => (
        titleByTaskId.get(String(job?.metadata?.queue_wait?.waiting_for || '').trim()) || ''
    );
    const hasLiveOrUploadingJobs = Boolean(queueUploadJob) || liveJobs.length > 0;
    // Search by title and a few filters over the whole list; which filter a
    // record falls under comes from the same outcome its badge shows.
    const [query, setQuery] = useState('');
    // Sent here to look at one task (a link already submitted): show every
    // record, or the filter could hide the very task pointed at.
    const [filter, setFilterState] = useState(() => (location.state?.highlightTaskId ? 'all' : readKeptFilter()));
    const setFilter = (value) => {
        setFilterState(value);
        keepFilter(value);
    };
    useEffect(() => {
        if (highlightTaskId) setFilterState('all');
    }, [highlightTaskId]);
    const filterCounts = useMemo(() => {
        const counts = {all: displayJobs.length};
        RECORD_FILTERS.forEach((item) => {
            if (item.id !== 'all') counts[item.id] = displayJobs.filter((job) => item.match(job)).length;
        });
        return counts;
    }, [displayJobs]);
    const visibleJobs = useMemo(() => {
        const needle = query.trim().toLowerCase();
        const rule = RECORD_FILTERS.find((item) => item.id === filter) || RECORD_FILTERS[0];
        return displayJobs.filter((job) => (
            rule.match(job)
            && (!needle || jobDisplayTitle(job, lang).toLowerCase().includes(needle))
        ));
    }, [displayJobs, filter, query, lang]);
    // Where the pinned records end: a small label above the first record after
    // them, so the line between "pinned" and "the rest" is visible.
    const restStartTaskId = useMemo(() => {
        const firstRest = visibleJobs.findIndex((job) => !isPinnedJob(job) && taskOutcome(job).kind !== 'live');
        if (firstRest <= 0 || !isPinnedJob(visibleJobs[firstRest - 1])) return '';
        return taskIdForJob(visibleJobs[firstRest]);
    }, [visibleJobs]);
    const queuedCount = liveJobs.filter((job) => normalizeTaskState(job) === TASK_STATE_QUEUED).length;
    const runningCount = liveJobs.length - queuedCount;
    return {
        queueUploadJob, displayJobs, queueAheadName, hasLiveOrUploadingJobs,
        query, setQuery, filter, setFilter, filterCounts, visibleJobs, restStartTaskId,
        queuedCount, runningCount,
    };
};
