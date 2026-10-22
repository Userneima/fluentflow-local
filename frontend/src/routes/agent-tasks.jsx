import {memo, useEffect, useMemo, useRef, useState} from 'react';
import {Link, useLocation, useNavigate} from 'react-router-dom';
import {
    AlertCircle,
    CheckCircle2,
    FileText,
    History,
    LoaderCircle,
    MoreHorizontal,
    Plus,
    RefreshCw,
    Search,
    SlidersHorizontal,
    XCircle,
} from 'lucide-react';
import {CutReport, hasReport} from '../components/CutReport.jsx';
import AgentCutWait from '../components/AgentCutWait.jsx';
import OutputFolderLine from '../components/OutputFolderLine.jsx';
import {fmtDuration, progressMessageOf} from '../lib/format.js';
import {DropdownMenu} from '../app/DropdownMenu.jsx';
import {noteFallbackSentence} from '../lib/noteWriter.js';
import {
    fmtBytes,
    fmtFileSize,
    diagnoseTaskError,
    friendlyTaskError,
    taskErrorContextForJob,
    isSttProgressUnmeasured,
    isDownloadProgressUnmeasured,
    jobDisplayTitle,
    jobToHistoryEntry,
    jobToCurrentJob,
    sortJobsForHistoryView,
    useApi,
    useI18n,
    useSettings,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {
    isLiveTask,
    markBackendJob,
    normalizeTaskState,
    TASK_STATE_QUEUED,
    TASK_STATE_RUNNING,
    TASK_STATE_UPLOADING,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_CANCELLED,
    TASK_STATE_CACHED_ONLY,
    taskStatusLabel,
    taskOutcome,
    cutsToJudge,
} from '../lib/taskState.js';
import {useJobPolling} from '../lib/useJobPolling.js';
import {extraUrlsIgnoredNotice, retryInputForJob, retryOptionsForJob} from '../lib/linkRetry.js';

const taskIdForJob = (job) => String(job?.task_id || job?.result?.task_id || '').trim();

const isLocalJob = (job) => String(job?.client_id || '').startsWith('local-') || job?.metadata?.stt_provider === 'local';

const mediaSourceForJob = (job) => {
    const sourceType = String(job?.source_type || job?.result?.source || '').toLowerCase();
    const filename = String(job?.source_filename || job?.result?.filename || '').toLowerCase();
    if (sourceType === 'video_link') return 'video_link';
    if (sourceType === 'transcript_file') return 'transcript_file';
    if (sourceType === 'audio_file' || /\.(mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i.test(filename)) return 'media_file';
    if (sourceType === 'video_file' || sourceType === 'queue_upload' || /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(filename)) return 'media_file';
    return '';
};

const formatTaskDateTime = (value, lang) => {
    const date = new Date(value || '');
    if (Number.isNaN(date.getTime())) return lang === 'zh' ? '时间未知' : 'Unknown time';
    try {
        return new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en-US', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        }).format(date).replace(/\//g, '-').replace(',', '');
    } catch (_) {
        return lang === 'zh' ? '时间未知' : 'Unknown time';
    }
};

const taskProcessingTimeLabel = (job, lang) => {
    const result = job?.result || {};
    const metadata = job?.metadata || {};
    const explicitElapsed = Number(
        result.stt_elapsed_seconds
        || metadata.stt_elapsed_seconds
        || metadata.total_duration_seconds
        || 0
    );
    const createdAt = Date.parse(job?.created_at || '');
    const updatedAt = Date.parse(job?.updated_at || '');
    const fallbackElapsed = Number.isFinite(createdAt) && Number.isFinite(updatedAt) && updatedAt > createdAt
        ? (updatedAt - createdAt) / 1000
        : 0;
    const elapsed = explicitElapsed > 0 ? explicitElapsed : fallbackElapsed;
    if (!elapsed) return statusLabel(job, lang);
    const sourceDuration = Number(
        result.audio_duration_seconds
        || job?.source_duration_seconds
        || metadata.duration_seconds
        || 0
    );
    const explicitFactor = Number(result.stt_realtime_factor || metadata.stt_realtime_factor || 0);
    const factor = explicitFactor > 0 ? explicitFactor : (sourceDuration > 0 ? elapsed / sourceDuration : 0);
    const elapsedLabel = fmtDuration(elapsed, lang);
    if (factor > 0) {
        const percent = Math.max(1, Math.round(factor * 100));
        return lang === 'zh'
            ? `${elapsedLabel}（占原时长 ${percent}%）`
            : `${elapsedLabel} (${percent}% of original)`;
    }
    return lang === 'zh' ? `${elapsedLabel}（处理耗时）` : `${elapsedLabel} elapsed`;
};

const jobsFromCurrentJob = (currentJob) => {
    if (!currentJob) return [];
    if (currentJob.queueUpload) {
        const items = Array.isArray(currentJob.queueItems) ? currentJob.queueItems : [];
        return items.map((item, index) => {
            const taskId = item.taskId || item.task_id || item.provisionalId || `queue-upload-${index + 1}`;
            const fileName = item.fileName || item.filename || taskId;
            const hasBackendTask = !!(item.taskId || item.task_id);
            const taskState = item.taskState || item.status || (hasBackendTask ? TASK_STATE_QUEUED : TASK_STATE_UPLOADING);
            return {
                task_id: taskId,
                status: taskState,
                task_state: taskState,
                stage: item.stage || (hasBackendTask ? 'queued' : currentJob.stage || 'upload'),
                progress: item.progress ?? (hasBackendTask ? 0 : currentJob.progress ?? 2),
                source_type: item.sourceType || item.source_type || currentJob.sourceType || null,
                source_filename: fileName,
                source_file_size_mb: item.fileSizeMb ?? item.source_file_size_mb ?? null,
                created_at: currentJob.startedAt ? new Date(currentJob.startedAt).toISOString() : new Date().toISOString(),
                metadata: {
                    display_title: fileName,
                    queue_position: item.queuePosition || item.queue_position || index + 1,
                    queue_total: item.queueTotal || item.queue_total || currentJob.queueTotal || items.length,
                    queue_provisional: !hasBackendTask,
                    stt_provider: currentJob.sttProvider || null,
                },
            };
        });
    }
    if (!currentJob.taskId) return [];
    return [{
        task_id: currentJob.taskId,
        status: currentJob.taskState || (currentJob.stage === 'done' ? 'completed' : TASK_STATE_RUNNING),
        task_state: currentJob.taskState || (currentJob.stage === 'done' ? 'completed' : TASK_STATE_RUNNING),
        stage: currentJob.stage || 'queued',
        progress: currentJob.progress ?? 0,
        source_type: currentJob.sourceType || null,
        source_filename: currentJob.fileName || currentJob.taskId,
        source_file_size_mb: currentJob.fileSizeMb || null,
        created_at: currentJob.startedAt ? new Date(currentJob.startedAt).toISOString() : new Date().toISOString(),
        metadata: {
            display_title: currentJob.fileName || currentJob.taskId,
            stt_provider: currentJob.sttProvider || null,
            video_source_progress: currentJob.videoSourceProgress || null,
        },
    }];
};

const mergeJobs = (...groups) => {
    const byId = new Map();
    groups.flat().forEach((job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        const existing = byId.get(taskId);
        const nextTs = Date.parse(job?.updated_at || job?.created_at || '') || 0;
        const existingTs = Date.parse(existing?.updated_at || existing?.created_at || '') || 0;
        if (!existing || nextTs >= existingTs) byId.set(taskId, job);
    });
    return sortJobsForHistoryView(Array.from(byId.values()));
};

const statusLabel = taskStatusLabel;

// Whether this task's material has no picture. Copy that says "提取音频" or "录像"
// about an uploaded .m4a is not a translation slip — it tells the user the product
// misunderstood what they gave it.
export const isAudioOnlySource = (job) => {
    const type = String(job?.source_type || job?.result?.source || '').toLowerCase();
    if (type.startsWith('video') || type === 'queue_upload') return false;
    if (type === 'audio' || type === 'audio_file') return true;
    const name = String(job?.source_filename || job?.result?.filename || '');
    if (/\.(mp4|mov|avi|mkv|webm|m4v|flv|wmv|mpe?g)$/i.test(name)) return false;
    return /\.(mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i.test(name);
};

const stageLabel = (job, lang) => {
    const isZh = lang === 'zh';
    const audioOnly = isAudioOnlySource(job);
    const labels = {
        upload: isZh ? '接收材料' : 'Receiving',
        queued: isZh ? '等待开始' : 'Waiting',
        resolving: isZh ? '解析链接' : 'Resolving link',
        downloading: audioOnly
            ? (isZh ? '下载音频' : 'Downloading audio')
            : (isZh ? '下载视频' : 'Downloading video'),
        saving: isZh ? '保存来源' : 'Saving source',
        // Named in this map because the pipeline has this stage now; without it the
        // card fell through to the generic status and a working cut read as stalled.
        prepare_media: isZh ? '剪掉气口（要几分钟）' : 'Removing breath gaps (minutes)',
        render_video: isZh ? '等剪后视频导出完' : 'Finishing the cut video',
        // "提取音频" from an audio file is the product telling the user it thinks
        // they uploaded a video. It is still converting — just not extracting.
        audio: audioOnly
            ? (isZh ? '准备音频' : 'Preparing audio')
            : (isZh ? '提取音频' : 'Extracting audio'),
        stt: isZh ? '语音转写' : 'Transcribing',
        transcript_ready: isZh ? '整理转录' : 'Transcript ready',
        summary: isZh ? '生成笔记' : 'Generating note',
        export: isZh ? '导出飞书' : 'Exporting',
    };
    return labels[job?.stage] || statusLabel(job, lang);
};

// Which task this one is behind, and how long it has been behind it.
//
// Recordings are processed one at a time, so most of "queued" is an ordinary
// wait — but the card said the same words whether the queue was moving or not,
// and a row of them on an idle machine was indistinguishable from a row of them
// on a working one. Naming the task ahead and the time waited is what separates
// them. It has to be the time, not a threshold: a lecture legitimately holds the
// queue for hours, so nothing here can decide on the reader's behalf that a wait
// has gone on too long.
const queueWaitDetail = (job, lang, aheadName = '') => {
    const wait = job?.metadata?.queue_wait;
    const waitingFor = String(wait?.waiting_for || '').trim();
    if (!waitingFor) return '';
    const since = Date.parse(wait?.since || '');
    const waited = Number.isFinite(since) ? fmtDuration((Date.now() - since) / 1000, lang) : '';
    // The recording's own name, because that is what the reader recognises on
    // this page. A task id identifies the right row to a developer and nothing
    // at all to the person who queued five lectures; it is the fallback only
    // when the task ahead is not one of the rows on screen.
    const ahead = aheadName || waitingFor;
    if (lang === 'zh') {
        return `在等「${ahead}」处理完${waited ? `，已经等了 ${waited}` : ''}。一次只处理一个；前面那个要先剪掉气口再转写，素材长的话这一步就要几分钟。`;
    }
    return `Waiting for "${ahead}"${waited ? `, ${waited} so far` : ''}. One at a time; that task removes its breath gaps before transcribing, which takes minutes on long material.`;
};

const liveStageDetail = (job, lang, aheadName = '') => {
    // Ahead of the snapshot's own line: the snapshot describes the step this task
    // will run, which for a task that has not started is the least useful true
    // thing on the card.
    const waiting = queueWaitDetail(job, lang, aheadName);
    if (waiting) return waiting;
    const progressMeta = job?.metadata?.video_source_progress || {};
    const loaded = progressMeta.loaded_bytes ? fmtBytes(progressMeta.loaded_bytes) : '';
    const total = progressMeta.total_bytes ? fmtBytes(progressMeta.total_bytes) : '';
    const byteText = loaded && total ? ` · ${loaded} / ${total}` : (loaded ? ` · ${loaded}` : '');
    // A link task's own line while it downloads or waits ("正在下载视频…"). The
    // service only sends it while the stage that wrote it lasts.
    const progressMessage = progressMessageOf(job);
    if (progressMessage) return `${progressMessage}${byteText}`;
    const snapshotStep = Array.isArray(job?.task_snapshot?.steps)
        ? job.task_snapshot.steps.find((step) => step?.id === job.task_snapshot?.current_step)
        : null;
    if (snapshotStep?.detail) return snapshotStep.detail;
    if (progressMeta.message) return `${progressMeta.message}${byteText}`;
    if (normalizeTaskState(job) === TASK_STATE_QUEUED) {
        // Say why the wait can be long. One task runs at a time, and the task
        // ahead now starts by re-encoding its whole recording to remove the
        // breath gaps — minutes on a long video. "Queued" alone made a working
        // queue look stalled.
        return lang === 'zh'
            ? '已经加入队列，会按顺序开始处理。前面那个任务要先剪掉气口再转写，素材长的话这一步要几分钟。'
            : 'Queued and waiting for its turn. The task ahead removes its breath gaps before transcribing, which takes minutes on long material.';
    }
    if (job?.stage === 'prepare_media') {
        const material = isAudioOnlySource(job)
            ? (lang === 'zh' ? '录音' : 'recording')
            : (lang === 'zh' ? '录像' : 'video');
        return lang === 'zh'
            ? `正在把${material}里的空白剪掉，之后的转写和笔记都基于剪好的那份。素材长的话要几分钟。`
            : `Removing the quiet stretches from the ${material}; the transcript and note will both come from the shortened file. Minutes on long material.`;
    }
    return lang === 'zh' ? '进度会在这里持续更新，离开本页也不会中断任务。' : 'Progress keeps updating here; leaving this page will not stop the task.';
};

const sourceLabel = (job, lang) => {
    const isZh = lang === 'zh';
    const sourceType = job?.source_type || job?.result?.source || '';
    const metadata = job?.metadata || {};
    const videoSource = metadata.video_source || {};
    const url = String(videoSource.url || videoSource.webpage_url || metadata.video_source_input_preview || '').trim();
    let host = '';
    try {
        host = new URL(url).hostname.replace(/^www\./, '').toLowerCase();
    } catch (_) {}
    if (sourceType === 'video_link') {
        if (host.includes('bilibili.com') || host === 'b23.tv') return 'Bilibili';
        if (host.includes('youtube.com') || host === 'youtu.be') return 'YouTube';
        if (host.includes('douyin.com')) return isZh ? '抖音' : 'Douyin';
        return isZh ? '视频平台链接' : 'Video platform link';
    }
    if (sourceType === 'transcript_file') return isZh ? '本地字幕文件' : 'Local subtitle file';
    if (sourceType === 'queue_upload' || sourceType === 'video_file') return isZh ? '本地视频文件' : 'Local video file';
    if (sourceType === 'audio_file') return isZh ? '本地音频文件' : 'Local audio file';
    const filename = String(job?.source_filename || job?.result?.filename || '').toLowerCase();
    if (/\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(filename)) return isZh ? '本地视频文件' : 'Local video file';
    if (/\.(mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(filename)) return isZh ? '本地音频文件' : 'Local audio file';
    if (/\.(srt|vtt|txt|md)$/i.test(filename)) return isZh ? '本地字幕文件' : 'Local subtitle file';
    return isZh ? '本地文件' : 'Local file';
};

// Size and length, and for a link the platform it came from.
//
// The platform used to have a tile of its own, next to a column that read
// "本地视频文件" on every card — the words were the same every time in an edition
// whose material is local files, and they were squeezing the cut, which is
// different on every recording. Folded in here the one case that carried real
// information keeps it: where a downloaded video came from is not visible anywhere
// else on the card, while "this is a local file" was already obvious from the row
// above it.
const fileInfoLabel = (job, lang) => {
    const sizeMb = Number(job?.source_file_size_mb || job?.metadata?.file_size_mb || 0) || 0;
    const durationSec = Number(job?.result?.audio_duration_seconds || job?.source_duration_seconds || job?.metadata?.duration_seconds || 0) || 0;
    const parts = [];
    if ((job?.source_type || job?.result?.source || '') === 'video_link') parts.push(sourceLabel(job, lang));
    if (sizeMb) parts.push(fmtFileSize(sizeMb));
    if (durationSec) parts.push(fmtDuration(durationSec, lang));
    return parts.join(' · ') || '-';
};

// What the cut actually did to this recording, or why it did not.
//
// This replaced the "处理路线" tile, which said "本地转写" on every card in an
// edition that only transcribes locally — a column of identical words. The cut is
// the opposite: it is different per recording, it is the reason the result is
// shorter than the upload, and it is the number worth seeing at a glance.
// Where the cut file ended up, but only when that is not the ordinary outcome.
//
// Three states, and two of them are worth a sentence:
//
//   null  — an upload. There is no "beside the original" to deliver to, because
//           the original FluentFlow holds *is* the copy. Worth saying once,
//           because the owner's folder does not get the file and they will go
//           looking for it there.
//   true  — the in-place entry did what it always does. Silent: a line repeating
//           it on every card is a column of the same words, and the name is the
//           recording's own with a suffix. Nothing there to learn.
//   false — it tried and could not. Loud, and with the reason, because a file the
//           owner expected in their folder is not in it.
//
// What the earlier version got wrong was not "no line per card" — it was that
// nothing anywhere said this happens at all, so the owner never knew to expect a
// file. That belongs where they choose the entry, not on a hundred records.
export const cutFileDelivery = (job, lang) => {
    const isZh = lang === 'zh';
    const state = job?.result?.debreath;
    if (!state || typeof state !== 'object') return null;
    // Nothing was rendered, so there was nothing to put anywhere.
    if (state.used_for_transcription === false || state.already_cut || state.not_worth_rendering) return null;
    if (!state.rendered && state.status !== 'completed') return null;
    if (state.delivered === false) {
        return {
            tone: 'error',
            text: state.delivery_error || (isZh
                ? '剪后文件没能存到原文件旁边。任务里这份还在，可以下载。'
                : 'The cut file could not be saved next to the original. The task still has it, and it can be downloaded.'),
        };
    }
    // Positive evidence, not a missing field. An in-place task always records the
    // outcome either way, so absence could equally mean "recorded before this was
    // built" — and telling the owner an old record was an upload when it was not
    // sends them looking in the wrong place. A task with no folder behind it is
    // the one that really has nowhere to deliver to.
    if (state.delivered === undefined || state.delivered === null) {
        if (job?.metadata?.folder_intake) return null;
        // A link's cut is filed in its folder under Videos, recorded on the
        // task's output folder rather than on the cut.
        if (job?.result?.output_folder?.cut_path) return null;
        return {
            tone: 'note',
            text: isZh
                ? '这份是上传处理的，剪后文件留在应用里，没有进你的文件夹——可以从这里下载。'
                : 'This one was uploaded, so the cut file stayed in the app rather than going into your folder — download it here.',
        };
    }
    return null;
};

export const debreathTile = (job, lang) => {
    const isZh = lang === 'zh';
    const state = job?.result?.debreath;
    if (job?.result?.agent_cut?.status === 'waiting') {
        return {value: isZh ? '等你的 agent 判断' : 'Waiting for your agent'};
    }
    // Absence means never cut. It has to stay distinguishable from "cut and found
    // nothing", which is why the list payload carries this block at all: without it
    // a freshly cut task reported "未剪" on this very card.
    if (!state || typeof state !== 'object') {
        // A task still running has not reached the cut yet: the recording is
        // transcribed whole first, so a fresh task read "older task" until then.
        if (!['completed', 'failed', 'cancelled'].includes(job?.status)) {
            return {value: isZh ? '转录完再剪' : 'After transcription'};
        }
        return {value: isZh ? '未剪（旧任务）' : 'Not cut (older task)'};
    }
    const plan = state.plan || {};
    const cuts = Number(plan.cut_count) || 0;
    const removed = Number(plan.removed_seconds) || 0;
    // Only the time removed. The engine's cut count counts every breath gap
    // one by one (1887 on a long interview), while the processing report
    // groups them into a few dozen entries, so the two numbers never agreed.
    if (state.status === 'running') return {value: isZh ? '正在剪…' : 'Cutting…'};
    if (state.status === 'failed') return {value: isZh ? '没成功' : 'Failed'};
    if (state.not_worth_rendering) {
        // Measured, not declined: there was almost nothing to remove, so the file was
        // left alone rather than re-exported whole for a fraction of a second.
        return {value: isZh ? '几乎没空白可剪' : 'Almost nothing to cut'};
    }
    if (state.already_cut) {
        // Not the same answer as declining. Declining is bad news — the note is about
        // the recording. This is not: the file arrived already shortened, so the pass
        // was skipped to save a pointless re-encode.
        return {value: isZh ? '本来就是剪后版本' : 'Already cut'};
    }
    if (state.used_for_transcription === false && state.not_used_reason) {
        // The cut declined and the recording was transcribed. Saying "cut" here
        // would be the one wrong answer, because the note is not about a cut file.
        // Why it declined is a sentence, and a sentence truncated into a tile is
        // worth less than nothing — it lives on the task's own page.
        return {value: isZh ? '按原片处理' : 'Used the original'};
    }
    if (!cuts) return {value: isZh ? '没有可剪的空白' : 'Nothing to cut'};
    const removedText = fmtDuration(removed, lang);
    const value = isZh ? `剪掉 ${removedText}` : `${removedText} removed`;
    // No line for the file landing beside the original: that is the normal outcome,
    // and a card that narrates the normal outcome is noise. The hint below is kept
    // for the outcomes that are *not* obvious from the numbers.
    if (state.render_verified === false) {
        return {value, hint: isZh ? '没通过自检' : 'Failed its own check'};
    }
    return {value};
};

// The note, in one phrase. It is what the task exists to produce, so its state
// belongs on the card — the tile it replaced ("判断材料类型") reported an internal
// classification that changes nothing the user does next.
export const noteTileValue = (job, lang) => {
    const isZh = lang === 'zh';
    const result = job?.result || {};
    // The job list carries only the first 240 characters of the note, so its
    // length is sent separately; the full result has no such field and is counted.
    const chars = Number.isFinite(result.summary_chars)
        ? result.summary_chars
        : String(result.summary_markdown || '').trim().length;
    if (chars) {
        const fromCut = result.summary_written_from === 'debreath_media_note';
        const base = isZh ? `${chars} 字` : `${chars} chars`;
        return fromCut
            ? (isZh ? `${base}（据剪后版本）` : `${base} (from the cut version)`)
            : base;
    }
    if (result.summary_status === 'failed' || result.summary_error) return isZh ? '没生成' : 'Not written';
    if (result.summary_skipped_reason === 'no_model_key') return isZh ? '没填 Key，只出转录稿' : 'No key: transcript only';
    if (result.summary_skipped) return isZh ? '已跳过' : 'Skipped';
    if (result.summary_status === 'pending') return isZh ? '正在写…' : 'Being written…';
    return isZh ? '等处理' : 'Pending';
};

const OUTCOME_PILL_CLASS = {
    done: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-100',
    warn: 'border-[#f5c86b] bg-[#fffaf0] text-[#8a5a00] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]',
    failed: 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300',
    live: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-200',
};

const QueueUploadBanner = ({upload, lang, onCancel}) => {
    if (!upload?.queueUpload || upload?.queueSubmitted) return null;
    const count = Number(upload.queueTotal || 0) || 1;
    const progress = Math.max(0, Math.min(100, Number(upload.progress) || 2));
    const totalSize = upload.fileSizeMb ? fmtFileSize(upload.fileSizeMb) : '';
    return (
        <section className="rounded-[20px] border border-blue-200 bg-blue-50/80 p-4 shadow-[0_16px_42px_-36px_rgba(17,17,17,.45)] dark:border-blue-400/20 dark:bg-blue-400/10 dark:shadow-none">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                    <p className="inline-flex items-center gap-2 text-[12px] font-extrabold text-blue-700 dark:text-blue-200">
                        <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/>
                        {lang === 'zh' ? '正在接收上传批次' : 'Receiving upload batch'}
                    </p>
                    <h2 className="mt-2 font-headline text-[18px] font-extrabold text-[#111111] dark:text-white">
                        {lang === 'zh' ? `${count} 个文件正在上传` : `${count} files are uploading`}
                    </h2>
                    <p className="mt-1 text-[13px] font-semibold leading-5 text-[#57585d] dark:text-white/64">
                        {lang === 'zh'
                            ? '每个文件的进程卡已显示在下方，上传提交完成后会切换为真实任务记录。'
                            : 'Each file is shown below; after upload is submitted, the cards switch to real task records.'}
                    </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {totalSize ? (
                        <span className="rounded-full border border-blue-200 bg-white/70 px-3 py-1 text-[12px] font-extrabold text-blue-700 dark:border-blue-400/20 dark:bg-white/[0.08] dark:text-blue-100">
                            {totalSize}
                        </span>
                    ) : null}
                    <span className="rounded-full border border-blue-200 bg-white/70 px-3 py-1 text-[12px] font-extrabold tabular-nums text-blue-700 dark:border-blue-400/20 dark:bg-white/[0.08] dark:text-blue-100">
                        {progress}%
                    </span>
                    <button type="button" onClick={onCancel} className="inline-flex h-8 items-center gap-1.5 rounded-[10px] border border-red-200 bg-white/70 px-2.5 text-[12px] font-extrabold text-red-700 transition hover:bg-red-50 dark:border-red-400/30 dark:bg-white/[0.08] dark:text-red-200 dark:hover:bg-red-400/10">
                        <XCircle className="size-3.5" strokeWidth={2.15}/>
                        {lang === 'zh' ? '取消上传' : 'Cancel upload'}
                    </button>
                </div>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-blue-100 dark:bg-white/[0.12]">
                <div className="h-full rounded-full bg-blue-700 transition-all duration-500 dark:bg-blue-200" style={{width: `${progress}%`}}/>
            </div>
        </section>
    );
};

const AgentTaskCardView = ({job, lang, aheadName = '', retryError = '', highlighted = false, cancellingTaskId, deletingTaskId, openingTaskId, retryingTaskId, downloadingTaskId, onCancel, onDelete, onDownloadCut, onOpenResult, onRetry}) => {
    const taskId = taskIdForJob(job);
    // The task a resubmitted link pointed back to is brought into view once.
    const highlightRef = useRef(null);
    useEffect(() => {
        if (highlighted) highlightRef.current?.scrollIntoView?.({block: 'center', behavior: 'smooth'});
    }, [highlighted]);
    const state = normalizeTaskState(job);
    const live = isLiveTask(job);
    const completed = state === TASK_STATE_COMPLETED || state === TASK_STATE_CACHED_ONLY;
    const cancelled = state === TASK_STATE_CANCELLED;
    const failedTerminal = state === TASK_STATE_FAILED;
    const failed = failedTerminal || cancelled;
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
    // A link that is an image post, members-only, too large, or from a site
    // that is not supported fails the same way every time; offering "submit
    // again" there only invites a second identical failure.
    const retryUseless = failedTerminal && mediaSourceForJob(job) === 'video_link' && diagnosis && !diagnosis.retryable;
    const detail = failed
        ? diagnosis.detail
        : completed
            ? (lang === 'zh' ? '处理完成，可以打开结果继续校对、下载或重写笔记。' : 'Done. Open the result to review, download, or regenerate notes.')
            : liveStageDetail(job, lang, aheadName);
    const failedProgressLabel = state === TASK_STATE_CANCELLED
        ? (lang === 'zh' ? '已取消' : 'Cancelled')
        : (lang === 'zh' ? '未完成' : 'Incomplete');
    const progressLabel = failed
        ? failedProgressLabel
        : progressUnknown && live
            ? (downloadUnknown && downloadedBytes > 0
                ? (lang === 'zh' ? `已下载 ${fmtBytes(downloadedBytes)}` : `${fmtBytes(downloadedBytes)} downloaded`)
                : (lang === 'zh' ? '处理中' : 'Working'))
            : `${progress}%`;
    const subtitle = completed ? taskProcessingTimeLabel(job, lang) : stageLabel(job, lang);
    // One line of facts per finished record, in a fixed order: when, the file,
    // what the cut removed, the note, how long processing took. The three tiles
    // and the full output path made each record three screens' worth of rows
    // for two or three records a screen (walkthrough, 2026-10-22).
    const cutTile = debreathTile(job, lang);
    const facts = [
        formatTaskDateTime(job?.updated_at || job?.created_at, lang),
        fileInfoLabel(job, lang),
        `${lang === 'zh' ? '去气口' : 'Cut'} ${cutTile.value}${cutTile.hint ? `（${cutTile.hint}）` : ''}`,
        `${lang === 'zh' ? '笔记' : 'Note'} ${noteTileValue(job, lang)}`,
        completed ? `${lang === 'zh' ? '处理' : 'Took'} ${subtitle}` : '',
    ].filter((item) => item && item !== '-');
    // A note the text model wrote because the frame note could not run is a
    // different note from the one asked for; the reason is the point.
    const noteHint = noteFallbackSentence(job?.result, lang);
    const delivery = cutFileDelivery(job, lang);
    const outcome = taskOutcome(job, lang);
    // The processing report opens under the row from the "更多" menu; the
    // places waiting for a judgement are handled in the editor, where
    // "查看结果" takes the owner.
    const [reportOpen, setReportOpen] = useState(false);
    const menuItems = [
        hasReport(job) ? {icon: 'description', label: reportOpen ? (lang === 'zh' ? '收起处理说明' : 'Hide processing report') : (lang === 'zh' ? '处理说明' : 'Processing report'), onClick: () => setReportOpen((value) => !value)} : null,
        {icon: 'delete', label: lang === 'zh' ? '删除记录' : 'Delete record', onClick: () => onDelete(job)},
    ].filter(Boolean);
    const secondaryButton = 'inline-flex h-9 items-center gap-1.5 rounded-[12px] border border-[#dedada] bg-white px-3 text-[12px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]';
    return (
        <article
            ref={highlightRef}
            data-highlighted={highlighted ? 'true' : undefined}
            className={`rounded-[16px] border bg-white px-4 py-3 dark:bg-white/[0.055] ${highlighted ? 'border-primary ring-2 ring-primary/30' : 'border-[#e4e1e1] dark:border-white/[0.10]'}`}
        >
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                        <span data-testid="task-outcome" className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-extrabold ${OUTCOME_PILL_CLASS[outcome.tone]}`}>
                            {outcome.label}
                        </span>
                        <h2 className="min-w-0 truncate font-headline text-[15px] font-extrabold text-[#111111] dark:text-white" title={displayTitle}>
                            {displayTitle}
                        </h2>
                    </div>
                    {terminal ? (
                        <p data-testid="task-facts" className="mt-1 truncate text-[12px] font-semibold text-[#76777d] dark:text-white/55" title={facts.join(' · ')}>
                            {facts.join(' · ')}
                        </p>
                    ) : (
                        <p className="mt-1 text-[12px] font-semibold text-[#76777d] dark:text-white/55">
                            {formatTaskDateTime(job?.updated_at || job?.created_at, lang)} · {subtitle} · {lang === 'zh' ? '进度' : 'Progress'}：{progressLabel}
                        </p>
                    )}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
                    {completed ? (
                        <button type="button" disabled={!job?.result || openingTaskId === taskId} onClick={() => onOpenResult(job)} className="inline-flex h-9 items-center gap-1.5 rounded-[12px] bg-[#111111] px-3.5 text-[12px] font-extrabold text-white transition hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-45 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                            {openingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <FileText className="size-4" strokeWidth={2.15}/>}
                            {lang === 'zh' ? '查看结果' : 'View result'}
                        </button>
                    ) : failed && !retryUseless ? (
                        <button type="button" disabled={retryingTaskId === taskId} onClick={() => onRetry(job)} className={secondaryButton}>
                            {retryingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <AlertCircle className="size-4" strokeWidth={2.15}/>}
                            {retryingTaskId === taskId ? (lang === 'zh' ? '正在入队…' : 'Queuing…') : (lang === 'zh' ? '重新提交' : 'Submit again')}
                        </button>
                    ) : null}
                    <OutputFolderLine taskId={taskId} result={job?.result} lang={lang} compact/>
                    {cancellableLive ? (
                        <button type="button" disabled={cancellingTaskId === taskId} onClick={() => onCancel(job)} className="inline-flex h-9 items-center gap-1.5 rounded-[12px] border border-red-200 bg-red-50 px-3 text-[12px] font-extrabold text-red-700 transition hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-45 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20">
                            {cancellingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <XCircle className="size-4" strokeWidth={2.15}/>}
                            {lang === 'zh' ? '取消' : 'Cancel'}
                        </button>
                    ) : null}
                    {completed && !job?.result ? (
                        <span className="inline-flex h-9 items-center gap-1.5 rounded-[12px] border border-[#dedada] bg-[#f4f3f3] px-3 text-[12px] font-extrabold text-[#676970] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white/55">
                            <CheckCircle2 className="size-4" strokeWidth={2.15}/>
                            {lang === 'zh' ? '结果同步中' : 'Syncing result'}
                        </span>
                    ) : null}
                    {/* Deleting is rare and cannot be undone; it does not sit
                        beside "查看结果" at the same weight. */}
                    {terminal && taskId ? (
                        <DropdownMenu
                            trigger={(
                                <button type="button" disabled={deletingTaskId === taskId} aria-label={lang === 'zh' ? '更多' : 'More'} title={lang === 'zh' ? '更多' : 'More'} className="inline-flex size-9 items-center justify-center rounded-[12px] border border-[#dedada] bg-white text-[#57585d] transition hover:bg-[#efeeee] disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.10]">
                                    {deletingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <MoreHorizontal className="size-4" strokeWidth={2.15}/>}
                                </button>
                            )}
                            items={menuItems}
                        />
                    ) : null}
                </div>
            </div>
            {!completed && !failed ? (
                <div className="mt-3">
                    <div className={`h-2 overflow-hidden rounded-full bg-[#efeeee] dark:bg-white/[0.12] ${progressUnknown && live ? 'progress-indeterminate' : ''}`}>
                        {!progressUnknown && <div className="h-full rounded-full bg-[#111111] transition-all duration-500 dark:bg-white" style={{width: `${progress}%`}}/>}
                    </div>
                    <p className="mt-2 text-[12px] font-semibold leading-5 text-[#57585d] dark:text-white/62">
                        {detail}
                    </p>
                </div>
            ) : null}
            {failedTerminal && detail ? (
                <p className="mt-2 inline-flex max-w-full items-start gap-2 rounded-[12px] border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold leading-5 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 break-words">{detail}</span>
                </p>
            ) : null}
            {/* The fix is a setting: one click to the row that holds it. */}
            {failedTerminal && diagnosis?.settingsTarget ? (
                <div className="mt-2">
                    <Link to={diagnosis.settingsTarget} className="text-[12px] font-extrabold text-[#111111] underline hover:no-underline dark:text-white">
                        {lang === 'zh' ? '去设置修改' : 'Change it in Settings'}
                    </Link>
                </div>
            ) : null}
            {/* Why the last "submit again" did not take. It names the recording's
                own path when the file has moved, which is the one thing this
                product cannot work out for the reader. */}
            {retryError ? (
                <p data-testid="retry-error" className="mt-2 inline-flex max-w-full items-start gap-2 rounded-[12px] border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold leading-5 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 break-words">{retryError}</span>
                </p>
            ) : null}
            {noteHint ? (
                <p className="mt-1.5 text-[12px] font-semibold leading-5 text-[#85868c] dark:text-white/45">{noteHint}</p>
            ) : null}
            {/* Only the two states worth a sentence. A cut file that landed where
                it belongs says nothing, so a card with words here is a card with
                something to do. */}
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
                            className="ml-auto inline-flex h-7 shrink-0 items-center gap-1.5 rounded-[10px] border border-current/25 bg-white/70 px-2.5 text-[12px] font-extrabold transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-45 dark:bg-white/[0.10] dark:hover:bg-white/[0.16]"
                        >
                            {lang === 'zh' ? '下载剪后文件' : 'Download the cut file'}
                        </button>
                    )}
                </div>
            ) : null}
            <AgentCutWait job={job}/>
            {reportOpen ? (
                <div data-testid="cut-report-panel" className="mt-3 rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-3 py-3 dark:border-white/[0.10] dark:bg-white/[0.04]">
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
        meta.display_title, meta.queue_position, meta.queue_total, meta.queue_provisional,
        meta.queue_wait?.waiting_for, meta.queue_wait?.since,
        meta.video_source_progress?.message, meta.video_source_progress?.loaded_bytes,
        meta.video_source_progress?.total_bytes, meta.video_source_progress?.percent,
        progressMessageOf(job),
        job?.task_snapshot?.current_step,
        job?.sttStatus, job?.stt_status, meta.stt_status,
    ].join('\u0001');
};
// The busy ids are page-wide; a card only cares whether they name it.
const BUSY_KEYS = ['cancellingTaskId', 'deletingTaskId', 'openingTaskId', 'retryingTaskId', 'downloadingTaskId'];
const areCardPropsEqual = (prev, next) => {
    if (prev.lang !== next.lang || prev.aheadName !== next.aheadName || prev.retryError !== next.retryError || prev.highlighted !== next.highlighted) return false;
    const taskId = taskIdForJob(next.job);
    for (const key of BUSY_KEYS) {
        if ((prev[key] === taskId) !== (next[key] === taskId)) return false;
    }
    // Handlers are stable (see the ref in AgentTasks), so they are not compared.
    return cardRenderKey(prev.job) === cardRenderKey(next.job);
};
const AgentTaskCard = memo(AgentTaskCardView, areCardPropsEqual);

const RECORD_FILTERS = [
    {id: 'all', zh: '全部', en: 'All', match: () => true},
    // Anything still producing a result: queued or running, and a finished
    // task whose note is being written or whose cut is being redone.
    {id: 'live', zh: '进行中', en: 'In progress', match: (job) => taskOutcome(job).kind === 'live'},
    {id: 'judge', zh: '待你判断', en: 'To review', match: (job) => taskOutcome(job).kind === 'judge'},
    // Failed, cancelled, or finished without the note or the cut.
    {id: 'problem', zh: '有问题', en: 'Problems', match: (job) => !['live', 'judge', 'done'].includes(taskOutcome(job).kind)},
];

const AgentTasks = () => {
    const {lang} = useI18n();
    // Read the shared task list + mutations from AppProvider; this page no
    // longer keeps a private jobs state, warm cache, or writes the cache
    // (plan Stage 3b).
    const {currentJob, setCurrentJob, setLastResult, addToHistory, removeFromHistory, tasks: jobs, ingestJobs, markCancelled, revertCancelled, restoreTask, abortPendingUpload} = useApp();
    const {getJob, cancelJob, deleteJob, retryJob, createVideoSourceJob, downloadJobMedia} = useApi();
    const {loadSettings} = useSettings();
    const location = useLocation();
    const navigate = useNavigate();
    const seededJob = location.state?.job && typeof location.state.job === 'object' ? location.state.job : null;
    const [cancellingTaskId, setCancellingTaskId] = useState('');
    const [openingTaskId, setOpeningTaskId] = useState('');
    const [deletingTaskId, setDeletingTaskId] = useState('');
    const [retryingTaskId, setRetryingTaskId] = useState('');
    // A refusal to re-run belongs on the card that was refused, not in the page
    // banner: that banner is cleared by every successful poll, and this page polls
    // while any task is live, so the reason a retry did not happen was wiped
    // before it could be read. The button just spun and nothing appeared.
    const [retryError, setRetryError] = useState(null);
    const [downloadingTaskId, setDownloadingTaskId] = useState('');
    // Kept in page state rather than read from the navigation each render: the
    // polling banner is cleared on every refresh, and this has to stay readable.
    const [extraUrlsIgnored, setExtraUrlsIgnored] = useState(() => location.state?.extraUrlsIgnored === true);
    useEffect(() => {
        if (location.state?.extraUrlsIgnored === true) setExtraUrlsIgnored(true);
    }, [location.state]);
    // A link that was already submitted is not queued a second time: the start
    // page sends the person here and points at the task that has it. The
    // notice is said only when that task is still running; a finished one that
    // could not be opened is just pointed at.
    const highlightFromState = () => String(location.state?.highlightTaskId || '');
    const [highlightTaskId, setHighlightTaskId] = useState(highlightFromState);
    const [duplicateNotice, setDuplicateNotice] = useState(() => location.state?.duplicateOfActive === true);
    useEffect(() => {
        const id = String(location.state?.highlightTaskId || '');
        if (id) setHighlightTaskId(id);
        if (location.state?.duplicateOfActive === true) setDuplicateNotice(true);
    }, [location.state]);
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
    const [filter, setFilter] = useState('all');
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
    const queuedCount = liveJobs.filter((job) => normalizeTaskState(job) === TASK_STATE_QUEUED).length;
    const runningCount = liveJobs.length - queuedCount;

    // Shared fetch + polling (see lib/useJobPolling.js), with task-oriented wording.
    const {loading, error, setError, loadJobs} = useJobPolling({
        hasLiveJobs: hasLiveOrUploadingJobs,
        refreshFailedZh: '任务刷新失败，已保留本地缓存。',
        refreshFailedEn: 'Failed to refresh tasks. Local cache is preserved.',
    });

    // Surface a job passed via navigation state immediately by ingesting it.
    useEffect(() => {
        if (seededJob) ingestJobs([seededJob]);
    }, [seededJob, ingestJobs]);

    useEffect(() => {
        setError(location.state?.queueSubmitError || null);
    }, [location.state?.queueSubmitError, setError]);

    useEffect(() => {
        if (location.state?.queueSubmitError || location.state?.queueSubmittedAt) {
            navigate('/agent', {replace: true, state: {}});
        }
    }, [location.state?.queueSubmitError, location.state?.queueSubmittedAt, navigate]);

    const cancelPendingUpload = () => {
        const confirmText = lang === 'zh'
            ? '取消当前上传？尚未完成上传的文件不会进入处理队列；已进入队列的文件可在下方单独取消。'
            : 'Cancel the current upload? Files not yet uploaded will not enter the queue. Already queued files can be cancelled below.';
        if (!window.confirm(confirmText)) return;
        abortPendingUpload();
    };


    const cancelLiveJob = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        const confirmText = lang === 'zh'
            ? '取消这个正在处理的任务？任务会中止，完整结果不会生成；这不是删除历史记录。'
            : 'Cancel this active task? The task will stop and a complete result will not be created. This does not delete history.';
        if (!window.confirm(confirmText)) return;
        setCancellingTaskId(taskId);
        markCancelled(taskId);
        try {
            await cancelJob(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            if (currentJob?.taskId === taskId) setCurrentJob(null);
            await loadJobs();
        } catch (err) {
            revertCancelled(taskId);
            setError(friendlyTaskError(err.message || String(err), lang));
            await loadJobs();
        } finally {
            setCancellingTaskId('');
        }
    };

    const deleteTerminalJob = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId || isLiveTask(job)) return;
        const confirmText = lang === 'zh'
            ? '删除这条处理记录？会清理这条记录可删除的任务文件；这不是取消正在执行的任务。'
            : 'Delete this processing record? This also cleans up deletable task files; it does not cancel a running task.';
        if (!window.confirm(confirmText)) return;
        setDeletingTaskId(taskId);
        // Tombstone + drop through AppProvider so an in-flight poll cannot
        // resurrect it; restore if the backend delete fails (non-404).
        removeFromHistory(taskId);
        if (currentJob?.taskId === taskId) setCurrentJob(null);
        try {
            await deleteJob(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            setError(null);
        } catch (err) {
            if (err.status === 404) {
                setError(null);
                return;
            }
            restoreTask(taskId);
            setError(friendlyTaskError(err.message || String(err), lang));
            await loadJobs();
        } finally {
            setDeletingTaskId('');
        }
    };

    const rememberRetriedJobs = (nextJobs=[]) => {
        const normalized = nextJobs.filter(Boolean).map(markBackendJob);
        if (!normalized.length) return;
        ingestJobs(normalized);
    };

    const retryStoredSourceJob = async (job) => {
        const taskId = taskIdForJob(job);
        const localOptions = isLocalJob(job) ? {sttProvider: 'local'} : {};
        return await retryJob(taskId, localOptions);
    };

    const retryTerminalJob = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId || isLiveTask(job)) return;
        setRetryingTaskId(taskId);
        setError(null);
        setRetryError(null);
        try {
            // Today's browser-login choice, not the one stored on the job: the
            // usual fix for a failed link is to pick a browser and try again.
            const options = retryOptionsForJob(job, {cookiesBrowser: loadSettings().videoCookiesBrowser || ''});
            const sourceKind = mediaSourceForJob(job);
            if (sourceKind === 'video_link') {
                const input = retryInputForJob(job);
                if (!input) {
                    throw new Error(lang === 'zh' ? '这条记录没有保留原视频链接，请从开始处理页重新提交。' : 'This record does not keep the original video link. Submit it again from the start page.');
                }
                const response = await createVideoSourceJob(input, options);
                const nextJob = response?.job ? markBackendJob(response.job) : null;
                if (nextJob) rememberRetriedJobs([nextJob]);
                await loadJobs();
                return;
            }
            if (sourceKind === 'media_file') {
                const response = await retryStoredSourceJob(job);
                const nextJobs = response?.job
                    ? [response.job]
                    : (Array.isArray(response?.queued) ? response.queued : []);
                rememberRetriedJobs(nextJobs);
                await loadJobs();
                return;
            }
            throw new Error(lang === 'zh' ? '这条记录的来源暂不支持直接重新提交，请从开始处理页重新提交。' : 'This record source cannot be resubmitted directly yet. Submit it again from the start page.');
        } catch (err) {
            setRetryError({taskId, message: friendlyTaskError(err.message || String(err), lang)});
        } finally {
            setRetryingTaskId('');
        }
    };

    const getJobWithFallback = async (job) => {
        const taskId = taskIdForJob(job);
        const primaryOptions = isLocalJob(job) ? {sttProvider: 'local'} : {};
        try {
            return await getJob(taskId, primaryOptions);
        } catch (err) {
            if (err.status !== 404 || primaryOptions.sttProvider === 'local') throw err;
            return getJob(taskId, {sttProvider: 'local'});
        }
    };

    // Hand over the cut file itself.
    //
    // Offered only on the two cards that say something: one whose cut file stayed
    // in the app because the drop had no folder to go back to, and one whose copy
    // beside the original failed. On every other card the file is already in the
    // owner's folder and a download button would be offering them what they have.
    const downloadCutFile = async (job) => {
        const taskId = taskIdForJob(job);
        const artifact = job?.result?.artifacts?.debreath_media;
        if (!taskId || !artifact) return;
        const name = String(artifact.filename || '').split('/').pop() || `${taskId}_debreath.mp4`;
        setDownloadingTaskId(taskId);
        try {
            await downloadJobMedia(taskId, 'debreath_media', name);
        } catch (error) {
            window.alert(error?.message || (lang === 'zh' ? '剪后文件不可用' : 'The cut file is unavailable'));
        } finally {
            setDownloadingTaskId('');
        }
    };

    const openResult = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        // The task id goes in the address, and a task whose cut still has
        // places for the owner to judge says so, so the editor can take them
        // there (the editor side is drawn by the editor's own work).
        const open = (sourceJob, result) => {
            setLastResult(result);
            addToHistory(jobToHistoryEntry({...sourceJob, result}));
            const params = new URLSearchParams({task: taskId});
            if (cutsToJudge({...sourceJob, result})) params.set('review', 'cut');
            navigate(`/editor?${params.toString()}`);
        };
        if (job.result) {
            open(job, job.result);
            return;
        }
        setOpeningTaskId(taskId);
        try {
            const fresh = await getJobWithFallback(job);
            if (fresh?.result) {
                open(fresh, fresh.result);
                return;
            }
            setError(lang === 'zh' ? '这条记录暂时没有可打开的结果。请刷新后再试。' : 'This record does not have an openable result yet. Refresh and try again.');
        } catch (err) {
            setError(friendlyTaskError(err.message || String(err), lang));
        } finally {
            setOpeningTaskId('');
        }
    };

    // The handlers close over the latest page state, so they are new every
    // render; the cards receive stable wrappers that look them up through a ref,
    // which is what lets a card skip rendering when its row did not change.
    const handlersRef = useRef({});
    handlersRef.current = {cancelLiveJob, deleteTerminalJob, downloadCutFile, openResult, retryTerminalJob};
    const cardHandlers = useMemo(() => ({
        onCancel: (job) => handlersRef.current.cancelLiveJob(job),
        onDelete: (job) => handlersRef.current.deleteTerminalJob(job),
        onDownloadCut: (job) => handlersRef.current.downloadCutFile(job),
        onOpenResult: (job) => handlersRef.current.openResult(job),
        onRetry: (job) => handlersRef.current.retryTerminalJob(job),
    }), []);

    return (
        <main className="ml-[var(--sidebar-offset)] h-dvh flex-1 overflow-y-auto bg-[#f8f7fb] px-6 py-5 text-[#111111] transition-[margin] duration-200 ease-out hide-scrollbar dark:bg-[#101010] dark:text-white/[0.92] lg:px-10">
            <div className="mx-auto max-w-7xl space-y-5">
                <header className="flex flex-col gap-4 border-b border-[#dedada] pb-5 dark:border-white/[0.10] lg:flex-row lg:items-end lg:justify-between">
                    <div className="min-w-0">
                        <p className="inline-flex items-center gap-2 text-[12px] font-extrabold text-[#676970] dark:text-white/[0.72]">
                            <SlidersHorizontal className="size-4" strokeWidth={2.15}/>
                            {lang === 'zh' ? '处理记录' : 'Processing records'}
                        </p>
                        <h1 className="mt-2 font-headline text-[24px] font-extrabold leading-tight text-[#111111] dark:text-white">
                            {lang === 'zh' ? '处理记录' : 'Processing records'}
                        </h1>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={() => loadJobs({full: true})} className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-[#dedada] bg-white px-4 text-[13px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]">
                            <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} strokeWidth={2.15}/>
                            {lang === 'zh' ? '刷新' : 'Refresh'}
                        </button>
                        <Link to="/media-text?mode=media" className="inline-flex h-10 items-center gap-2 rounded-[14px] bg-[#111111] px-4 text-[13px] font-extrabold text-white transition hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                            <Plus className="size-4" strokeWidth={2.15}/>
                            {lang === 'zh' ? '添加任务' : 'Add task'}
                        </Link>
                    </div>
                </header>

                {error && (
                    <div className="rounded-[16px] border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
                        {error}
                    </div>
                )}

                {extraUrlsIgnored ? (
                    <div data-testid="extra-urls-ignored" className="flex items-start justify-between gap-3 rounded-[16px] border border-[#f5c86b] bg-[#fffaf0] px-4 py-3 text-sm font-semibold text-[#8a5a00] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]">
                        <span>{extraUrlsIgnoredNotice(lang)}</span>
                        <button type="button" onClick={() => setExtraUrlsIgnored(false)} className="shrink-0 text-[12px] font-extrabold underline">
                            {lang === 'zh' ? '知道了' : 'Got it'}
                        </button>
                    </div>
                ) : null}

                {duplicateNotice ? (
                    <div data-testid="duplicate-link-notice" className="flex items-start justify-between gap-3 rounded-[16px] border border-[#c7d2fe] bg-[#eef2ff] px-4 py-3 text-sm font-semibold text-[#3730a3] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/80">
                        <span>{lang === 'zh' ? '这个链接已经在处理中，没有重复提交。' : 'This link is already being processed; it was not submitted again.'}</span>
                        <button type="button" onClick={() => setDuplicateNotice(false)} className="shrink-0 text-[12px] font-extrabold underline">
                            {lang === 'zh' ? '知道了' : 'Got it'}
                        </button>
                    </div>
                ) : null}

                {queueUploadJob ? <QueueUploadBanner upload={queueUploadJob} lang={lang} onCancel={cancelPendingUpload}/> : null}

                {/* One line instead of three tiles: the count of records only
                    once the full list is in (it used to climb from the cached 100
                    to the real total), and what is running only when something is. */}
                <section className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={lang === 'zh' ? '筛选' : 'Filter'}>
                        {RECORD_FILTERS.map((item) => {
                            const count = filterCounts[item.id];
                            if (item.id !== 'all' && !count && filter !== item.id) return null;
                            const active = filter === item.id;
                            return (
                                <button
                                    key={item.id}
                                    type="button"
                                    aria-pressed={active}
                                    onClick={() => setFilter(item.id)}
                                    className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12px] font-extrabold transition ${active
                                        ? 'border-[#111111] bg-[#111111] text-white dark:border-white dark:bg-white dark:text-[#111111]'
                                        : 'border-[#dedada] bg-white text-[#57585d] hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.10]'}`}
                                >
                                    {lang === 'zh' ? item.zh : item.en}
                                    {loading && item.id === 'all' ? null : <span className="tabular-nums opacity-70">{count}</span>}
                                </button>
                            );
                        })}
                        {runningCount + queuedCount > 0 ? (
                            <span data-testid="live-summary" className="ml-1 inline-flex items-center gap-1.5 text-[12px] font-extrabold text-blue-700 dark:text-blue-200">
                                <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.15}/>
                                {lang === 'zh'
                                    ? [runningCount ? `${runningCount} 个在处理` : '', queuedCount ? `${queuedCount} 个排队` : ''].filter(Boolean).join(' · ')
                                    : [runningCount ? `${runningCount} running` : '', queuedCount ? `${queuedCount} queued` : ''].filter(Boolean).join(' · ')}
                            </span>
                        ) : null}
                    </div>
                    <label className="relative block sm:w-64">
                        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#9a9ba0]" strokeWidth={2.15}/>
                        <input
                            type="search"
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder={lang === 'zh' ? '搜索标题' : 'Search titles'}
                            className="h-9 w-full rounded-[12px] border border-[#dedada] bg-white pl-9 pr-3 text-[13px] font-semibold text-[#111111] outline-none placeholder:text-[#a2a3a8] focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:focus:border-white/40"
                        />
                    </label>
                </section>

                <section className="space-y-2">
                    {loading && !displayJobs.length && !queueUploadJob && (
                        <div className="flex h-48 items-center justify-center rounded-[22px] border border-[#dedada] bg-white text-sm font-semibold text-[#676970] dark:border-white/[0.10] dark:bg-white/[0.055] dark:text-white/60">
                            <LoaderCircle className="mr-2 size-4 animate-spin" strokeWidth={2.15}/>
                            {lang === 'zh' ? '正在读取任务...' : 'Loading tasks...'}
                        </div>
                    )}
                    {!loading && displayJobs.length === 0 && !queueUploadJob && (
                        <div className="rounded-[24px] border border-[#dedada] bg-white p-8 text-center dark:border-white/[0.10] dark:bg-white/[0.055]">
                            <History className="mx-auto size-9 text-[#85868c] dark:text-white/45" strokeWidth={2.15}/>
                            <h2 className="mt-3 font-headline text-[18px] font-extrabold text-[#111111] dark:text-white">
                                {lang === 'zh' ? '当前没有处理记录' : 'No processing records'}
                            </h2>
                            <p className="mx-auto mt-2 max-w-[54ch] text-[13px] font-semibold leading-5 text-[#676970] dark:text-white/60">
                                {lang === 'zh' ? '开始一个新任务后，处理进度会直接作为记录显示在这里。' : 'Start a task and its processing record will appear here.'}
                            </p>
                            <div className="mt-5 flex flex-wrap justify-center gap-2">
                                <Link to="/media-text?mode=media" className="inline-flex h-10 items-center gap-2 rounded-[14px] bg-[#111111] px-4 text-[13px] font-extrabold text-white transition hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111]">
                                    <Plus className="size-4" strokeWidth={2.15}/>
                                    {lang === 'zh' ? '开始处理' : 'Start'}
                                </Link>
                            </div>
                        </div>
                    )}
                    {displayJobs.length > 0 && visibleJobs.length === 0 && (
                        <p className="rounded-[16px] border border-dashed border-[#dedada] bg-white px-4 py-8 text-center text-[13px] font-semibold text-[#85868c] dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-white/50">
                            {lang === 'zh' ? '没有符合的记录。' : 'No matching records.'}
                        </p>
                    )}
                    {visibleJobs.map((job) => (
                        <AgentTaskCard
                            key={taskIdForJob(job)}
                            job={job}
                            lang={lang}
                            aheadName={queueAheadName(job)}
                            retryError={retryError?.taskId === taskIdForJob(job) ? retryError.message : ''}
                            highlighted={!!highlightTaskId && highlightTaskId === taskIdForJob(job)}
                            cancellingTaskId={cancellingTaskId}
                            deletingTaskId={deletingTaskId}
                            openingTaskId={openingTaskId}
                            retryingTaskId={retryingTaskId}
                            downloadingTaskId={downloadingTaskId}
                            {...cardHandlers}
                        />
                    ))}
                </section>
            </div>
        </main>
    );
};

export default AgentTasks;
