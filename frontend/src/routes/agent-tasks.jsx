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
    sttProviderLabel,
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

const stageLabel = (job, lang) => {
    const isZh = lang === 'zh';
    const labels = {
        upload: isZh ? '接收材料' : 'Receiving',
        queued: isZh ? '等待开始' : 'Waiting',
        resolving: isZh ? '解析链接' : 'Resolving link',
        downloading: isZh ? '下载视频' : 'Downloading',
        saving: isZh ? '保存来源' : 'Saving source',
        audio: isZh ? '提取音频' : 'Extracting audio',
        stt: isZh ? '语音转写' : 'Transcribing',
        transcript_ready: isZh ? '整理转录' : 'Transcript ready',
        summary: isZh ? '生成笔记' : 'Generating note',
        export: isZh ? '导出飞书' : 'Exporting',
    };
    return labels[job?.stage] || statusLabel(job, lang);
};

const liveStageDetail = (job, lang) => {
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
        return lang === 'zh' ? '已经加入队列，会按顺序开始处理。' : 'Queued and waiting for its turn.';
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

const routeLabel = (job, lang) => {
    const provider = String(
        job?.metadata?.queue_options?.stt_provider
        || job?.metadata?.stt_provider
        || job?.result?.stt_provider
        || ''
    );
    return sttProviderLabel(provider, lang) || (lang === 'zh' ? '按任务决定' : 'Task default');
};

const fileInfoLabel = (job) => {
    const sizeMb = Number(job?.source_file_size_mb || job?.metadata?.file_size_mb || 0) || 0;
    const durationSec = Number(job?.result?.audio_duration_seconds || job?.source_duration_seconds || job?.metadata?.duration_seconds || 0) || 0;
    const parts = [];
    if (sizeMb) parts.push(fmtFileSize(sizeMb));
    if (durationSec) parts.push(fmtElapsed(durationSec));
    return parts.join(' · ') || '-';
};

const materialTypeLabel = (value, lang) => {
    const isZh = lang === 'zh';
    const labels = {
        course_transcript_file: isZh ? '课程字幕文件' : 'Course subtitle file',
        course_material: isZh ? '课程材料' : 'Course material',
        lecture_material: isZh ? '讲座材料' : 'Lecture material',
        sharing_session_material: isZh ? '分享讨论材料' : 'Sharing session',
        interview_material: isZh ? '访谈材料' : 'Interview material',
        meeting_material: isZh ? '会议材料' : 'Meeting material',
        research_material: isZh ? '研究材料' : 'Research material',
        briefing_material: isZh ? '资料解读材料' : 'Briefing material',
        training_material: isZh ? '培训材料' : 'Training material',
        learning_material: isZh ? '学习材料' : 'Learning material',
        course_video_pending_content: isZh ? '待转录课程视频' : 'Course video pending transcript',
        lecture_video_pending_content: isZh ? '待转录讲座视频' : 'Lecture video pending transcript',
        learning_material_pending_content: isZh ? '待判断学习材料' : 'Learning material pending transcript',
        course_or_lecture_pending_content: isZh ? '学习材料' : 'Learning material',
        course_notes: isZh ? '课程材料' : 'Course material',
        lecture_notes: isZh ? '讲座材料' : 'Lecture material',
        learning_notes: isZh ? '学习材料' : 'Learning material',
        course: isZh ? '课程材料' : 'Course material',
        lecture: isZh ? '讲座材料' : 'Lecture material',
        interview: isZh ? '访谈材料' : 'Interview material',
        meeting: isZh ? '会议材料' : 'Meeting material',
        research: isZh ? '研究材料' : 'Research material',
        career_talk: isZh ? '经验访谈' : 'Career talk',
        product_training: isZh ? '产品培训' : 'Product training',
    };
    return labels[String(value || '').trim()] || String(value || '').trim();
};

const materialDecisionFromLog = (job) => {
    const entries = job?.decision_log?.entries || job?.result?.decision_log?.entries || [];
    if (!Array.isArray(entries)) return '';
    const entry = entries.find((item) => {
        const id = String(item?.id || '').toLowerCase();
        const title = String(item?.title || '').toLowerCase();
        return id === 'material_classification' || title.includes('判断材料类型') || title.includes('material classification');
    });
    const status = String(entry?.status || '').toLowerCase();
    const decision = String(entry?.decision || '').trim();
    if (!decision || status === 'pending' || decision === '待判断' || decision.toLowerCase() === 'pending') return '';
    return decision;
};

const materialLabel = (job, lang) => {
    const value = String(
        job?.metadata?.material_type_label
        || job?.metadata?.material_type
        || job?.result?.material_type
        || job?.result?.processing_plan?.material?.type
        || job?.result?.processing_plan?.goal?.primary
        || ''
    ).trim();
    if (value) return materialTypeLabel(value, lang);
    const loggedDecision = materialDecisionFromLog(job);
    if (loggedDecision) return loggedDecision;
    const completed = normalizeTaskState(job) === TASK_STATE_COMPLETED || normalizeTaskState(job) === TASK_STATE_CACHED_ONLY;
    if (completed && (job?.result || job?.summary_status === 'completed')) {
        return lang === 'zh' ? '学习材料' : 'Learning material';
    }
    return lang === 'zh' ? '待判断' : 'Pending';
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

const AgentTaskCard = ({job, lang, cancellingTaskId, deletingTaskId, openingTaskId, retryingTaskId, onCancel, onDelete, onOpenResult, onRetry}) => {
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
            : liveStageDetail(job, lang);
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
        {label: lang === 'zh' ? '来源' : 'Source', value: sourceLabel(job, lang)},
        {label: lang === 'zh' ? '处理路线' : 'Route', value: routeLabel(job, lang)},
        {label: lang === 'zh' ? '文件信息' : 'File', value: fileInfoLabel(job)},
        {label: lang === 'zh' ? '判断材料类型' : 'Material', value: materialLabel(job, lang)},
    ];
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
            <div className="mt-4 grid gap-2 md:grid-cols-4">
                {metaItems.map((item) => (
                    <div key={item.label} className="min-w-0 rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-3 py-2 dark:border-white/[0.10] dark:bg-white/[0.04]">
                        <p className="text-[11px] font-extrabold text-[#85868c] dark:text-white/55">{item.label}</p>
                        <p className="mt-1 truncate text-[13px] font-extrabold text-[#111111] dark:text-white" title={item.value}>{item.value}</p>
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
    const {getJob, cancelJob, deleteJob, retryJob, createVideoSourceJob, fetchJobSourceFile, enqueueProcessFiles} = useApi();
    const location = useLocation();
    const navigate = useNavigate();
    const seededJob = location.state?.job && typeof location.state.job === 'object' ? location.state.job : null;
    const [cancellingTaskId, setCancellingTaskId] = useState('');
    const [openingTaskId, setOpeningTaskId] = useState('');
    const [deletingTaskId, setDeletingTaskId] = useState('');
    const [retryingTaskId, setRetryingTaskId] = useState('');
    const queueUploadJob = currentJob?.queueUpload ? currentJob : null;
    const currentJobRecords = useMemo(() => jobsFromCurrentJob(currentJob), [currentJob]);
    // The shared list already reflects history + cache; merge in the live
    // currentJob record for immediate progress display.
    const displayJobs = useMemo(() => (
        mergeJobs(currentJobRecords, jobs).slice(0, 30)
    ), [currentJobRecords, jobs]);
    const liveJobs = useMemo(() => displayJobs.filter(isLiveTask), [displayJobs]);
    const hasLiveOrUploadingJobs = Boolean(queueUploadJob) || liveJobs.length > 0;
    const queuedCount = liveJobs.filter((job) => normalizeTaskState(job) === TASK_STATE_QUEUED).length;
    const runningCount = liveJobs.filter((job) => normalizeTaskState(job) === TASK_STATE_RUNNING || normalizeTaskState(job) === TASK_STATE_UPLOADING).length;

    // Shared fetch + polling (see lib/useJobPolling.js). /agent warns only when
    // every fetch fails and uses task-oriented wording.
    const {loading, error, setError, loadJobs} = useJobPolling({
        hasLiveJobs: hasLiveOrUploadingJobs,
        errorOnAllOnly: true,
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
            setError(friendlyTaskError(err.message || String(err), lang));
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
                        <button type="button" onClick={loadJobs} className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-[#dedada] bg-white px-4 text-[13px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]">
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
                            cancellingTaskId={cancellingTaskId}
                            deletingTaskId={deletingTaskId}
                            openingTaskId={openingTaskId}
                            retryingTaskId={retryingTaskId}
                            onCancel={cancelLiveJob}
                            onDelete={deleteTerminalJob}
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
