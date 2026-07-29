/* ═══════════════ History ═══════════════ */
import {useEffect, useMemo, useState} from 'react';
import {useLocation, useNavigate, Link} from 'react-router-dom';
import {
    Activity,
    Download,
    ExternalLink,
    FileVideo,
    ListTodo,
    LoaderCircle,
    RefreshCw,
    RotateCcw,
    Subtitles,
    Trash2,
    XCircle,
} from 'lucide-react';
import {
    fmtBytes,
    fmtElapsed,
    fmtFileSize,
    friendlyTaskError,
    hasTranscriptResult,
    DropdownMenu,
    isSttProgressUnmeasured,
    jobToCurrentJob,
    jobDisplayTitle,
    sortJobsForHistoryView,
    timeAgo,
    useApi,
    useI18n,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {
    isCachedOnlyTask,
    isLiveTask,
    markBackendJob,
    normalizeTaskState,
    TASK_STATE_CANCELLED,
    TASK_STATE_CACHED_ONLY,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_QUEUED,
    TASK_STATE_RUNNING,
} from '../lib/taskState.js';
import {useJobPolling} from '../lib/useJobPolling.js';

const agentPlanSummary = (job, lang) => {
    const plan = job?.result?.processing_plan;
    const trace = job?.result?.tool_trace;
    if (!plan || typeof plan !== 'object') return null;
    const zh = lang === 'zh';
    const goal = (() => {
        const primary = plan.goal?.primary;
        if (primary === 'lecture_notes') return zh ? '讲座整理' : 'Lecture notes';
        if (primary === 'learning_notes') return zh ? '学习笔记' : 'Learning notes';
        if (primary === 'course_notes') return zh ? '课程笔记' : 'Course notes';
        return primary || null;
    })();
    const route = (() => {
        const strategy = plan.note_strategy || {};
        const mode = strategy.resolved_mode || strategy.selected_mode || strategy.requested_mode;
        if (mode === 'high_fidelity') return zh ? '高保真整理' : 'High fidelity';
        if (mode === 'chapter_coverage') return zh ? '章节覆盖' : 'Chapter coverage';
        if (mode === 'direct') return zh ? '直接生成' : 'Direct';
        return mode || null;
    })();
    const traceSteps = Array.isArray(trace?.steps) ? trace.steps : [];
    const friendlyTool = (step) => ({
        resolve_link: zh ? '解析链接' : 'Resolve link',
        download_video: zh ? '下载媒体' : 'Download media',
        extract_audio: zh ? '提取音频' : 'Extract audio',
        local_stt: zh ? '本机转录' : 'Local STT',
        cloud_stt: zh ? '云端转录' : 'Cloud STT',
        generate_note: zh ? '生成笔记' : 'Generate notes',
        save_artifacts: zh ? '保存产物' : 'Save artifacts',
        export_lark: zh ? '导出飞书' : 'Export Feishu',
    })[step.id] || step.label || step.tool;
    const completedTrace = traceSteps
        .filter((step) => step.status === 'completed')
        .map(friendlyTool)
        .filter(Boolean)
        .slice(0, 4);
    if (!goal && !route && completedTrace.length === 0) return null;
    return {goal, route, trace: completedTrace};
};

const taskIdForJob = (job) => String(job?.task_id || job?.result?.task_id || '').trim();

const Tasks = () => {
    const {t, lang} = useI18n();
    // Read the shared task list and mutations from AppProvider; this page no
    // longer keeps a private jobs state or writes the cache (plan Stage 3b).
    const {currentJob, setLastResult, setCurrentJob, tasks: jobs, ingestJobs, markCancelled, revertCancelled, removeFromHistory, restoreTask} = useApp();
    const {getJob, cancelJob, deleteJob, downloadJobArtifact, createVideoSourceJob, retryJob} = useApi();
    const navigate = useNavigate();
    const location = useLocation();
    const [deletingTaskId, setDeletingTaskId] = useState('');
    const [cancellingTaskId, setCancellingTaskId] = useState('');
    const [openingTaskId, setOpeningTaskId] = useState('');
    const [retryingTaskId, setRetryingTaskId] = useState('');
    const queueUploadJob = currentJob?.queueUpload ? currentJob : null;
    const isLiveJob = isLiveTask;
    const isDeletableJob = (job) => !isLiveJob(job) && (!!taskIdForJob(job) || isCachedOnlyTask(job));
    // A desktop-synced task was processed locally, but its record lives in the
    // cloud. Treating it as a local task on every device prevents a second
    // desktop from falling back to the readable cloud result.
    const isDesktopSyncJob = (job) => job?.metadata?.desktop_sync?.execution_location === 'local_desktop';
    const isLocalJob = (job) => !isDesktopSyncJob(job) && (
        String(job.client_id || '').startsWith('local-') || job.metadata?.stt_provider === 'local'
    );
    const [taskFilter, setTaskFilter] = useState('all');
    const stats = useMemo(() => ({
        live: jobs.filter(isLiveJob).length,
        failed: jobs.filter((job) => normalizeTaskState(job) === TASK_STATE_FAILED).length,
        completed: jobs.filter((job) => normalizeTaskState(job) === TASK_STATE_COMPLETED || isCachedOnlyTask(job)).length,
        all: jobs.length,
    }), [jobs]);
    const taskFilters = [
        ['all', lang === 'zh' ? '全部' : 'All', stats.all],
        ...(stats.live > 0 ? [['live', lang === 'zh' ? '进行中' : 'Active', stats.live]] : []),
        ['failed', t('tasks.failed'), stats.failed],
        ['completed', lang === 'zh' ? '历史' : 'History', stats.completed],
    ];
    const visibleJobs = useMemo(() => {
        return sortJobsForHistoryView(
            jobs.filter((job) => {
                if (taskFilter === 'live') return isLiveJob(job);
                const taskState = normalizeTaskState(job);
                if (taskFilter === 'failed') return taskState === TASK_STATE_FAILED || taskState === TASK_STATE_CANCELLED;
                if (taskFilter === 'completed') return taskState === TASK_STATE_COMPLETED || isCachedOnlyTask(job);
                return true;
            })
        );
    }, [jobs, taskFilter]);
    const hasLiveJobs = Boolean(queueUploadJob || jobs.some(isLiveJob));

    // Shared fetch + polling (see lib/useJobPolling.js). /tasks warns on any
    // failed fetch and uses record-oriented wording.
    const {loading, setLoading, error, setError, loadJobs, loadJobsRef} = useJobPolling({
        hasLiveJobs,
        errorOnAllOnly: false,
        refreshFailedZh: '记录刷新失败，已保留本地缓存。',
        refreshFailedEn: 'Failed to refresh records. Local cache is preserved.',
    });

    useEffect(() => {
        setError(location.state?.queueSubmitError || null);
    }, [location.state?.queueSubmitError]);

    useEffect(() => {
        if(location.state?.queueSubmitError) {
            navigate('/tasks', {replace:true, state:{}});
            return;
        }
        if(location.state?.queueSubmittedAt) {
            setLoading(true);
            loadJobsRef.current();
            navigate('/tasks', {replace:true, state:{}});
        }
    }, [location.state?.queueSubmitError, location.state?.queueSubmittedAt, navigate]);

    const getJobWithFallback = async (job) => {
        const taskId = taskIdForJob(job);
        if (isDesktopSyncJob(job)) {
            try {
                // The originating desktop still owns the editable local copy.
                // A second desktop will receive 404 here and then read the
                // cloud projection below.
                return await getJob(taskId, {sttProvider: 'local'});
            } catch (err) {
                if (err.status !== 404) throw err;
                return getJob(taskId);
            }
        }
        const primaryOptions = isLocalJob(job) ? {sttProvider: 'local'} : {};
        try {
            return await getJob(taskId, primaryOptions);
        } catch (err) {
            if (err.status !== 404 || primaryOptions.sttProvider === 'local') throw err;
            return getJob(taskId, {sttProvider: 'local'});
        }
    };

    const openJob = async (job) => {
        if (normalizeTaskState(job) === TASK_STATE_RUNNING) {
            setCurrentJob(jobToCurrentJob(job));
            return;
        }
        const taskId = taskIdForJob(job);
        const openResult = (sourceJob, result) => {
            const desktopSync = sourceJob?.metadata?.desktop_sync;
            const presentedResult = desktopSync ? {...result, desktop_sync: desktopSync} : result;
            setLastResult(presentedResult);
            ingestJobs([{...sourceJob, result: presentedResult}]);
            navigate('/editor');
        };
        if (isCachedOnlyTask(job) && job.result) {
            openResult(job, job.result);
            return;
        }
        setOpeningTaskId(taskId);
        try {
            const fresh = await getJobWithFallback(job);
            const result = fresh?.result || job.result;
            if (result) {
                openResult(fresh || job, result);
                return;
            }
            setError(lang === 'zh'
                ? '这条记录暂时没有可打开的结果。请点“详情”查看处理状态，或刷新后再试。'
                : 'This record does not have an openable result yet. Open Details or refresh and try again.');
        } catch (err) {
            if (err.status === 404 && job.result) {
                openResult(job, job.result);
                return;
            }
            setError(friendlyTaskError(err.message || String(err), lang));
        } finally {
            setOpeningTaskId('');
        }
    };

    const downloadArtifact = async (job, kind) => {
        const artifact = job.result?.artifacts?.[kind];
        await downloadJobArtifact(job.task_id, kind, artifact?.filename, isLocalJob(job) ? {sttProvider: 'local'} : {});
    };
    const cancelLiveJob = async (job) => {
        if (!isLiveJob(job)) return;
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        const confirmText = lang === 'zh'
            ? '取消这个正在处理的任务？已生成的完整结果不会保留。'
            : 'Cancel this active task? A complete result will not be kept.';
        if (!window.confirm(confirmText)) return;
        setCancellingTaskId(taskId);
        // Optimistically pin to cancelled through AppProvider; revert if the
        // backend cancel fails.
        markCancelled(taskId);
        try {
            await cancelJob(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            if (currentJob?.taskId === taskId) setCurrentJob(null);
            setError(null);
            loadJobs();
        } catch (err) {
            revertCancelled(taskId);
            setError(friendlyTaskError(err.message || String(err), lang));
            loadJobs();
        } finally {
            setCancellingTaskId('');
        }
    };

    const deleteFinishedJob = async (job) => {
        if (!isDeletableJob(job)) return;
        if (!window.confirm(t('tasks.deleteConfirm'))) return;
        const taskId = taskIdForJob(job);
        setDeletingTaskId(taskId);
        if (!taskId) {
            setDeletingTaskId('');
            return;
        }
        // Tombstone + drop through AppProvider so an in-flight poll cannot
        // resurrect it; restore if the backend delete fails (non-404).
        removeFromHistory(taskId);
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
            loadJobs();
        } finally {
            setDeletingTaskId('');
        }
    };

    const retryInputForJob = (job) => {
        const metadata = job?.metadata || {};
        const videoSource = metadata.video_source || {};
        return String(
            metadata.video_source_input_preview
            || videoSource.source_url
            || videoSource.url
            || videoSource.webpage_url
            || ''
        ).trim();
    };

    const retryOptionsForJob = (job) => {
        const queueOptions = job?.metadata?.queue_options;
        if (!queueOptions || typeof queueOptions !== 'object') return {};
        return {
            exportToLark: queueOptions.export_to_lark === true || queueOptions.export_to_lark === 'true',
            larkExportRoute: queueOptions.lark_export_route,
            larkViaCli: queueOptions.lark_via_cli === true || queueOptions.lark_via_cli === 'true',
            skipSummary: queueOptions.skip_summary === true || queueOptions.skip_summary === 'true',
            aiProvider: queueOptions.ai_provider,
            aiModel: queueOptions.ai_model,
            noteMode: queueOptions.note_mode,
            promptPreset: queueOptions.prompt_preset,
            promptPresetLabel: queueOptions.prompt_preset_label,
            sttProvider: queueOptions.stt_provider,
            sttModel: queueOptions.stt_model,
            sttSpeed: queueOptions.stt_speed,
            sttLanguage: queueOptions.stt_language,
            speakerDiarization: queueOptions.speaker_diarization === true || queueOptions.speaker_diarization === 'true',
        };
    };

    const hasReusableOssSource = (job) => {
        const metadata = job?.metadata || {};
        return metadata.source_storage === 'oss' && !!String(metadata.oss_upload_session_id || '').trim();
    };

    const canRetryJob = (job) => normalizeTaskState(job) === TASK_STATE_FAILED && (!!retryInputForJob(job) || hasReusableOssSource(job));

    const retryFailedJob = async (job) => {
        if (!canRetryJob(job)) return;
        const taskId = taskIdForJob(job);
        setRetryingTaskId(taskId);
        try {
            const input = retryInputForJob(job);
            const response = input
                ? await createVideoSourceJob(input, retryOptionsForJob(job))
                : await retryJob(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            const nextJob = response?.job ? markBackendJob(response.job) : null;
            if (nextJob) {
                ingestJobs([nextJob]);
                setTaskFilter('all');
                setError(null);
                if (nextJob.task_id) navigate(`/tasks/${encodeURIComponent(nextJob.task_id)}/agent`, {state: {job: nextJob}});
            } else {
                loadJobs();
            }
        } catch (err) {
            setError(friendlyTaskError(err.message || String(err), lang));
        } finally {
            setRetryingTaskId('');
        }
    };

    const statusLabel = (job) => {
        const taskState = normalizeTaskState(job);
        if (taskState === TASK_STATE_QUEUED) return t('tasks.queued');
        if (taskState === TASK_STATE_COMPLETED || isCachedOnlyTask(job)) return t('tasks.completed');
        if (taskState === TASK_STATE_FAILED) return t('tasks.failed');
        if (taskState === TASK_STATE_CANCELLED) return lang === 'zh' ? '已取消' : 'Cancelled';
        return t('tasks.running');
    };
    const statusClass = (job) => {
        const taskState = normalizeTaskState(job);
        return (
        taskState === TASK_STATE_COMPLETED || isCachedOnlyTask(job)
            ? 'bg-primary/10 text-primary border-primary/20'
            : taskState === TASK_STATE_FAILED
                ? 'bg-error-container text-on-error-container border-error/20'
                : taskState === TASK_STATE_CANCELLED
                    ? 'bg-surface-container text-on-surface-variant border-outline-variant/40'
                : taskState === TASK_STATE_QUEUED
                    ? 'bg-surface-container text-on-surface-variant border-outline-variant/40'
                    : 'bg-tertiary/10 text-tertiary border-tertiary/20'
        );
    };
    const formatUpdated = (job) => {
        const ts = Date.parse(job.updated_at || job.created_at || '');
        if (!ts) return '-';
        return timeAgo(ts, t);
    };
    const stageLabel = (job) => t(`status.${job.stage || (normalizeTaskState(job) === TASK_STATE_FAILED ? 'failed' : 'idle')}`);
    const liveStageDetail = (job) => {
        const snapshotStep = Array.isArray(job.task_snapshot?.steps)
            ? job.task_snapshot.steps.find((step) => step?.id === job.task_snapshot?.current_step)
            : null;
        if (snapshotStep?.detail) return snapshotStep.detail;
        const progressMeta = job.metadata?.video_source_progress || {};
        const loaded = progressMeta.loaded_bytes ? fmtBytes(progressMeta.loaded_bytes) : '';
        const total = progressMeta.total_bytes ? fmtBytes(progressMeta.total_bytes) : '';
        const byteText = loaded && total ? ` · ${loaded} / ${total}` : (loaded ? ` · ${loaded}` : '');
        if (progressMeta.message) return `${progressMeta.message}${byteText}`;
        const taskState = normalizeTaskState(job);
        if (taskState === TASK_STATE_QUEUED) return lang === 'zh' ? '等待处理开始。' : 'Waiting for processing to start.';
        if (taskState === TASK_STATE_RUNNING) return job.summary_status || stageLabel(job);
        return '-';
    };
    const taskFailureDetail = (job) => {
        const taskState = normalizeTaskState(job);
        if (taskState === TASK_STATE_CANCELLED) return lang === 'zh' ? '用户已取消这个任务。' : 'This task was cancelled by the user.';
        const snapshotFailure = job.task_snapshot?.failure_reason || '';
        if (snapshotFailure) return snapshotFailure;
        if (taskState !== TASK_STATE_FAILED) return '';
        const raw = job.error_reason || job.result?.summary_error || job.result?.error_reason || job.metadata?.raw_error || '';
        return raw
            ? friendlyTaskError(raw, lang)
            : (lang === 'zh' ? '任务处理失败，但后端没有返回具体原因。' : 'The task failed, but no detailed reason was returned.');
    };
    const artifactButtons = [
        ['transcript_srt', t('tasks.srt')],
        ['transcript_txt', t('tasks.txt')],
        ['transcript_vtt', t('tasks.vtt')],
        ['transcript_bilingual_srt', t('tasks.bilingualSrt')],
        ['transcript_bilingual_vtt', t('tasks.bilingualVtt')],
        ['summary_md', t('tasks.md')],
    ];

    return (
        <div className="ml-[var(--sidebar-offset)] min-h-screen bg-[#f8f7fb] pb-8 text-[#111111] transition-[margin] duration-200 ease-out dark:bg-[#101010] dark:text-white/[0.92]">
            <main className="mx-auto h-dvh max-w-[1500px] overflow-y-auto px-8 py-7 hide-scrollbar">
                <div className="space-y-5">
                    <div className="flex flex-col gap-3 rounded-[22px] border border-[#e4e0e0] bg-white px-3 py-3 shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] md:flex-row md:items-center md:justify-between dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                        <div className="flex flex-wrap gap-1.5">
                            {taskFilters.map(([key, label, count]) => (
                                <button key={key} type="button" onClick={() => setTaskFilter(key)} className={`inline-flex h-9 items-center gap-1.5 rounded-[14px] px-3 text-xs font-bold transition ${taskFilter === key ? 'bg-[#111111] text-white dark:bg-white dark:text-[#111111]' : 'text-[#666] hover:bg-[#efeeee] hover:text-[#111111] dark:text-white/55 dark:hover:bg-white/[0.08] dark:hover:text-white'}`}>
                                    {label}
                                    <span className={`tabular-nums ${taskFilter === key ? 'text-white dark:text-[#111111]' : 'text-[#8a8a8a] dark:text-white/40'}`}>{count}</span>
                                </button>
                            ))}
                        </div>
                        <button type="button" onClick={loadJobs} className="inline-flex h-9 items-center justify-center gap-1.5 rounded-[14px] px-3 text-xs font-bold text-[#666] hover:bg-[#efeeee] hover:text-[#111111] dark:text-white/55 dark:hover:bg-white/[0.08] dark:hover:text-white">
                            <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} strokeWidth={2.15}/>
                            {t('tasks.refresh')}
                        </button>
                    </div>

                    {error && (
                        <div className="rounded-[16px] border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">{error}</div>
                    )}

                    <section className="space-y-3">
                        {queueUploadJob && (
                            <article className="rounded-[24px] border border-[#e4e0e0] bg-white p-5 shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                                    <div className="flex items-start gap-3 min-w-0">
                                        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[14px] bg-[#efeeee] text-[#111111] dark:bg-white/[0.12] dark:text-white">
                                            <LoaderCircle className="size-5 animate-spin" strokeWidth={2.15}/>
                                        </div>
                                        <div className="min-w-0">
                                            <h2 className="text-base font-headline font-bold text-on-surface dark:text-white">
                                                {lang === 'zh' ? '正在写入历史记录' : 'Uploading to History'}
                                            </h2>
                                            <p className="text-sm text-on-surface-variant mt-1">
                                                {lang === 'zh'
                                                    ? `已选择 ${queueUploadJob.queueTotal || 0} 个文件，上传完成后会自动出现在历史记录里。`
                                                    : `${queueUploadJob.queueTotal || 0} files selected. They will appear in History after upload finishes.`}
                                            </p>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-2 gap-3 lg:w-[280px]">
                                        <div className="rounded-sm bg-surface-container-low px-3 py-2">
                                            <p className="text-[10px] uppercase tracking-wider font-bold text-outline">{t('dash.elapsed')}</p>
                                            <p className="text-sm font-semibold text-on-surface mt-1">{fmtElapsed(Math.floor((Date.now() - (queueUploadJob.startedAt || Date.now())) / 1000))}</p>
                                        </div>
                                        <div className="rounded-sm bg-surface-container-low px-3 py-2">
                                            <p className="text-[10px] uppercase tracking-wider font-bold text-outline">{t('dash.fileSize')}</p>
                                            <p className="text-sm font-semibold text-on-surface mt-1">{fmtFileSize(queueUploadJob.fileSizeMb)}</p>
                                        </div>
                                    </div>
                                </div>
                                <div className="mt-4 h-2 overflow-hidden rounded-full bg-[#efeeee] progress-indeterminate dark:bg-white/[0.12]"></div>
                            </article>
                        )}
                        {visibleJobs.length === 0 && !loading && !queueUploadJob && (
                            <div className="rounded-[24px] border border-[#e4e0e0] bg-white p-10 text-center shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                                <ListTodo className="mx-auto mb-3 size-10 text-[#8a8a8a] dark:text-white/40" strokeWidth={2}/>
                                <p className="text-sm font-semibold text-[#777] dark:text-white/55">{taskFilter === 'all' ? t('tasks.empty') : (lang === 'zh' ? '这个分类下暂时没有记录。' : 'No records in this view.')}</p>
                            </div>
                        )}
                        {visibleJobs.map((job) => {
                            const taskState = normalizeTaskState(job);
                            const progress = Math.max(0, Math.min(100, Number(job.progress) || (taskState === TASK_STATE_COMPLETED || taskState === TASK_STATE_CACHED_ONLY ? 100 : 0)));
                            const result = job.result || {};
                            const artifacts = result.artifacts || {};
                            const availableArtifacts = artifactButtons.filter(([kind]) => artifacts[kind]);
                            const larkUrl = result.lark_response?.url || result.feishu_doc_url || null;
                            const canOpen = hasTranscriptResult(result) || (!!result && (taskState === TASK_STATE_COMPLETED || taskState === TASK_STATE_CACHED_ONLY));
                            const planSummary = agentPlanSummary(job, lang);
                            const displayTitle = jobDisplayTitle(job, lang);
                            const failureDetail = taskFailureDetail(job);
                            const taskId = taskIdForJob(job);
                            const canRetry = canRetryJob(job);
                            const downloadItems = [
                                ...availableArtifacts.map(([kind, label]) => ({icon:'download', label, badge:kind.endsWith('vtt')?'VTT':kind.endsWith('srt')?'SRT':kind.endsWith('txt')?'TXT':kind.endsWith('md')?'MD':kind, onClick:()=>downloadArtifact(job,kind)})),
                                ...(larkUrl ? [{divider:true},{icon:'open_in_new', label:t('tasks.larkDoc'), onClick:()=>window.open(larkUrl,'_blank','noopener')}] : []),
                            ];
                            return (
                                <article key={taskId || `cached-${job.updated_at || job.created_at || displayTitle}`} className="rounded-[24px] border border-[#e4e0e0] bg-white p-4 shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                                    <div className="flex items-start gap-3">
                                        <div className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[14px] ${isLiveJob(job) ? 'bg-[#efeeee] text-[#111111] dark:bg-white/[0.12] dark:text-white' : taskState === TASK_STATE_FAILED ? 'bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-300' : 'bg-[#efeeee] text-[#111111] dark:bg-white/[0.12] dark:text-white'}`}>
                                            {job.source_type === 'transcript_file'
                                                ? <Subtitles className="size-5" strokeWidth={2.15}/>
                                                : <FileVideo className="size-5" strokeWidth={2.15}/>}
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <h2 className="max-w-[520px] truncate font-headline text-sm font-extrabold text-[#111111] dark:text-white" title={displayTitle}>{displayTitle}</h2>
                                                <span className={`rounded-full border px-2.5 py-1 text-[11px] font-bold flex-shrink-0 ${statusClass(job)}`}>{statusLabel(job)}</span>
                                                <span className="text-xs font-medium text-[#777] dark:text-white/55 flex-shrink-0">{formatUpdated(job)}</span>
                                            </div>
                                            {planSummary && (
                                                <p className="mt-1.5 text-xs font-medium leading-relaxed text-on-surface-variant">
                                                    <span className="mr-1 font-semibold">{planSummary.goal}</span>
                                                    {[planSummary.route, ...(planSummary.trace.length > 0 ? [planSummary.trace.join(' → ')] : [])].filter(Boolean).join(' · ')}
                                                </p>
                                            )}
                                            {isLiveJob(job) && (
                                            <div className="mt-2 ml-0 space-y-2">
                                                <div className={`h-1.5 overflow-hidden rounded-full bg-[#efeeee] dark:bg-white/[0.12] ${isSttProgressUnmeasured(jobToCurrentJob(job)) ? 'progress-indeterminate' : ''}`}>
                                                    {!isSttProgressUnmeasured(jobToCurrentJob(job)) && <div className="h-full bg-[#111111] transition-all duration-500 dark:bg-white" style={{width:`${progress}%`}}></div>}
                                                </div>
                                                <p className="rounded-sm bg-surface-container-low px-3 py-2 text-xs font-semibold leading-relaxed text-on-surface-variant">
                                                    {liveStageDetail(job)}
                                                </p>
                                            </div>
                                            )}
                                            {failureDetail && (
                                                <p className="mt-2 rounded-sm border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold leading-relaxed text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
                                                    {failureDetail}
                                                </p>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-1.5 flex-shrink-0">
                                            {isLiveJob(job) ? (
                                                <button type="button" disabled={cancellingTaskId === taskId} onClick={() => cancelLiveJob(job)} className="inline-flex h-10 items-center justify-center gap-2 rounded-[14px] border border-red-200 bg-red-50 px-3.5 text-xs font-bold text-red-600 transition-colors hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20">
                                                    {cancellingTaskId === taskId
                                                        ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/>
                                                        : <XCircle className="size-4" strokeWidth={2.15}/>}
                                                    {lang === 'zh' ? '取消' : 'Cancel'}
                                                </button>
                                            ) : (
                                                <>
                                                    {taskState === TASK_STATE_FAILED && canRetry ? (
                                                        <button type="button" disabled={retryingTaskId === taskId} onClick={() => retryFailedJob(job)} className="inline-flex h-10 items-center justify-center gap-2 rounded-[14px] bg-[#111111] px-3.5 text-xs font-bold text-white transition-colors hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                                                            {retryingTaskId === taskId
                                                                ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/>
                                                                : <RotateCcw className="size-4" strokeWidth={2.15}/>}
                                                            {lang === 'zh' ? '重新处理' : 'Retry'}
                                                        </button>
                                                    ) : (
                                                        <button type="button" disabled={!canOpen || openingTaskId === taskId} onClick={() => openJob(job)} className="inline-flex h-10 items-center justify-center gap-2 rounded-[14px] bg-[#111111] px-3.5 text-xs font-bold text-white transition-colors hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                                                            {openingTaskId === taskId ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <ExternalLink className="size-4" strokeWidth={2.15}/>}
                                                            {t('tasks.open')}
                                                        </button>
                                                    )}
                                                    {taskId ? (
                                                        <Link to={`/tasks/${encodeURIComponent(taskId)}/agent`} state={{job}} className="inline-flex h-10 items-center justify-center gap-1.5 rounded-[14px] border border-[#dedada] bg-[#f4f3f3] px-3 text-xs font-bold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]">
                                                            <Activity className="size-3.5" strokeWidth={2.15}/>
                                                            {lang === 'zh' ? '详情' : 'Details'}
                                                        </Link>
                                                    ) : null}
                                                    {downloadItems.length > 0 ? (
                                                        <DropdownMenu
                                                            align="right"
                                                            trigger={<button type="button" className="inline-flex h-10 items-center justify-center gap-1.5 rounded-[14px] border border-[#dedada] bg-[#f4f3f3] px-3 text-xs font-bold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]"><Download className="size-4" strokeWidth={2.15}/></button>}
                                                            items={downloadItems}
                                                        />
                                                    ) : null}
                                                    <button type="button" disabled={!isDeletableJob(job) || deletingTaskId === taskId} onClick={() => deleteFinishedJob(job)} className="inline-flex h-10 items-center justify-center gap-2 rounded-[14px] border border-red-200 bg-red-50 px-3.5 text-xs font-bold text-red-600 transition-colors hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20">
                                                        {deletingTaskId === taskId
                                                            ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/>
                                                            : <Trash2 className="size-4" strokeWidth={2.15}/>}
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    </div>
                                </article>
                            );
                        })}
                    </section>
                </div>
            </main>
        </div>
    );
};

export default Tasks;
