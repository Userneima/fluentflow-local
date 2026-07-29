import {useState} from 'react';
import {Link} from 'react-router-dom';
import {ListPlus, XCircle} from 'lucide-react';
import {
    fmtBytes,
    fmtElapsed,
    isSttProgressUnmeasured,
    jobProgressLabel,
    noteModeLabel,
    sttProgressFraction,
    sttStatusLabel,
    useApi,
    useI18n,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';

const stageLabel = (stage, lang) => {
    const isZh = lang === 'zh';
    const labels = {
        queued: isZh ? '排队中' : 'Queued',
        upload: isZh ? '接收材料' : 'Receiving',
        resolving: isZh ? '解析链接' : 'Resolving link',
        downloading: isZh ? '下载视频' : 'Downloading',
        saving: isZh ? '保存来源' : 'Saving source',
        audio: isZh ? '提取音频' : 'Extracting audio',
        stt: isZh ? '转录中' : 'Transcribing',
        transcript_ready: isZh ? '转录完成' : 'Transcript ready',
        cleanup: isZh ? '整理转录' : 'Cleaning transcript',
        summary: isZh ? '生成笔记' : 'Generating note',
        export: isZh ? '导出飞书' : 'Exporting',
        failed: isZh ? '失败' : 'Failed',
        error: isZh ? '失败' : 'Failed',
        cancelled: isZh ? '已取消' : 'Cancelled',
        done: isZh ? '已完成' : 'Done',
    };
    return labels[stage] || (isZh ? '处理中' : 'Processing');
};

const normalizeTask = (pageData, currentJob) => {
    const task = pageData?.task || {};
    const source = pageData?.source || {};
    const videoSource = source.video_source || {};
    const videoSourceProgress = task.video_source_progress || source.video_source_progress || null;
    const videoSourceFileSizeMb = videoSourceProgress?.total_bytes ? Number(videoSourceProgress.total_bytes) / 1024 / 1024 : null;
    const sameCurrentJob = currentJob?.taskId && currentJob.taskId === task.task_id;
    const current = sameCurrentJob ? currentJob : null;
    return {
        taskId: task.task_id || current?.taskId,
        title: task.title || source.display_title || pageData?.title || current?.fileName || task.filename,
        stage: current?.stage || task.stage || (task.status === 'completed' ? 'done' : task.status || 'queued'),
        status: task.status || current?.taskState || null,
        progress: current?.progress ?? task.progress ?? (task.status === 'completed' ? 100 : 0),
        sttStatus: current?.sttStatus || null,
        videoSourceProgress,
        sourceType: current?.sourceType || task.source_type || source.type || null,
        sourceFilename: current?.fileName || task.filename || source.filename || null,
        sourceUrl: source.url || videoSource.url || videoSource.webpage_url || null,
        sourcePlatform: source.platform || videoSource.provider || videoSource.extractor_key || videoSource.extractor || null,
        fileSizeMb: current?.fileSizeMb ?? task.file_size_mb ?? source.file_size_mb ?? videoSourceFileSizeMb,
    };
};

const InfoTile = ({label, value, wrap = false}) => {
    if (!value) return null;
    return (
        <div className="min-w-0 rounded-[15px] border border-[#dedada] bg-[#fbfbfb] px-3 py-2.5 dark:border-white/[0.12] dark:bg-white/[0.05]">
            <p className="text-[11px] font-extrabold text-[#676970] dark:text-white/[0.72]">{label}</p>
            <p className={`mt-1 text-[14px] font-extrabold text-[#111111] dark:text-white ${wrap ? 'whitespace-normal break-words leading-5' : 'truncate'}`} title={String(value)}>
                {value}
            </p>
        </div>
    );
};

const sourcePlatformLabel = (source, isZh) => {
    const raw = String(source?.sourcePlatform || '').toLowerCase();
    const url = String(source?.sourceUrl || '').trim();
    let host = '';
    try {
        host = new URL(url).hostname.replace(/^www\./, '').toLowerCase();
    } catch (_) {}
    const probe = `${raw} ${host}`;
    if (probe.includes('bilibili') || host === 'b23.tv') return 'Bilibili';
    if (probe.includes('youtube') || host === 'youtu.be') return 'YouTube';
    if (probe.includes('douyin')) return isZh ? '抖音' : 'Douyin';
    if (probe.includes('xiaohongshu')) return isZh ? '小红书' : 'Xiaohongshu';
    if (host) return host;
    return '';
};

const localFileKindLabel = (filename, isZh) => {
    const name = String(filename || '').toLowerCase();
    if (/\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(name)) return isZh ? '本地视频文件' : 'local video file';
    if (/\.(mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(name)) return isZh ? '本地音频文件' : 'local audio file';
    if (/\.(srt|vtt|txt|md)$/i.test(name)) return isZh ? '本地字幕文件' : 'local subtitle file';
    return '';
};

const sourceKindLabel = (source, isZh) => {
    const value = String(source?.sourceType || '').toLowerCase();
    const platform = sourcePlatformLabel(source, isZh);
    if (value === 'video_link') return platform || (isZh ? '视频平台链接' : 'video platform link');
    if (value === 'transcript_file') return isZh ? '本地字幕文件' : 'local subtitle file';
    if (value === 'audio_file' || value === 'audio') return isZh ? '本地音频文件' : 'local audio file';
    if (value === 'video_file' || value === 'video' || value === 'queue_upload') return isZh ? '本地视频文件' : 'local video file';
    if (value === 'text') return isZh ? '文本材料' : 'text material';
    return platform || localFileKindLabel(source?.sourceFilename, isZh) || (isZh ? '本地文件' : 'local file');
};

const routeLabel = (route, isZh) => {
    const transcription = String(route?.transcription || route?.stt_provider || '').toLowerCase();
    const provider = String(route?.stt_provider || '').trim();
    const model = String(route?.stt_model || '').trim();
    const modelText = model ? `${model}${isZh ? ' 模型' : ' model'}` : '';
    if (transcription === 'transcript_file') return isZh ? '导入字幕整理' : 'subtitle import';
    if (transcription === 'local' || provider === 'local') {
        return [isZh ? '本地转写' : 'local transcription', modelText].filter(Boolean).join(isZh ? ' · ' : ' · ');
    }
    if (transcription === 'cloud' || provider) {
        return [isZh ? '云端转写' : 'cloud transcription', provider, modelText].filter(Boolean).join(isZh ? ' · ' : ' · ');
    }
    return '';
};

const currentJobRouteLabel = (job, isZh) => {
    if (!job || job.sourceType === 'transcript_file') return isZh ? '导入字幕整理' : 'subtitle import';
    const local = String(job.sttProvider || '').toLowerCase() === 'local';
    const route = local ? (isZh ? '本地转写' : 'local transcription') : (isZh ? '云端转写' : 'cloud transcription');
    const model = job.sttModel ? `${job.sttModel}${isZh ? ' 模型' : ' model'}` : '';
    return [route, model, job.sttSpeed].filter(Boolean).join(' / ');
};

const noteModeTag = (pageData, lang) => {
    const mode = String(
        pageData?.note?.resolved_mode
        || pageData?.processing_plan?.note_strategy?.resolved_mode
        || pageData?.processing_plan?.note_strategy?.selected_mode
        || ''
    ).trim();
    if (!mode || !['high_fidelity', 'chapter_coverage'].includes(mode)) return '';
    return noteModeLabel(mode, lang);
};

const noteWordCountTag = (pageData, lang) => {
    const explicitChars = Number(pageData?.note?.markdown_chars);
    const markdown = String(pageData?.note?.markdown || '');
    const chars = Number.isFinite(explicitChars) && explicitChars > 0 ? explicitChars : markdown.trim().length;
    if (!chars) return '';
    const formatted = chars.toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US');
    return lang === 'zh' ? `${formatted} 字` : `${formatted} chars`;
};

const TaskProgressOverview = ({pageData, materialJudgment = ''}) => {
    const {lang, t} = useI18n();
    const {cancelJob, cancelGuestTrialJob} = useApi();
    const {currentJob, setCurrentJob} = useApp();
    const [cancelBusy, setCancelBusy] = useState(false);
    const [cancelError, setCancelError] = useState('');
    const task = normalizeTask(pageData, currentJob);
    const isZh = lang === 'zh';
    const progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
    const progressText = task.taskId === currentJob?.taskId
        ? jobProgressLabel({...currentJob, progress}, t)
        : `${Math.round(progress)}%`;
    const sttUnknown = task.taskId === currentJob?.taskId && isSttProgressUnmeasured(currentJob);
    const stateText = String(task.stage || task.status || '').toLowerCase();
    const failed = ['failed', 'error', 'cancelled'].includes(stateText);
    const completed = !failed && (stateText === 'done' || stateText === 'completed' || String(task.status || '').toLowerCase() === 'completed' || progress >= 100);
    const running = !completed && !failed;
    const activeCurrentJob = running && currentJob?.taskId && currentJob.taskId === task.taskId ? currentJob : null;
    const diagnosis = pageData?.diagnosis || pageData?.note?.diagnosis || {};
    const route = routeLabel(pageData?.task_snapshot?.route || {}, isZh);
    const noteMode = noteModeTag(pageData, lang);
    const noteWordCount = noteWordCountTag(pageData, lang);
    const source = sourceKindLabel(task, isZh);
    const duration = Number(pageData?.task?.duration_seconds || pageData?.source?.duration_seconds);
    const durationText = Number.isFinite(duration) && duration > 0
        ? `${Math.floor(duration / 60)}:${String(Math.floor(duration % 60)).padStart(2, '0')}`
        : '';
    const fileSizeText = task.fileSizeMb ? fmtBytes(task.fileSizeMb * 1024 * 1024) : '';
    const currentStageLabel = stageLabel(task.stage, lang);
    const displayTitle = String(task.title || '').trim();
    const headline = failed
        ? (isZh ? '需要处理这个失败任务' : 'This task needs attention')
        : completed
            ? (displayTitle || (isZh ? '这条记录处理完成' : 'This record is complete'))
            : currentStageLabel;
    const runningDetail = task.stage === 'stt' && sttUnknown
        ? sttStatusLabel(task.sttStatus, t)
        : [
            `${isZh ? '当前阶段' : 'Current stage'}：${currentStageLabel}`,
            `${isZh ? '进度' : 'Progress'}：${progressText}`,
            route ? `${isZh ? '路线' : 'Route'}：${route}` : '',
        ].filter(Boolean).join(isZh ? ' · ' : ' · ');
    const activeJobElapsed = activeCurrentJob ? Math.max(0, Math.floor((Date.now() - (activeCurrentJob.startedAt || Date.now())) / 1000)) : 0;
    const activeJobRoute = activeCurrentJob ? currentJobRouteLabel(activeCurrentJob, isZh) : '';
    const activeJobSummaryMode = activeCurrentJob
        ? (activeCurrentJob.skipSummary
            ? (isZh ? '仅转录' : 'Transcript only')
            : `${isZh ? '生成 AI 摘要' : 'AI summary'} / ${noteModeLabel(activeCurrentJob.noteMode, lang)}`)
        : '';
    const activeJobFileSize = activeCurrentJob?.fileSizeMb ? fmtBytes(activeCurrentJob.fileSizeMb * 1024 * 1024) : fileSizeText;
    const activeJobSttProgress = activeCurrentJob ? Math.round(sttProgressFraction(activeCurrentJob) * 100) : 0;
    const activeJobHasSttTiming = !!activeCurrentJob && activeCurrentJob.stage === 'stt' && activeCurrentJob.durationSeconds > 0 && !sttUnknown;
    const activeJobTranscriptLine = activeJobHasSttTiming
        ? `${isZh ? '已转录' : 'Transcribed'}: ${fmtElapsed(activeCurrentJob.transcribedSeconds || 0)} / ${fmtElapsed(activeCurrentJob.durationSeconds || 0)}`
        : (activeCurrentJob?.stage === 'stt' ? sttStatusLabel(activeCurrentJob.sttStatus, t) : '');
    const handleCancel = async () => {
        if (!activeCurrentJob?.taskId || cancelBusy) return;
        const confirmText = isZh
            ? '取消当前正在处理的任务？任务会中止，完整结果不会生成；可到处理记录查看已取消记录。这不是删除历史记录。'
            : 'Cancel the active task? The task will stop and a complete result will not be created. You can check the cancelled record in Processing records. This does not delete history.';
        if (!window.confirm(confirmText)) return;
        setCancelBusy(true);
        setCancelError('');
        try {
            if (activeCurrentJob.guestTrial) {
                await cancelGuestTrialJob(activeCurrentJob.taskId, activeCurrentJob.guestToken);
            } else {
                await cancelJob(activeCurrentJob.taskId, {sttProvider: activeCurrentJob.sttProvider});
            }
            setCurrentJob((prev) => prev?.taskId === activeCurrentJob.taskId ? null : prev);
        } catch (exc) {
            setCancelError(exc.message || String(exc));
        } finally {
            setCancelBusy(false);
        }
    };

    const badgeText = activeCurrentJob
        ? (isZh ? '当前任务' : 'Active task')
        : completed
            ? (isZh ? '已完成记录' : 'Completed record')
            : failed
                ? (isZh ? '处理失败' : 'Failed')
                : (isZh ? '任务记录' : 'Task record');
    const overviewTitle = displayTitle || activeCurrentJob?.fileName || task.taskId;
    const overviewHint = activeCurrentJob
        ? (isZh ? '你可以离开本页，进度会在记录里继续更新。' : 'You can leave this page; progress will continue updating in records.')
        : completed
            ? (isZh ? '处理已完成，可以打开结果继续复查。' : 'Processing is complete. Open the result to review it.')
            : failed
                ? (diagnosis.detail || diagnosis.next_action || (isZh ? '查看失败原因，按建议重新处理。' : 'Review the failure reason and retry if needed.'))
                : runningDetail;
    const progressValue = completed ? 100 : failed ? Math.max(progress, 8) : progress;
    const canShowSttLine = activeCurrentJob?.stage === 'stt';
    const infoCards = activeCurrentJob ? [
        {label: isZh ? '已用时间' : 'Elapsed', value: fmtElapsed(activeJobElapsed)},
        {label: isZh ? '文件大小' : 'File size', value: activeJobFileSize},
        {label: isZh ? '转录路线' : 'Route', value: activeJobRoute || route || stageLabel(task.stage, lang)},
        {label: isZh ? '摘要模式' : 'Summary mode', value: activeJobSummaryMode},
    ] : [
        {label: isZh ? '来源' : 'Source', value: source},
        {label: isZh ? '处理路线' : 'Route', value: route || stageLabel(task.stage, lang)},
        {label: isZh ? '文件信息' : 'File', value: [fileSizeText, durationText].filter(Boolean).join(' · ')},
        {label: isZh ? '判断材料类型' : 'Material type', value: materialJudgment, wrap: true},
    ];

    return (
        <section className={`rounded-[26px] border p-5 shadow-[0_18px_44px_-38px_rgba(17,17,17,.45)] dark:shadow-none ${
            failed
                ? 'border-red-200 bg-red-50 dark:border-red-500/20 dark:bg-red-500/10'
                : 'border-[#dedada] bg-white dark:border-white/[0.12] dark:bg-white/[0.07]'
        }`}>
            <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="inline-flex rounded-full bg-[#efeeee] px-3 py-1 text-[12px] font-extrabold text-[#111111] dark:bg-white/[0.13] dark:text-white">
                            {badgeText}
                        </span>
                        {noteMode && (
                            <span className="rounded-full border border-[#dedada] bg-[#fbfbfb] px-2.5 py-1 text-[11px] font-extrabold text-[#57585d] dark:border-white/[0.14] dark:bg-white/[0.07] dark:text-white/[0.76]">
                                {noteMode}
                            </span>
                        )}
                        {noteWordCount && (
                            <span className="rounded-full border border-[#dedada] bg-[#fbfbfb] px-2.5 py-1 text-[11px] font-extrabold text-[#57585d] dark:border-white/[0.14] dark:bg-white/[0.07] dark:text-white/[0.76]">
                                {noteWordCount}
                            </span>
                        )}
                    </div>
                    <h2 className="mt-3 max-w-[48rem] truncate font-headline text-[26px] font-extrabold leading-tight text-[#111111] dark:text-white" title={overviewTitle || undefined}>
                        {overviewTitle || headline}
                    </h2>
                    <p className="mt-2 text-[14px] font-semibold leading-6 text-[#676970] dark:text-white/[0.62]">
                        {overviewHint}
                    </p>
                </div>
                {activeCurrentJob && (
                    <div className="flex shrink-0 flex-wrap gap-2">
                        <Link
                            to="/media-text?mode=media"
                            className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-3 text-[12px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.14] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]"
                        >
                            <ListPlus className="size-4" strokeWidth={2.2}/>
                            {isZh ? '添加新任务' : 'Add new task'}
                        </Link>
                        <button
                            type="button"
                            onClick={handleCancel}
                            disabled={cancelBusy}
                            className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-red-200 bg-red-50 px-3 text-[12px] font-extrabold text-red-600 transition hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20"
                        >
                            <XCircle className="size-4" strokeWidth={2.15}/>
                            {cancelBusy ? (isZh ? '取消中...' : 'Cancelling...') : (isZh ? '取消任务' : 'Cancel task')}
                        </button>
                    </div>
                )}
            </div>

            <div className="mt-7">
                <div className="mb-2 flex items-end justify-between gap-4">
                    <div>
                        <p className="text-[12px] font-extrabold text-[#676970] dark:text-white/[0.60]">
                            {isZh ? '当前阶段' : 'Current stage'}
                        </p>
                        <p className="text-[22px] font-extrabold text-[#111111] dark:text-white">
                            {currentStageLabel}
                        </p>
                    </div>
                    <p className="font-headline text-[30px] font-extrabold tabular-nums text-[#111111] dark:text-white">
                        {completed ? '100%' : failed ? '-' : progressText}
                    </p>
                </div>
                <div className={`h-2.5 w-full overflow-hidden rounded-full bg-[#efeeee] dark:bg-white/[0.12] ${running && sttUnknown ? 'progress-indeterminate' : ''}`}>
                    {!sttUnknown && (
                        <div className={`h-full rounded-full transition-all duration-700 ${failed ? 'bg-red-500' : 'bg-[#111111] dark:bg-white'}`} style={{width: `${progressValue}%`}}/>
                    )}
                </div>
                {canShowSttLine && (
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[12px] font-semibold text-[#676970] dark:text-white/[0.62]">
                        <span>{activeJobTranscriptLine}</span>
                        <span className="font-extrabold text-[#111111] dark:text-white">
                            {sttUnknown ? (isZh ? 'STT 计算中' : 'STT measuring') : `STT ${activeJobSttProgress}%`}
                        </span>
                    </div>
                )}
                {cancelError && (
                    <p className="mt-3 rounded-[14px] border border-red-200 bg-red-50 px-3 py-2 text-[12px] font-semibold text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
                        {cancelError}
                    </p>
                )}
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {infoCards.map((item) => (
                    <InfoTile key={item.label} label={item.label} value={item.value} wrap={item.wrap}/>
                ))}
            </div>
        </section>
    );
};

export default TaskProgressOverview;
