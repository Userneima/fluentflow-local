// One record on the records page: its title, facts, progress, failure and
// resume lines, cut-file notice and processing report. Memoised on what it
// shows, so a 5-second poll that did not change a row does not render it.
import {memo, useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {AlertCircle, ChevronDown, LoaderCircle, MoreHorizontal, Pin} from 'lucide-react';
import {CutReport, hasReport} from '../components/CutReport.jsx';
import AgentCutWait from '../components/AgentCutWait.jsx';
import OutputFolderLine from '../components/OutputFolderLine.jsx';
import {progressMessageOf} from '../lib/format.js';
import {DropdownMenu} from '../app/DropdownMenu.jsx';
import {noteFallbackSentence} from '../lib/noteWriter.js';
import {
    fmtBytes,
    diagnoseTaskError,
    taskErrorContextForJob,
    isSttProgressUnmeasured,
    isDownloadProgressUnmeasured,
    jobDisplayTitle,
    jobToCurrentJob,
    isPinnedJob,
} from '../app/shared.jsx';
import {
    isLiveTask,
    normalizeTaskState,
    TASK_STATE_QUEUED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_CANCELLED,
    TASK_STATE_CACHED_ONLY,
    taskOutcome,
    noteCharsOf,
    resumeOffer,
    noteWritingLabel,
} from '../lib/taskState.js';
import {factBlock, factToggle, button, iconButton, linkButton, textButton} from '../lib/ui.js';
import {mediaSourceForJob, taskIdForJob} from './agent-tasks-jobs.js';
import {
    cutFileDelivery,
    debreathTile,
    formatTaskDateTime,
    liveStageDetail,
    noteTileValue,
    sizeAndLengthLabel,
    stageLabel,
} from './agent-tasks-labels.js';

const OUTCOME_PILL_CLASS = {
    done: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-100',
    warn: 'border-[#f5c86b] bg-[#fffaf0] text-[#8a5a00] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]',
    failed: 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300',
    live: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-200',
};

// One row per record (records page redesign, 2026-10-23): the title opens the
// editor, the facts are small blocks that wrap rather than
// truncate, the cut block opens the processing report in place, and the right
// side holds only two icon buttons in fixed slots. A finished record with
// nothing wrong carries no badge, the same rule as the start page's recent list.
const AgentTaskCardView = ({job, lang, aheadName = '', retryError = '', highlighted = false, cancellingTaskId, deletingTaskId, openingTaskId, retryingTaskId, downloadingTaskId, resumingTaskId, stoppingTaskId, pinningTaskId, onCancel, onDelete, onDownloadCut, onOpenResult, onOpenSteps, onPin, onRetry, onResume, onStop}) => {
    const zh = lang === 'zh';
    const taskId = taskIdForJob(job);
    const highlightRef = useRef(null);
    useEffect(() => {
        if (highlighted) highlightRef.current?.scrollIntoView?.({block: 'center', behavior: 'smooth'});
    }, [highlighted]);
    const [reportOpen, setReportOpen] = useState(false);
    const state = normalizeTaskState(job);
    const live = isLiveTask(job);
    const completed = state === TASK_STATE_COMPLETED || state === TASK_STATE_CACHED_ONLY;
    const failedTerminal = state === TASK_STATE_FAILED;
    const failed = failedTerminal || state === TASK_STATE_CANCELLED;
    const terminal = completed || failed;
    const cancellableLive = live && taskId && !job?.metadata?.queue_provisional;
    const progress = completed ? 100 : Math.max(0, Math.min(100, Number(job?.progress) || (state === TASK_STATE_QUEUED ? 0 : 2)));
    const current = jobToCurrentJob(job);
    const downloadUnknown = isDownloadProgressUnmeasured(job);
    const progressUnknown = isSttProgressUnmeasured(current) || downloadUnknown;
    const downloadedBytes = Number(job?.metadata?.video_source_progress?.loaded_bytes) || 0;
    const displayTitle = jobDisplayTitle(job, lang);
    const diagnosis = failed
        ? diagnoseTaskError(job?.error_reason || job?.result?.summary_error || '', lang, taskErrorContextForJob(job))
        : null;
    // A transcript file has no recording to run again: its retry always answered 404 (10-18 review, item 16).
    const retryUseless = failedTerminal && (mediaSourceForJob(job) === 'transcript_file'
        || (mediaSourceForJob(job) === 'video_link' && diagnosis && !diagnosis.retryable));
    const outcome = taskOutcome(job, lang);
    const cutTile = debreathTile(job, lang);
    const reportAvailable = hasReport(job);
    const sizeAndLength = sizeAndLengthLabel(job, lang);
    const noteHint = noteFallbackSentence(job?.result, lang);
    const delivery = cutFileDelivery(job, lang);
    const opening = openingTaskId === taskId;
    const progressLabel = progressUnknown && live
        ? (downloadUnknown && downloadedBytes > 0
            ? (zh ? `已下载 ${fmtBytes(downloadedBytes)}` : `${fmtBytes(downloadedBytes)} downloaded`)
            : (zh ? '处理中' : 'Working'))
        : `${progress}%`;
    const resume = resumeOffer(job, lang);
    // A finished transcript whose later steps still run: the step it is on can
    // be stopped and carried on later (a task still transcribing is cancelled).
    const stoppable = completed && taskId && Boolean(job?.activity?.busy);
    const pinned = isPinnedJob(job);
    const pinnable = taskId && !job?.metadata?.queue_provisional;
    const menuItems = [
        pinnable ? {label: pinned ? (zh ? '取消置顶' : 'Unpin') : (zh ? '置顶' : 'Pin to top'), disabled: pinningTaskId === taskId, onClick: () => onPin(job)} : null,
        cancellableLive ? {label: zh ? '取消任务' : 'Cancel task', disabled: cancellingTaskId === taskId, onClick: () => onCancel(job)} : null,
        stoppable ? {label: zh ? '停下这一步' : 'Stop this step', disabled: stoppingTaskId === taskId, onClick: () => onStop(job)} : null,
        // Step-by-step state and the failure diagnosis of this one task.
        taskId ? {label: zh ? '处理过程' : 'Processing steps', onClick: () => onOpenSteps(job)} : null,
        terminal && taskId ? {icon: 'delete', danger: true, label: zh ? '删除记录' : 'Delete record', onClick: () => onDelete(job)} : null,
    ].filter(Boolean);
    const menuBusy = pinningTaskId === taskId || deletingTaskId === taskId || cancellingTaskId === taskId || stoppingTaskId === taskId;
    const cutText = `${zh ? '去气口' : 'Cut'} ${cutTile.value}${cutTile.hint ? `（${cutTile.hint}）` : ''}`;
    // A failed or cancelled task shows only the steps it got to: its cut and
    // note blocks otherwise read "未剪（旧任务）" and "等处理", which say it is
    // an old task and still waiting when it is neither (review 2026-10-23).
    const result = job?.result || {};
    const noteChars = noteCharsOf(job);
    // A download-only task (已下载) has no cut or note by request: neither block shows.
    const showCut = !result.download_only && (!failed || (result.debreath && typeof result.debreath === 'object') || reportAvailable);
    const showNote = !result.download_only && (!failed || noteChars > 0);
    return (
        <article
            ref={highlightRef}
            data-highlighted={highlighted ? 'true' : undefined}
            className={`rounded-[16px] border bg-white px-4 py-3 dark:bg-white/[0.055] ${highlighted ? 'border-primary ring-2 ring-primary/30' : 'border-[#e4e1e1] dark:border-white/[0.10]'}`}
        >
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5">
                <div className="col-start-1 row-start-1 flex min-w-0 items-start gap-2 max-[699px]:col-span-2">
                    {pinned ? (
                        <span data-testid="task-pinned" title={zh ? '已置顶' : 'Pinned'} className="mt-[3px] inline-flex shrink-0 text-[#85868c] dark:text-white/50">
                            <Pin className="size-4" strokeWidth={2.15} aria-label={zh ? '已置顶' : 'Pinned'}/>
                        </span>
                    ) : null}
                    {/* A plain 已完成 needs no badge; 已下载 says the task stopped at the video on purpose. */}
                    {outcome.kind !== 'done' ? (
                        <span data-testid="task-outcome" className={`mt-[1px] shrink-0 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-extrabold ${OUTCOME_PILL_CLASS[outcome.tone]}`}>
                            {outcome.label}
                        </span>
                    ) : null}
                    <h2 className="min-w-0 font-headline text-[15px] font-extrabold leading-[22px] text-[#111111] dark:text-white" title={displayTitle}>
                        {completed ? (
                            <button
                                type="button"
                                disabled={opening}
                                onClick={() => onOpenResult(job)}
                                className={`${textButton} line-clamp-2 break-words`}
                            >
                                {displayTitle}
                            </button>
                        ) : (
                            <span className="line-clamp-2 break-words">{displayTitle}</span>
                        )}
                    </h2>
                    {opening ? <LoaderCircle className="mt-1 size-4 shrink-0 animate-spin text-[#85868c]" strokeWidth={2.15}/> : null}
                </div>
                <div data-testid="task-facts" className="col-start-1 row-start-2 flex min-w-0 flex-wrap items-center gap-1.5">
                    <span className="mr-0.5 whitespace-nowrap text-[12px] font-semibold text-[#85868c] dark:text-white/50">
                        {formatTaskDateTime(job?.updated_at || job?.created_at, lang)}
                    </span>
                    {terminal ? (
                        <>
                            {sizeAndLength ? <span className={factBlock}>{sizeAndLength}</span> : null}
                            {!showCut ? null : reportAvailable ? (
                                <button
                                    type="button"
                                    aria-expanded={reportOpen}
                                    onClick={() => setReportOpen((value) => !value)}
                                    title={zh ? (reportOpen ? '收起处理说明' : '看处理说明') : (reportOpen ? 'Hide processing report' : 'Processing report')}
                                    className={factToggle(reportOpen)}
                                >
                                    {cutText}
                                    <ChevronDown className={`size-3.5 transition-transform ${reportOpen ? 'rotate-180' : ''}`} strokeWidth={2.4}/>
                                </button>
                            ) : (
                                <span className={factBlock}>{cutText}</span>
                            )}
                            {showNote ? <span className={factBlock}>{zh ? '笔记' : 'Note'} {noteTileValue(job, lang)}</span> : null}
                        </>
                    ) : (
                        <span className={factBlock}>{stageLabel(job, lang)}{noteWritingLabel(job, lang) ? '' : ` · ${progressLabel}`}</span>
                    )}
                </div>
                <div className="col-start-2 row-start-1 row-span-2 flex items-start gap-2 self-start max-[699px]:row-start-2 max-[699px]:row-span-1">
                    {/* Fixed slots: a record without an output folder keeps the
                        space, so the menu button lines up down the list. */}
                    <span className="inline-flex size-9">
                        <OutputFolderLine taskId={taskId} result={job?.result} lang={lang} compact/>
                    </span>
                    <span className="inline-flex size-9">
                        {menuItems.length ? (
                            <DropdownMenu
                                trigger={(
                                    <button type="button" disabled={menuBusy} aria-label={zh ? '更多' : 'More'} title={zh ? '更多' : 'More'} className={iconButton()}>
                                        {menuBusy ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <MoreHorizontal className="size-4" strokeWidth={2.15}/>}
                                    </button>
                                )}
                                items={menuItems}
                            />
                        ) : null}
                    </span>
                </div>
            </div>
            {!completed && !failed ? (
                <div className="mt-3">
                    <div className={`h-2 overflow-hidden rounded-full bg-[#efeeee] dark:bg-white/[0.12] ${progressUnknown && live ? 'progress-indeterminate' : ''}`}>
                        {!progressUnknown && <div className="h-full rounded-full bg-[#111111] transition-all duration-500 dark:bg-white" style={{width: `${progress}%`}}/>}
                    </div>
                    <p className="mt-2 text-[12px] font-semibold leading-5 text-[#57585d] dark:text-white/60">
                        {liveStageDetail(job, lang, aheadName)}
                    </p>
                </div>
            ) : null}
            {failedTerminal && diagnosis?.detail ? (
                <div className="mt-2 flex max-w-full flex-wrap items-start gap-x-3 gap-y-1.5 rounded-[12px] border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold leading-5 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 flex-1 break-words">{diagnosis.detail}</span>
                    {diagnosis.settingsTarget ? (
                        <Link to={diagnosis.settingsTarget} className={`shrink-0 ${linkButton()}`}>
                            {zh ? '去设置修改' : 'Change it in Settings'}
                        </Link>
                    ) : null}
                    {diagnosis.pictureNote ? (
                        <button type="button" disabled={retryingTaskId === taskId} onClick={() => onRetry(job, {pictureNote: true})} className={`shrink-0 ${linkButton()}`}>
                            {retryingTaskId === taskId ? (zh ? '正在入队…' : 'Queuing…') : (zh ? '照画面写笔记' : 'Write from the picture')}
                        </button>
                    ) : !retryUseless ? (
                        <button type="button" disabled={retryingTaskId === taskId} onClick={() => onRetry(job)} className={`shrink-0 ${linkButton()}`}>
                            {retryingTaskId === taskId ? (zh ? '正在入队…' : 'Queuing…') : (zh ? '重新提交' : 'Submit again')}
                        </button>
                    ) : null}
                </div>
            ) : null}
            {resume ? (
                <div data-testid="resume-offer" className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px] font-semibold leading-5 text-[#57585d] dark:text-white/60">
                    <span className="min-w-0 break-words">{resume.sentence}</span>
                    <button type="button" disabled={resumingTaskId === taskId} onClick={() => onResume(job)} className={`shrink-0 ${button('secondary', 'sm')}`}>
                        {resumingTaskId === taskId ? (zh ? '正在入队…' : 'Queuing…') : resume.label}
                    </button>
                </div>
            ) : null}
            {retryError ? (
                <p data-testid="retry-error" className="mt-2 inline-flex max-w-full items-start gap-2 rounded-[12px] border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold leading-5 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 break-words">{retryError}</span>
                </p>
            ) : null}
            {noteHint ? (
                <p className="mt-1.5 text-[12px] font-semibold leading-5 text-[#85868c] dark:text-white/45">{noteHint}</p>
            ) : null}
            {delivery ? (
                <div className={`mt-2 flex flex-wrap items-start gap-2 rounded-[12px] border px-3 py-2 text-[12px] font-semibold leading-5 ${
                    delivery.tone === 'error'
                        ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200'
                        : 'border-[#f5c86b] bg-[#fffaf0] text-[#8a5a00] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]'
                }`}>
                    <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 flex-1 break-words">{delivery.text}</span>
                    {job?.result?.artifacts?.debreath_media && (
                        <button
                            type="button"
                            disabled={downloadingTaskId === taskId}
                            onClick={() => onDownloadCut?.(job)}
                            className={`ml-auto ${button('secondary', 'sm')}`}
                        >
                            {zh ? '下载剪后文件' : 'Download the cut file'}
                        </button>
                    )}
                </div>
            ) : null}
            <AgentCutWait job={job}/>
            {reportOpen ? (
                <div data-testid="cut-report-panel" className="mt-3 rounded-[12px] border border-[#dedada] bg-[#fbfbfb] px-3 py-3 dark:border-white/[0.10] dark:bg-white/[0.04]">
                    <CutReport job={job} embedded/>
                </div>
            ) : null}
        </article>
    );
};

// What the card shows, as one string. Every 5-second poll rebuilds the list
// with fresh objects; a card whose row did not change must not render again.
const cardRenderKey = (job) => {
    const result = job?.result || {};
    const debreath = result.debreath || {};
    const meta = job?.metadata || {};
    return [
        taskIdForJob(job),
        job?.updated_at, job?.created_at, job?.status, job?.task_state, job?.stage, job?.progress,
        job?.error_reason, job?.source_filename, job?.source_file_size_mb, job?.source_duration_seconds,
        !!job?.result, result.summary_chars, result.summary_status, result.summary_error, result.summary_skipped,
        result.summary_written_from, String(result.summary_markdown || '').length,
        result.note_written_by, result.note_fallback_reason, result.agent_cut?.status, result.agent_cut?.fallback_reason,
        !!result.artifacts?.debreath_media,
        debreath.status, debreath.delivered, debreath.delivery_error, debreath.used_for_transcription,
        debreath.already_cut, debreath.not_worth_rendering, debreath.render_verified, debreath.rendered,
        debreath.plan?.cut_count, debreath.plan?.removed_seconds,
        meta.display_title, meta.pinned_at, meta.queue_position, meta.queue_total, meta.queue_provisional,
        meta.queue_wait?.waiting_for, meta.queue_wait?.since,
        meta.video_source_progress?.message, meta.video_source_progress?.loaded_bytes,
        meta.video_source_progress?.total_bytes, meta.video_source_progress?.percent,
        progressMessageOf(job),
        job?.task_snapshot?.current_step,
        job?.activity?.busy, job?.activity?.label, job?.activity?.stopped, job?.activity?.resume_from?.step,
        (job?.activity?.failed_steps || []).map((step) => step?.step).join(','),
        job?.sttStatus, job?.stt_status, meta.stt_status,
    ].join('\u0001');
};
// The busy ids are page-wide; a card only cares whether they name it.
const BUSY_KEYS = ['pinningTaskId', 'cancellingTaskId', 'deletingTaskId', 'openingTaskId', 'retryingTaskId', 'downloadingTaskId', 'resumingTaskId', 'stoppingTaskId'];
const areCardPropsEqual = (prev, next) => {
    if (prev.lang !== next.lang || prev.aheadName !== next.aheadName || prev.retryError !== next.retryError || prev.highlighted !== next.highlighted) return false;
    const taskId = taskIdForJob(next.job);
    for (const key of BUSY_KEYS) {
        if ((prev[key] === taskId) !== (next[key] === taskId)) return false;
    }
    // Handlers are stable (see the ref in useTaskActions), so they are not compared.
    return cardRenderKey(prev.job) === cardRenderKey(next.job);
};
export default memo(AgentTaskCardView, areCardPropsEqual);
