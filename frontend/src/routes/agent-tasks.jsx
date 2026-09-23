import {useEffect, useMemo, useState} from 'react';
import {Link, useLocation, useNavigate} from 'react-router-dom';
import {
    AlertCircle,
    CheckCircle2,
    FileText,
    History,
    LoaderCircle,
    Plus,
    RefreshCw,
    SlidersHorizontal,
    Trash2,
    XCircle,
} from 'lucide-react';
import {fmtDurationCompact} from '../lib/format.js';
import {downloadBrowserFile} from './editor-helpers.js';
import {
    fmtElapsed,
    fmtBytes,
    fmtFileSize,
    friendlyTaskError,
    isSttProgressUnmeasured,
    jobDisplayTitle,
    jobToHistoryEntry,
    jobToCurrentJob,
    sortJobsForHistoryView,
    useApi,
    useI18n,
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
} from '../lib/taskState.js';
import {useJobPolling} from '../lib/useJobPolling.js';

const taskIdForJob = (job) => String(job?.task_id || job?.result?.task_id || '').trim();

const isLocalJob = (job) => String(job?.client_id || '').startsWith('local-') || job?.metadata?.stt_provider === 'local';

const retryInputForJob = (job) => {
    const metadata = job?.metadata || {};
    const videoSource = metadata.video_source || {};
    return String(
        metadata.video_source_input_preview
        || videoSource.source_url
        || videoSource.url
        || videoSource.webpage_url
        || metadata.raw_input
        || ''
    ).trim();
};

const retryOptionsForJob = (job) => {
    const queueOptions = job?.metadata?.queue_options;
    const metadata = job?.metadata || {};
    const base = queueOptions && typeof queueOptions === 'object' ? queueOptions : metadata;
    return {
        exportToLark: base.export_to_lark === true || base.export_to_lark === 'true',
        larkExportRoute: base.lark_export_route,
        larkViaCli: base.lark_via_cli === true || base.lark_via_cli === 'true',
        skipSummary: base.skip_summary === true || base.skip_summary === 'true',
        aiProvider: base.ai_provider,
        aiModel: base.ai_model,
        noteMode: base.note_mode,
        promptPreset: base.prompt_preset,
        promptPresetLabel: base.prompt_preset_label,
        sttProvider: base.stt_provider,
        sttModel: base.stt_model,
        sttSpeed: base.stt_speed,
        sttLanguage: base.stt_language || 'auto',
        speakerDiarization: base.speaker_diarization === true || base.speaker_diarization === 'true',
    };
};

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

const formatProcessingElapsed = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours > 0) return `${hours}h${minutes}min`;
    if (minutes > 0) return secs > 0 ? `${minutes}min${secs}s` : `${minutes}min`;
    return `${secs}s`;
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
    const elapsedLabel = formatProcessingElapsed(elapsed);
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

const statusLabel = (job, lang) => {
    const state = normalizeTaskState(job);
    if (state === TASK_STATE_UPLOADING) return lang === 'zh' ? '上传中' : 'Uploading';
    if (state === TASK_STATE_QUEUED) return lang === 'zh' ? '排队中' : 'Queued';
    if (state === TASK_STATE_COMPLETED || state === TASK_STATE_CACHED_ONLY) return lang === 'zh' ? '已完成' : 'Completed';
    if (state === TASK_STATE_FAILED) return lang === 'zh' ? '失败' : 'Failed';
    if (state === TASK_STATE_CANCELLED) return lang === 'zh' ? '已取消' : 'Cancelled';
    return lang === 'zh' ? '处理中' : 'Running';
};

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
    const waited = Number.isFinite(since) ? fmtElapsed((Date.now() - since) / 1000) : '';
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
    const snapshotStep = Array.isArray(job?.task_snapshot?.steps)
        ? job.task_snapshot.steps.find((step) => step?.id === job.task_snapshot?.current_step)
        : null;
    if (snapshotStep?.detail) return snapshotStep.detail;
    const progressMeta = job?.metadata?.video_source_progress || {};
    const loaded = progressMeta.loaded_bytes ? fmtBytes(progressMeta.loaded_bytes) : '';
    const total = progressMeta.total_bytes ? fmtBytes(progressMeta.total_bytes) : '';
    const byteText = loaded && total ? ` · ${loaded} / ${total}` : (loaded ? ` · ${loaded}` : '');
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
            ? `正在把${material}里的空白剪掉，之后的转写和笔记都基于剪好的那份。素材长的话要几分钟，没有百分比可报。`
            : `Removing the quiet stretches from the ${material}; the transcript and note will both come from the shortened file. Minutes on long material, with no percentage to report.`;
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
    if (durationSec) parts.push(fmtElapsed(durationSec));
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
    // Absence means never cut. It has to stay distinguishable from "cut and found
    // nothing", which is why the list payload carries this block at all: without it
    // a freshly cut task reported "未剪" on this very card.
    if (!state || typeof state !== 'object') {
        return {value: isZh ? '未剪（旧任务）' : 'Not cut (older task)'};
    }
    const plan = state.plan || {};
    const cuts = Number(plan.cut_count) || 0;
    const removed = Number(plan.removed_seconds) || 0;
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
    const removedText = fmtDurationCompact(removed);
    const value = isZh ? `剪掉 ${cuts} 处，省 ${removedText}` : `${cuts} cuts, ${removedText} saved`;
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
    const chars = String(result.summary_markdown || '').trim().length;
    if (chars) {
        const fromCut = result.summary_written_from === 'debreath_media_note';
        const base = isZh ? `${chars} 字` : `${chars} chars`;
        return fromCut
            ? (isZh ? `${base}（据剪后版本）` : `${base} (from the cut version)`)
            : base;
    }
    if (result.summary_status === 'failed' || result.summary_error) return isZh ? '没生成' : 'Not written';
    if (result.summary_skipped) return isZh ? '已跳过' : 'Skipped';
    if (result.summary_status === 'pending') return isZh ? '正在写…' : 'Being written…';
    return isZh ? '等处理' : 'Pending';
};

const statePillClass = (state) => {
    if (state === TASK_STATE_COMPLETED) return 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-100';
    if (state === TASK_STATE_FAILED || state === TASK_STATE_CANCELLED) return 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300';
    return 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-200';
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

const AgentTaskCard = ({job, lang, aheadName = '', retryError = '', cancellingTaskId, deletingTaskId, openingTaskId, retryingTaskId, downloadingTaskId, onCancel, onDelete, onDownloadCut, onOpenResult, onRetry}) => {
    const taskId = taskIdForJob(job);
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
    const progressUnknown = isSttProgressUnmeasured(current);
    const displayTitle = jobDisplayTitle(job, lang);
    const detail = failed
        ? friendlyTaskError(job?.error_reason || job?.result?.summary_error || '', lang)
        : completed
            ? (lang === 'zh' ? '处理完成，可以打开结果继续校对、下载或重生笔记。' : 'Done. Open the result to review, download, or regenerate notes.')
            : liveStageDetail(job, lang, aheadName);
    const failedProgressLabel = state === TASK_STATE_CANCELLED
        ? (lang === 'zh' ? '已取消' : 'Cancelled')
        : (lang === 'zh' ? '未完成' : 'Incomplete');
    const progressLabel = failed
        ? failedProgressLabel
        : progressUnknown && live
            ? (lang === 'zh' ? '处理中' : 'Working')
            : `${progress}%`;
    const subtitle = completed ? taskProcessingTimeLabel(job, lang) : stageLabel(job, lang);
    const metaItems = [
        {label: lang === 'zh' ? '文件信息' : 'File', value: fileInfoLabel(job, lang)},
        // Three, not four, and the two that are gone were both columns of the same
        // words: "处理路线" said 本地转写 on every card, "判断材料类型" reported an
        // internal classification, and "来源" said 本地视频文件 for material that is
        // always a local file. Four tiles of equal width left the cut truncated at
        // "剪掉 639 处，省 2m …" — the one number that differs per recording was the
        // one being cut off to make room for words that never changed.
        {label: lang === 'zh' ? '去气口' : 'Breath gaps', ...debreathTile(job, lang)},
        {label: lang === 'zh' ? '笔记' : 'Note', value: noteTileValue(job, lang)},
    ];
    const delivery = cutFileDelivery(job, lang);
    return (
        <article className="rounded-[24px] border border-[#dedada] bg-white p-5 shadow-[0_18px_44px_-38px_rgba(17,17,17,.45)] dark:border-white/[0.10] dark:bg-white/[0.055] dark:shadow-none">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-extrabold ${statePillClass(state)}`}>
                            {statusLabel(job, lang)}
                        </span>
                        <span className="text-[12px] font-semibold text-[#85868c] dark:text-white/55">
                            {formatTaskDateTime(job?.updated_at || job?.created_at, lang)}
                        </span>
                    </div>
                    <h2 className="mt-2 truncate font-headline text-[17px] font-extrabold text-[#111111] dark:text-white" title={displayTitle}>
                        {displayTitle}
                    </h2>
                    <p className="mt-1 text-[13px] font-semibold leading-5 text-[#676970] dark:text-white/60">
                        {subtitle}
                        {failed ? ` · ${progressLabel}` : (!completed && ` · ${lang === 'zh' ? '进度' : 'Progress'}：${progressLabel}`)}
                    </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
                    {completed ? (
                        <button type="button" disabled={!job?.result || openingTaskId === taskId} onClick={() => onOpenResult(job)} className="inline-flex h-10 items-center gap-2 rounded-[14px] bg-[#111111] px-4 text-[13px] font-extrabold text-white transition hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-45 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                            {openingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <FileText className="size-4" strokeWidth={2.15}/>}
                            {lang === 'zh' ? '查看结果' : 'View result'}
                        </button>
                    ) : failed ? (
                        <button type="button" disabled={retryingTaskId === taskId} onClick={() => onRetry(job)} className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-[#dedada] bg-[#f4f3f3] px-4 text-[13px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]">
                            {retryingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <AlertCircle className="size-4" strokeWidth={2.15}/>}
                            {retryingTaskId === taskId ? (lang === 'zh' ? '正在入队…' : 'Queuing…') : (lang === 'zh' ? '重新提交' : 'Submit again')}
                        </button>
                    ) : null}
                    {cancellableLive ? (
                        <button type="button" disabled={cancellingTaskId === taskId} onClick={() => onCancel(job)} className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-red-200 bg-red-50 px-4 text-[13px] font-extrabold text-red-700 transition hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-45 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20">
                            {cancellingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <XCircle className="size-4" strokeWidth={2.15}/>}
                            {lang === 'zh' ? '取消' : 'Cancel'}
                        </button>
                    ) : null}
                    {terminal && taskId ? (
                        <button type="button" disabled={deletingTaskId === taskId} onClick={() => onDelete(job)} className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-[#dedada] bg-white px-4 text-[13px] font-extrabold text-[#57585d] transition hover:bg-[#efeeee] disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.10]">
                            {deletingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <Trash2 className="size-4" strokeWidth={2.15}/>}
                            {lang === 'zh' ? '删除记录' : 'Delete record'}
                        </button>
                    ) : null}
                    {completed && !job?.result ? (
                        <span className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-[#dedada] bg-[#f4f3f3] px-4 text-[13px] font-extrabold text-[#676970] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white/55">
                            <CheckCircle2 className="size-4" strokeWidth={2.15}/>
                            {lang === 'zh' ? '结果同步中' : 'Syncing result'}
                        </span>
                    ) : null}
                </div>
            </div>
            {!completed && !failed ? (
                <div className="mt-4">
                    <div className="mb-2 flex items-end justify-between gap-4">
                        <div>
                            <p className="text-[12px] font-extrabold text-[#85868c] dark:text-white/55">{lang === 'zh' ? '当前阶段' : 'Current stage'}</p>
                            <p className="mt-1 font-headline text-[22px] font-extrabold text-[#111111] dark:text-white">{stageLabel(job, lang)}</p>
                        </div>
                        <p className="font-headline text-[24px] font-extrabold tabular-nums text-[#111111] dark:text-white">{progressLabel}</p>
                    </div>
                    <div className={`h-2.5 overflow-hidden rounded-full bg-[#efeeee] dark:bg-white/[0.12] ${progressUnknown && live ? 'progress-indeterminate' : ''}`}>
                        {!progressUnknown && <div className="h-full rounded-full bg-[#111111] transition-all duration-500 dark:bg-white" style={{width: `${progress}%`}}/>}
                    </div>
                    <p className="mt-3 rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-3 py-2 text-[12px] font-semibold leading-5 text-[#57585d] dark:border-white/[0.10] dark:bg-white/[0.04] dark:text-white/62">
                        {detail}
                    </p>
                </div>
            ) : null}
            {failedTerminal && detail ? (
                <p className="mt-3 inline-flex max-w-full items-start gap-2 rounded-[12px] border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold leading-5 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 break-words">{detail}</span>
                </p>
            ) : null}
            {/* Why the last "submit again" did not take. It names the recording's
                own path when the file has moved, which is the one thing this
                product cannot work out for the reader. */}
            {retryError ? (
                <p data-testid="retry-error" className="mt-3 inline-flex max-w-full items-start gap-2 rounded-[12px] border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold leading-5 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 break-words">{retryError}</span>
                </p>
            ) : null}
            {/* Only the two states worth a sentence. A cut file that landed where
                it belongs says nothing, so a card with words here is a card with
                something to do. */}
            {delivery ? (
                <div className={`mt-3 flex flex-wrap items-start gap-2 rounded-[12px] border px-3 py-2 text-[12px] font-semibold leading-5 ${
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
            <div className="mt-4 grid gap-2 md:grid-cols-3">
                {metaItems.map((item) => (
                    <div key={item.label} className="min-w-0 rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-3 py-2 dark:border-white/[0.10] dark:bg-white/[0.04]">
                        <p className="text-[11px] font-extrabold text-[#85868c] dark:text-white/55">{item.label}</p>
                        <p className="mt-1 truncate text-[13px] font-extrabold text-[#111111] dark:text-white" title={item.value}>{item.value}</p>
                        {item.hint ? (
                            <p className="mt-0.5 truncate text-[11px] font-semibold text-[#85868c] dark:text-white/45" title={item.hint}>{item.hint}</p>
                        ) : null}
                    </div>
                ))}
            </div>
        </article>
    );
};

const AgentTasks = () => {
    const {lang} = useI18n();
    // Read the shared task list + mutations from AppProvider; this page no
    // longer keeps a private jobs state, warm cache, or writes the cache
    // (plan Stage 3b).
    const {currentJob, setCurrentJob, setLastResult, addToHistory, removeFromHistory, runtimeConfig, tasks: jobs, ingestJobs, markCancelled, revertCancelled, restoreTask, abortPendingUpload} = useApp();
    const {getJob, cancelJob, deleteJob, retryJob, createVideoSourceJob, fetchJobSourceFile, fetchJobArtifactFile, enqueueProcessFiles} = useApi();
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
    const queueUploadJob = currentJob?.queueUpload ? currentJob : null;
    const currentJobRecords = useMemo(() => jobsFromCurrentJob(currentJob), [currentJob]);
    // The shared list already reflects history + cache; merge in the live
    // currentJob record for immediate progress display.
    // Every task is listed and counted; there is no display window.
    const displayJobs = useMemo(() => (
        mergeJobs(currentJobRecords, jobs)
    ), [currentJobRecords, jobs]);
    const liveJobs = useMemo(() => displayJobs.filter(isLiveTask), [displayJobs]);
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
    const queuedCount = liveJobs.filter((job) => normalizeTaskState(job) === TASK_STATE_QUEUED).length;
    const runningCount = liveJobs.filter((job) => normalizeTaskState(job) === TASK_STATE_RUNNING || normalizeTaskState(job) === TASK_STATE_UPLOADING).length;

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
    }, [location.state?.queueSubmitError]);

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

    const retryStoredSourceJob = async (job, options) => {
        const taskId = taskIdForJob(job);
        const localOptions = isLocalJob(job) ? {sttProvider: 'local'} : {};
        if (runtimeConfig?.jobRetryFromStoredSource) {
            return await retryJob(taskId, localOptions);
        }
        const filename = job?.source_filename || job?.result?.filename || 'source';
        const sourceFile = await fetchJobSourceFile(taskId, filename, localOptions);
        return await enqueueProcessFiles([sourceFile], options);
    };

    const retryTerminalJob = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId || isLiveTask(job)) return;
        setRetryingTaskId(taskId);
        setError(null);
        setRetryError(null);
        try {
            const options = retryOptionsForJob(job);
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
                const response = await retryStoredSourceJob(job, options);
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
            const file = await fetchJobArtifactFile(taskId, 'debreath_media', name, {sttProvider: 'local'});
            downloadBrowserFile(file, name);
        } catch (error) {
            window.alert(error?.message || (lang === 'zh' ? '剪后文件不可用' : 'The cut file is unavailable'));
        } finally {
            setDownloadingTaskId('');
        }
    };

    const openResult = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        const open = (sourceJob, result) => {
            setLastResult(result);
            addToHistory(jobToHistoryEntry({...sourceJob, result}));
            navigate('/editor');
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

                {queueUploadJob ? <QueueUploadBanner upload={queueUploadJob} lang={lang} onCancel={cancelPendingUpload}/> : null}

                <section className="grid gap-3 sm:grid-cols-3">
                    <div className="rounded-[18px] border border-[#dedada] bg-white p-4 dark:border-white/[0.10] dark:bg-white/[0.055]">
                        <p className="text-[11px] font-extrabold text-[#85868c] dark:text-white/55">{lang === 'zh' ? '进行中' : 'Running'}</p>
                        <p className="mt-1 text-[22px] font-extrabold tabular-nums text-[#111111] dark:text-white">{runningCount}</p>
                    </div>
                    <div className="rounded-[18px] border border-[#dedada] bg-white p-4 dark:border-white/[0.10] dark:bg-white/[0.055]">
                        <p className="text-[11px] font-extrabold text-[#85868c] dark:text-white/55">{lang === 'zh' ? '排队中' : 'Queued'}</p>
                        <p className="mt-1 text-[22px] font-extrabold tabular-nums text-[#111111] dark:text-white">{queuedCount}</p>
                    </div>
                    <div className="rounded-[18px] border border-[#dedada] bg-white p-4 dark:border-white/[0.10] dark:bg-white/[0.055]">
                        <p className="text-[11px] font-extrabold text-[#85868c] dark:text-white/55">{lang === 'zh' ? '历史记录' : 'History records'}</p>
                        <p className="mt-1 text-[22px] font-extrabold tabular-nums text-[#111111] dark:text-white">{displayJobs.length}</p>
                    </div>
                </section>

                <section className="space-y-3">
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
                    {displayJobs.map((job) => (
                        <AgentTaskCard
                            key={taskIdForJob(job)}
                            job={job}
                            lang={lang}
                            aheadName={queueAheadName(job)}
                            retryError={retryError?.taskId === taskIdForJob(job) ? retryError.message : ''}
                            cancellingTaskId={cancellingTaskId}
                            deletingTaskId={deletingTaskId}
                            openingTaskId={openingTaskId}
                            retryingTaskId={retryingTaskId}
                            onCancel={cancelLiveJob}
                            onDelete={deleteTerminalJob}
                            downloadingTaskId={downloadingTaskId}
                            onDownloadCut={downloadCutFile}
                            onOpenResult={openResult}
                            onRetry={retryTerminalJob}
                        />
                    ))}
                </section>
            </div>
        </main>
    );
};

export default AgentTasks;
