import {useMemo} from 'react';
import {Link, Navigate} from 'react-router-dom';
import {ArrowRight, ListChecks, Plus, Workflow} from 'lucide-react';
import {
    historyEntryToResult,
    useI18n,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';

const recentTaskIdFromHistory = (history) => {
    const entry = Array.isArray(history) ? history.find((item) => item?.taskId) : null;
    return entry?.taskId || '';
};

const jobFromResult = (result) => {
    if (!result?.task_id) return null;
    return {
        task_id: result.task_id,
        status: result.summary_status === 'failed' ? 'failed' : 'completed',
        stage: result.summary_status === 'failed' ? 'failed' : 'done',
        progress: 100,
        source_type: result.source || null,
        source_filename: result.filename || result.display_title || result.raw_title || result.task_id,
        result,
        metadata: {
            display_title: result.display_title || result.raw_title || result.filename || result.task_id,
            stt_provider: result.stt_provider || null,
        },
    };
};

const jobFromHistoryEntry = (entry) => {
    const result = historyEntryToResult(entry);
    if (!result?.task_id) return null;
    return {
        ...jobFromResult(result),
        task_snapshot: entry.taskSnapshot || null,
        status: entry.status || result.summary_status || 'completed',
        source_type: entry.source || result.source || null,
        updated_at: entry.timestamp ? new Date(entry.timestamp).toISOString() : undefined,
    };
};

const Processing = () => {
    const {lang} = useI18n();
    const {currentJob, lastResult, history} = useApp();
    const isZh = lang === 'zh';
    const recentTaskId = recentTaskIdFromHistory(history);
    const targetTaskId = currentJob?.taskId || lastResult?.task_id || recentTaskId;
    // Resolve the target job from AppProvider's derived history (the single task
    // list) instead of reading the account cache directly. See
    // docs/task_list_reconciliation_plan.md.
    const targetHistoryEntry = Array.isArray(history) ? history.find((item) => item?.taskId === targetTaskId) : null;
    const targetJob = useMemo(() => {
        if (!targetTaskId) return null;
        if (targetHistoryEntry) return jobFromHistoryEntry(targetHistoryEntry);
        if (lastResult?.task_id === targetTaskId) return jobFromResult(lastResult);
        if (currentJob?.taskId === targetTaskId) {
            return {
                task_id: currentJob.taskId,
                status: currentJob.taskState || (currentJob.stage === 'done' ? 'completed' : 'running'),
                stage: currentJob.stage || 'queued',
                progress: currentJob.progress ?? 0,
                task_snapshot: currentJob.taskSnapshot || null,
                source_type: currentJob.sourceType || null,
                source_filename: currentJob.fileName || currentJob.taskId,
                metadata: {
                    display_title: currentJob.fileName || currentJob.taskId,
                    stt_provider: currentJob.sttProvider || null,
                    stt_progress: currentJob.sttProgress,
                    transcribed_seconds: currentJob.transcribedSeconds,
                    duration_seconds: currentJob.durationSeconds,
                    stt_elapsed_seconds: currentJob.sttElapsedSeconds,
                    stt_status: currentJob.sttStatus,
                },
            };
        }
        return null;
    }, [targetHistoryEntry, currentJob, lastResult, targetTaskId]);

    if (targetTaskId) {
        return (
            <Navigate
                to={`/tasks/${encodeURIComponent(targetTaskId)}/agent`}
                replace
                state={targetJob ? {job: targetJob} : undefined}
            />
        );
    }

    return (
        <main className="ml-[var(--sidebar-offset)] min-h-dvh bg-[#f7f5f2] px-5 py-6 text-[#111111] transition-[margin] duration-200 dark:bg-[#0f1012] dark:text-white md:px-8">
            <div className="mx-auto flex min-h-[calc(100dvh-48px)] max-w-4xl items-center justify-center">
                <section className="w-full rounded-[24px] border border-[#dedada] bg-white p-6 shadow-[0_18px_55px_rgba(17,17,17,0.06)] dark:border-white/[0.12] dark:bg-white/[0.06] md:p-8">
                    <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-[16px] bg-[#111111] text-white dark:bg-white dark:text-[#111111]">
                        <Workflow size={24} strokeWidth={2.4}/>
                    </div>
                    <p className="text-[12px] font-extrabold uppercase tracking-[0.16em] text-[#85868c] dark:text-white/45">
                        {isZh ? '处理记录' : 'Processing records'}
                    </p>
                    <h1 className="mt-3 max-w-2xl font-headline text-[24px] font-extrabold leading-tight tracking-normal text-[#111111] dark:text-white md:text-[28px]">
                        {isZh ? '选择一个任务查看处理详情' : 'Choose a task to view its workflow'}
                    </h1>
                    <p className="mt-3 max-w-2xl text-[15px] font-semibold leading-7 text-[#6f7177] dark:text-white/60">
                        {isZh
                            ? '处理记录现在按具体任务组织。提交链接或上传素材后，这里会直接打开当前任务的进度、判断依据、失败原因和下一步操作。'
                            : 'Processing records are organized by task. After submitting a link or uploading media, this entry opens the current task progress, decisions, failure details, and next action.'}
                    </p>
                    <div className="mt-7 flex flex-wrap gap-3">
                        <Link
                            to="/media-text?mode=media"
                            className="inline-flex h-12 items-center gap-2 rounded-[14px] bg-[#111111] px-5 text-[14px] font-extrabold text-white transition hover:-translate-y-0.5 hover:shadow-[0_12px_30px_rgba(17,17,17,0.16)] dark:bg-white dark:text-[#111111]"
                        >
                            <Plus size={18} strokeWidth={2.4}/>
                            {isZh ? '开始处理' : 'Start'}
                        </Link>
                        <Link
                            to="/agent"
                            className="inline-flex h-12 items-center gap-2 rounded-[14px] border border-[#dedada] bg-white px-5 text-[14px] font-extrabold text-[#111111] transition hover:border-[#111111] dark:border-white/[0.14] dark:bg-white/[0.06] dark:text-white dark:hover:border-white/45"
                        >
                            <ListChecks size={18} strokeWidth={2.4}/>
                            {isZh ? '处理记录' : 'Processing records'}
                            <ArrowRight size={16} strokeWidth={2.4}/>
                        </Link>
                    </div>
                </section>
            </div>
        </main>
    );
};

export default Processing;
