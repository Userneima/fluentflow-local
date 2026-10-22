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

// The task's state as one word, the same on every page that lists tasks.
// A task whose transcript is done while its note is still being written. The
// backend counts it completed (the transcript is usable), but showing a green
// "已完成" beside "正在写…" told people the note was ready when it was not.
export const isNoteStillBeingWritten = (job = {}) => {
    const state = normalizeTaskState(job);
    if (state !== TASK_STATE_COMPLETED) return false;
    const pending = (value) => String(value || '').toLowerCase() === 'pending';
    return pending(job?.result?.summary_status) || pending(job?.summary_status);
};

export const taskStatusLabel = (job, lang = 'zh') => {
    const state = normalizeTaskState(job);
    const zh = lang === 'zh';
    if (state === TASK_STATE_UPLOADING) return zh ? '上传中' : 'Uploading';
    if (state === TASK_STATE_QUEUED) return zh ? '排队中' : 'Queued';
    if (isNoteStillBeingWritten(job)) return zh ? '写笔记中' : 'Writing note';
    if (state === TASK_STATE_COMPLETED || state === TASK_STATE_CACHED_ONLY) return zh ? '已完成' : 'Completed';
    if (state === TASK_STATE_FAILED) return zh ? '失败' : 'Failed';
    if (state === TASK_STATE_CANCELLED) return zh ? '已取消' : 'Cancelled';
    return zh ? '处理中' : 'Running';
};

// What the task actually produced, for the badge on a record: the backend's
// "completed" only means the transcript exists, so a task whose cut or note
// failed read as a green 已完成 beside "没生成" (walkthrough, 2026-10-22; the
// step-by-step state model in docs/01-待办.md is the full fix).
//
// tone: 'live' | 'failed' | 'warn' | 'done'. `done` is the only quiet one;
// `quietWhenDone` lets a list skip a badge that would say nothing.
export const cutsToJudge = (job = {}) => Number(job?.result?.debreath?.survey?.to_judge) || 0;

export const taskOutcome = (job = {}, lang = 'zh') => {
    const zh = lang === 'zh';
    const state = normalizeTaskState(job);
    if (state === TASK_STATE_FAILED) return {tone: 'failed', label: zh ? '失败' : 'Failed'};
    if (state === TASK_STATE_CANCELLED) return {tone: 'failed', label: zh ? '已取消' : 'Cancelled'};
    if (![TASK_STATE_COMPLETED, TASK_STATE_CACHED_ONLY].includes(state) || isNoteStillBeingWritten(job)) {
        return {tone: 'live', label: taskStatusLabel(job, lang)};
    }
    const result = job?.result || {};
    const noteChars = Number.isFinite(result.summary_chars)
        ? result.summary_chars
        : String(result.summary_markdown || '').trim().length;
    if (!noteChars && (String(result.summary_status || '').toLowerCase() === 'failed' || result.summary_error)) {
        return {tone: 'warn', label: zh ? '笔记没生成' : 'No note'};
    }
    if (!noteChars && result.summary_skipped_reason === 'no_model_key') {
        return {tone: 'warn', label: zh ? '只有转录稿' : 'Transcript only'};
    }
    if (String(result.debreath?.status || '').toLowerCase() === 'failed') {
        return {tone: 'warn', label: zh ? '没去成气口' : 'Cut failed'};
    }
    const toJudge = cutsToJudge(job);
    if (toJudge) return {tone: 'warn', label: zh ? `${toJudge} 处待你判断` : `${toJudge} to review`};
    return {tone: 'done', label: zh ? '已完成' : 'Completed'};
};

export const isCachedOnlyTask =(job={}) => normalizeTaskState(job) === TASK_STATE_CACHED_ONLY;
export const isLiveTask = (job={}) => [TASK_STATE_UPLOADING, TASK_STATE_QUEUED, TASK_STATE_RUNNING].includes(normalizeTaskState(job));
export const isTerminalTask = (job={}) => [TASK_STATE_COMPLETED, TASK_STATE_FAILED, TASK_STATE_CANCELLED, TASK_STATE_CACHED_ONLY].includes(normalizeTaskState(job));

export const markCachedOnlyJob = (job) => (
    job && typeof job === 'object'
        ? {...job, task_state: TASK_STATE_CACHED_ONLY}
        : job
);

export const markBackendJob = (job) => {
    if (!job || typeof job !== 'object') return job;
    const {__cacheOnly: _cacheOnly, task_state: _taskState, taskState: _taskStateCamel, ...backendJob} = job;
    return {...backendJob, task_state: normalizeTaskState(backendJob)};
};
