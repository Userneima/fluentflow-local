import {noteProgressDetail, noteProgressShort, noteProgressStatus} from './noteProgress.js';
import {msgs} from '../app/shared.jsx';

// Translates FluentFlow's own message keys, English when a language lacks one.
export const noteProgressT = (lang) => (key) => msgs[lang]?.[key] ?? msgs.en[key] ?? key;

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

// What a pipeline stage is called, for the records card and the processing
// page alike; '' for a stage it does not know. Two maps of their own drifted:
// the correction stages had no name in either, and a task waiting minutes on
// the word-list correction read as the generic 处理中 at 60% (2026-11-21).
export const stageName = (stage, lang = 'zh', {audioOnly = false} = {}) => {
    const zh = lang === 'zh';
    const names = {
        upload: zh ? '接收材料' : 'Receiving',
        queued: zh ? '排队中' : 'Queued',
        resolving: zh ? '解析链接' : 'Resolving link',
        downloading: audioOnly ? (zh ? '下载音频' : 'Downloading audio') : (zh ? '下载视频' : 'Downloading video'),
        saving: zh ? '保存来源' : 'Saving source',
        // Named because it is long: without a name it read as a hung task.
        prepare_media: zh ? '剪掉气口（要几分钟）' : 'Removing breath gaps (minutes)',
        render_video: zh ? '等剪后视频导出完' : 'Finishing the cut video',
        // "提取音频" from an audio file tells the user the product thinks they
        // gave it a video. It is still converting, just not extracting.
        audio: audioOnly ? (zh ? '准备音频' : 'Preparing audio') : (zh ? '提取音频' : 'Extracting audio'),
        stt: zh ? '转录中' : 'Transcribing',
        subtitle_correction: zh ? '按画面字幕校正' : 'Checking against on-screen text',
        glossary_correction: zh ? '按词表校正' : 'Correcting from the word list',
        translation: zh ? '翻译字幕' : 'Translating subtitles',
        transcript_cleanup: zh ? '整理转录' : 'Tidying the transcript',
        transcript_ready: zh ? '整理转录' : 'Transcript ready',
        summary: zh ? '生成笔记' : 'Generating note',
        note: zh ? '写笔记' : 'Writing the note',
        export: zh ? '导出飞书' : 'Exporting',
    };
    return names[stage] || '';
};

// The task's state as one word, the same on every page that lists tasks.
// A task whose transcript is done while its note is still being written. The
// backend counts it completed (the transcript is usable), but showing a green
// "已完成" beside "正在写…" told people the note was ready when it was not.
export const isNoteStillBeingWritten = (job = {}) => {
    const state = normalizeTaskState(job);
    if (state !== TASK_STATE_COMPLETED) return false;
    if (hasActivity(job)) return Boolean(job.activity.busy) && job.activity.current_step === 'note';
    const pending = (value) => String(value || '').toLowerCase() === 'pending';
    return pending(job?.result?.summary_status) || pending(job?.summary_status);
};

// What the backend says the task is still doing, read from its steps
// (backend/core/task_activity.py). Records cached before it existed have none.
const hasActivity = (job = {}) => Boolean(job?.activity) && typeof job.activity.busy === 'boolean';

// A finished-transcript task whose later steps (cut, note, compressed copy,
// Feishu) are still running, with what it is doing in the user's words.
export const stillWorkingLabel = (job = {}) => {
    if (normalizeTaskState(job) !== TASK_STATE_COMPLETED || !hasActivity(job) || !job.activity.busy) return '';
    return String(job.activity.label || '');
};

// 「写笔记中」, with how far the writer has got when it says (backend
// note_progress), while the note is being written; '' otherwise. The task
// list's card and every status badge read it from here.
export const noteWritingLabel = (job = {}, lang = 'zh') => {
    const progress = job?.activity?.note_progress || null;
    if (!progress && !isNoteStillBeingWritten(job)) return '';
    const short = noteProgressShort(progress, noteProgressT(lang), lang);
    const head = lang === 'zh' ? '写笔记中' : 'Writing note';
    return short ? `${head} · ${short}` : head;
};

// The sentence for a task whose transcript is stored (the service counts it
// completed) while its note is being written: said as both, with how far the
// writer has got when it reports (FluentFlow's own writer does; Claude and the
// user's agent do not). '' for any other task. The owner saw 「已完成」 and an
// empty note for the 7 minutes the note took (2026-11-21).
export const noteWritingSentence = (job = {}, lang = 'zh') => {
    if (!isNoteStillBeingWritten(job)) return '';
    const zh = lang === 'zh';
    const progress = job?.activity?.note_progress || null;
    if (!progress) return zh ? '转录已完成，正在写笔记。' : 'Transcript done; writing the note.';
    // The writer's own line already says it is writing the note.
    const t = noteProgressT(lang);
    return `${zh ? '转录已完成。' : 'Transcript done. '}${noteProgressStatus(progress, t, lang)}${zh ? '，' : ', '}${noteProgressDetail(progress, t, lang)}`;
};

export const taskStatusLabel = (job, lang = 'zh') => {
    const state = normalizeTaskState(job);
    const zh = lang === 'zh';
    if (state === TASK_STATE_UPLOADING) return zh ? '上传中' : 'Uploading';
    if (state === TASK_STATE_QUEUED) return zh ? '排队中' : 'Queued';
    const writing = noteWritingLabel(job, lang);
    if (writing) return writing;
    const working = stillWorkingLabel(job);
    if (working) return zh ? working : 'Working';
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
// kind names the case (filters use it); tone is its colour: 'live' |
// 'failed' | 'warn' | 'done', and `done` is the only quiet one.
export const cutsToJudge = (job = {}) => Number(job?.result?.debreath?.survey?.to_judge) || 0;

// How many places of the cut wait for the user's judgement: items of the
// report's judgement group without an answer. The full result carries the
// report and the answers; a records-list copy carries only the count.
export const cutItemsToJudge = (result) => {
    const survey = result?.debreath?.survey;
    if (!survey || typeof survey !== 'object') return 0;
    const sections = Array.isArray(survey.report?.sections) ? survey.report.sections : null;
    if (!sections) return Math.max(0, Number(survey.to_judge) || 0);
    const verdicts = survey.verdicts && typeof survey.verdicts === 'object' ? survey.verdicts : {};
    return sections
        .filter((section) => section?.judge)
        .flatMap((section) => section.items || [])
        .filter((item) => item && !verdicts[item.key])
        .length;
};

// A task the user stopped (a step reads stopped and nothing is working).
export const isStopped = (job = {}) => hasActivity(job) && !job.activity.busy && Boolean(job.activity.stopped);

const stepLabel = (step, lang) => (lang === 'zh' ? String(step?.label || '') : String(step?.step || ''));

// What carrying the task on would do, when the page should offer it:
// 接着做 for a stopped task, 重做这一步 for a finished one whose step failed.
// The step it starts from comes from the backend (activity.resume_from).
export const resumeOffer = (job = {}, lang = 'zh') => {
    const zh = lang === 'zh';
    if (!hasActivity(job) || job.activity.busy || !job.activity.resume_from) return null;
    const step = stepLabel(job.activity.resume_from, lang);
    if (isStopped(job)) {
        return {kind: 'resume', label: zh ? '接着做' : 'Carry on', sentence: zh ? `停在了「${step}」这一步。` : `Stopped at ${step}.`};
    }
    const state = normalizeTaskState(job);
    const failed = Array.isArray(job.activity.failed_steps) && job.activity.failed_steps.length > 0;
    if ([TASK_STATE_COMPLETED, TASK_STATE_CACHED_ONLY].includes(state) && failed) {
        return {kind: 'redo', label: zh ? '重做这一步' : 'Redo this step', sentence: zh ? `「${step}」这一步没做成。` : `${step} did not finish.`};
    }
    return null;
};

// How long the task's note is, in characters; 0 when FluentFlow holds no note
// (only a transcript, the note skipped, failed or not written yet). The list
// carries only the note's first characters, so its length comes separately.
export const noteCharsOf = (job = {}) => {
    const result = job?.result || {};
    return Number.isFinite(result.summary_chars)
        ? result.summary_chars
        : String(result.summary_markdown || '').trim().length;
};

export const taskOutcome = (job = {}, lang = 'zh') => {
    const zh = lang === 'zh';
    const state = normalizeTaskState(job);
    if (state === TASK_STATE_FAILED) return {kind: 'failed', tone: 'failed', label: zh ? '失败' : 'Failed'};
    if (state === TASK_STATE_CANCELLED) return {kind: 'cancelled', tone: 'failed', label: zh ? '已取消' : 'Cancelled'};
    // Stopped after the transcript was stored (a cancelled one stays 已取消).
    if (isStopped(job)) return {kind: 'stopped', tone: 'warn', label: zh ? '已停下' : 'Stopped'};
    if (![TASK_STATE_COMPLETED, TASK_STATE_CACHED_ONLY].includes(state) || isNoteStillBeingWritten(job) || stillWorkingLabel(job)) {
        return {kind: 'live', tone: 'live', label: taskStatusLabel(job, lang)};
    }
    const result = job?.result || {};
    // A finished task whose cut is being redone is still producing a result.
    if (String(result.debreath?.status || '').toLowerCase() === 'running') {
        return {kind: 'live', tone: 'live', label: zh ? '正在剪气口' : 'Cutting'};
    }
    // Only the video was asked for (download_only): nothing else is missing.
    if (result.download_only) return {kind: 'downloaded', tone: 'done', label: zh ? '已下载' : 'Downloaded'};
    const noteChars = noteCharsOf(job);
    if (!noteChars && (String(result.summary_status || '').toLowerCase() === 'failed' || result.summary_error)) {
        return {kind: 'no_note', tone: 'warn', label: zh ? '笔记没生成' : 'No note'};
    }
    if (!noteChars && result.summary_skipped_reason === 'no_model_key') {
        return {kind: 'transcript_only', tone: 'warn', label: zh ? '只有转录稿' : 'Transcript only'};
    }
    if (String(result.debreath?.status || '').toLowerCase() === 'failed') {
        return {kind: 'cut_failed', tone: 'warn', label: zh ? '没去成气口' : 'Cut failed'};
    }
    const redo = resumeOffer(job, lang);
    if (redo?.kind === 'redo') {
        const step = stepLabel(job.activity.resume_from, lang);
        return {kind: 'step_failed', tone: 'warn', label: zh ? `完成，但${step}失败` : `Done, ${step} failed`};
    }
    const toJudge = cutsToJudge(job);
    if (toJudge) return {kind: 'judge', tone: 'warn', label: zh ? `${toJudge} 处待你判断` : `${toJudge} to review`};
    return {kind: 'done', tone: 'done', label: zh ? '已完成' : 'Completed'};
};

export const isLiveTask = (job={}) => [TASK_STATE_UPLOADING, TASK_STATE_QUEUED, TASK_STATE_RUNNING].includes(normalizeTaskState(job));


export const markBackendJob = (job) => {
    if (!job || typeof job !== 'object') return job;
    const {__cacheOnly: _cacheOnly, task_state: _taskState, taskState: _taskStateCamel, ...backendJob} = job;
    return {...backendJob, task_state: normalizeTaskState(backendJob)};
};
