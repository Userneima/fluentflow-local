export const TASK_STATE_IDLE = 'idle';
export const TASK_STATE_UPLOADING = 'uploading';
export const TASK_STATE_QUEUED = 'queued';
export const TASK_STATE_RUNNING = 'running';
export const TASK_STATE_COMPLETED = 'completed';
export const TASK_STATE_FAILED = 'failed';
export const TASK_STATE_CANCELLED = 'cancelled';
export const TASK_STATE_CACHED_ONLY = 'cached_only';

export const TASK_STATES = new Set([
    TASK_STATE_IDLE,
    TASK_STATE_UPLOADING,
    TASK_STATE_QUEUED,
    TASK_STATE_RUNNING,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_CANCELLED,
    TASK_STATE_CACHED_ONLY,
]);

const normalizeSnapshotStatus = (status) => {
    const value = String(status || '').trim();
    if (value === 'processing') return TASK_STATE_RUNNING;
    if (value === 'done') return TASK_STATE_COMPLETED;
    if (value === 'canceled') return TASK_STATE_CANCELLED;
    return TASK_STATES.has(value) ? value : '';
};

export const normalizeTaskState = (job={}) => {
    if (job?.__cacheOnly || job?.status === TASK_STATE_CACHED_ONLY) return TASK_STATE_CACHED_ONLY;
    if (job?.queueUpload || job?.status === TASK_STATE_UPLOADING || job?.stage === TASK_STATE_UPLOADING) return TASK_STATE_UPLOADING;
    const snapshotStatus = normalizeSnapshotStatus(job?.task_snapshot?.overall_status);
    if (snapshotStatus) return snapshotStatus;
    const explicit = String(job?.task_state || job?.taskState || '').trim();
    if (TASK_STATES.has(explicit)) return explicit;
    const status = String(job?.status || '').trim();
    if (status === 'processing') return TASK_STATE_RUNNING;
    if (TASK_STATES.has(status)) return status;
    const stage = String(job?.stage || '').trim();
    if (stage === TASK_STATE_QUEUED) return TASK_STATE_QUEUED;
    if (stage && stage !== 'done' && stage !== TASK_STATE_IDLE) return TASK_STATE_RUNNING;
    if (job?.result) return TASK_STATE_COMPLETED;
    return TASK_STATE_IDLE;
};

export const isCachedOnlyTask = (job={}) => normalizeTaskState(job) === TASK_STATE_CACHED_ONLY;
export const isLiveTask = (job={}) => [TASK_STATE_UPLOADING, TASK_STATE_QUEUED, TASK_STATE_RUNNING].includes(normalizeTaskState(job));
export const isTerminalTask = (job={}) => [TASK_STATE_COMPLETED, TASK_STATE_FAILED, TASK_STATE_CANCELLED, TASK_STATE_CACHED_ONLY].includes(normalizeTaskState(job));

export const markCachedOnlyJob = (job) => (
    job && typeof job === 'object'
        ? {...job, task_state: TASK_STATE_CACHED_ONLY}
        : job
);

export const markBackendJob = (job) => {
    if (!job || typeof job !== 'object') return job;
    const {__cacheOnly, task_state, taskState, ...backendJob} = job;
    return {...backendJob, task_state: normalizeTaskState(backendJob)};
};
