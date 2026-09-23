import {useCallback, useEffect, useRef, useState} from 'react';
import {AlertCircle, CheckCircle2, LoaderCircle, RotateCcw} from 'lucide-react';
import {friendlyTaskError, useApi, useI18n} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {markBackendJob} from '../lib/taskState.js';

// How often an open window asks whether the service restarted under it. The
// usual restart happens while the page stays open, so asking only on load
// would miss exactly the case this dialog exists for.
const CHECK_INTERVAL_MS = 20000;

const copy = (lang) => (lang === 'zh' ? {
    title: (count) => `FluentFlow 服务重启过，${count} 个任务被中断了`,
    body: '这些任务已标记为失败。可以现在全部重新处理，也可以稍后在「处理记录」里逐个重试。',
    queued: '还在排队',
    started: '处理到一半',
    gone: '原文件不在了，需要重新提交',
    goneCopy: 'FluentFlow 保存的副本已不在，需要重新选择原文件提交',
    wasAt: '原位置：',
    link: '原链接：',
    retried: '已重新开始处理',
    retryAll: (count) => `全部重新处理（${count}）`,
    later: '稍后再说',
    done: '知道了',
    elsewhere: '这些任务已在另一个窗口处理过。',
    ackFailed: '没能记下已读，下次打开 FluentFlow 还会提醒。',
} : {
    title: (count) => `FluentFlow restarted and interrupted ${count} ${count === 1 ? 'task' : 'tasks'}`,
    body: 'They are marked failed. Re-run them all now, or retry them one by one later from Processing records.',
    queued: 'Was still queued',
    started: 'Was halfway through',
    gone: 'The original file is gone; submit it again',
    goneCopy: 'FluentFlow\'s stored copy is gone; choose the original file and submit again',
    wasAt: 'It was at: ',
    link: 'Link: ',
    retried: 'Started again',
    retryAll: (count) => `Re-run all (${count})`,
    later: 'Later',
    done: 'Got it',
    elsewhere: 'These were already handled in another window.',
    ackFailed: 'Could not record this as seen; it will show again next time FluentFlow opens.',
});

const InterruptedTaskRow = ({task, text, outcome}) => {
    const location = task.original_path || task.source_link || '';
    return (
        <li className="rounded-[14px] border border-[#e6e3e3] bg-[#faf9f9] px-3 py-2.5 dark:border-white/[0.10] dark:bg-white/[0.04]">
            <div className="flex items-start justify-between gap-3">
                <p className="min-w-0 break-words text-[13px] font-extrabold text-[#111111] dark:text-white">{task.title}</p>
                <span className="shrink-0 rounded-full bg-[#efeeee] px-2 py-0.5 text-[11px] font-bold text-[#676970] dark:bg-white/[0.08] dark:text-white/60">
                    {task.started ? text.started : text.queued}
                </span>
            </div>
            {!task.retryable ? (
                <p className="mt-1 text-[12px] font-semibold leading-5 text-[#9a5b12] dark:text-amber-200">
                    {location ? text.gone : text.goneCopy}
                    {location ? (
                        <span className="mt-0.5 block break-all font-medium text-[#676970] dark:text-white/55">
                            {task.original_path ? text.wasAt : text.link}{location}
                        </span>
                    ) : null}
                </p>
            ) : null}
            {outcome?.ok ? (
                <p className="mt-1 inline-flex items-center gap-1.5 text-[12px] font-semibold text-emerald-700 dark:text-emerald-300">
                    <CheckCircle2 className="size-3.5" strokeWidth={2.15}/>{text.retried}
                </p>
            ) : null}
            {outcome && !outcome.ok ? (
                <p data-testid="interrupted-retry-error" className="mt-1 flex items-start gap-1.5 text-[12px] font-semibold leading-5 text-red-700 dark:text-red-200">
                    <AlertCircle className="mt-0.5 size-3.5 shrink-0" strokeWidth={2.15}/>
                    <span className="min-w-0 break-words">{outcome.message}</span>
                </p>
            ) : null}
        </li>
    );
};

// Tells the user, on whichever page is open, that a service restart cut tasks
// off, and lets them decide what to do. Whether they have been told is stored
// in the backend, so the notice shows once per interruption on this machine,
// not once per browser or tab.
const InterruptedTasksDialog = () => {
    const {lang} = useI18n();
    const {ingestJobs} = useApp();
    const {getInterruptedJobs, acknowledgeInterruptedJobs, retryJob} = useApi();
    const text = copy(lang);
    const [tasks, setTasks] = useState([]);
    const [busy, setBusy] = useState(false);
    const [outcomes, setOutcomes] = useState({});
    const [finished, setFinished] = useState(false);
    const [notice, setNotice] = useState('');
    const openRef = useRef(false);
    // Ids this window has already shown and closed. If the acknowledgement did
    // not reach the backend, the notice waits for the next launch instead of
    // reopening every CHECK_INTERVAL_MS.
    const closedIdsRef = useRef(new Set());
    const apiRef = useRef({getInterruptedJobs});
    apiRef.current = {getInterruptedJobs};

    const check = useCallback(async () => {
        if (openRef.current) return;
        try {
            const listed = await apiRef.current.getInterruptedJobs();
            const found = (Array.isArray(listed) ? listed : [])
                .filter((task) => !closedIdsRef.current.has(String(task?.task_id)));
            if (openRef.current || !found.length) return;
            openRef.current = true;
            setTasks(found);
            setOutcomes({});
            setFinished(false);
            setNotice('');
        } catch (_) {
            // The service may be the thing restarting; the next check asks again.
        }
    }, []);

    useEffect(() => {
        check();
        const timer = setInterval(check, CHECK_INTERVAL_MS);
        const onVisible = () => { if (document.visibilityState === 'visible') check(); };
        window.addEventListener('focus', check);
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            clearInterval(timer);
            window.removeEventListener('focus', check);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [check]);

    const close = () => {
        tasks.forEach((task) => closedIdsRef.current.add(String(task.task_id)));
        openRef.current = false;
        setTasks([]);
        setOutcomes({});
        setFinished(false);
        setNotice('');
    };

    const acknowledge = async () => {
        try {
            const response = await acknowledgeInterruptedJobs(tasks.map((task) => task.task_id));
            return Array.isArray(response?.acknowledged) ? response.acknowledged : [];
        } catch (_) {
            setNotice(text.ackFailed);
            return null;
        }
    };

    const dismiss = async () => {
        setBusy(true);
        const acknowledged = await acknowledge();
        setBusy(false);
        if (acknowledged !== null) close();
        else setFinished(true);
    };

    // Acknowledge first, then re-run only what that call actually marked. The
    // acknowledgement is the claim: two open windows both pressing the button
    // must not start every task twice.
    const retryAll = async () => {
        setBusy(true);
        const acknowledged = await acknowledge();
        if (acknowledged === null) {
            setBusy(false);
            setFinished(true);
            return;
        }
        const claimed = new Set(acknowledged.map(String));
        const toRetry = tasks.filter((task) => task.retryable && claimed.has(String(task.task_id)));
        if (!toRetry.length && tasks.some((task) => task.retryable)) {
            setNotice(text.elsewhere);
        }
        const next = {};
        for (const task of toRetry) {
            try {
                const response = await retryJob(task.task_id, {sttProvider: 'local'});
                if (response?.job) ingestJobs([markBackendJob(response.job)]);
                next[task.task_id] = {ok: true};
            } catch (err) {
                next[task.task_id] = {ok: false, message: friendlyTaskError(err?.message || String(err), lang)};
            }
            setOutcomes({...next});
        }
        setBusy(false);
        const needsReading = Object.values(next).some((outcome) => !outcome.ok)
            || tasks.some((task) => !task.retryable)
            || !toRetry.length;
        if (needsReading) setFinished(true);
        else close();
    };

    if (!tasks.length) return null;
    const retryableCount = tasks.filter((task) => task.retryable).length;

    return (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 px-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="interruptedTasksTitle">
            <div className="flex max-h-[85dvh] w-full max-w-[520px] flex-col rounded-[22px] border border-[#dedada] bg-white p-5 shadow-[0_28px_90px_-52px_rgba(17,17,17,.72)] dark:border-white/[0.12] dark:bg-[#151515]">
                <div className="flex items-start gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[14px] bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-200">
                        <RotateCcw className="size-5" strokeWidth={2.15}/>
                    </span>
                    <div className="min-w-0">
                        <h2 id="interruptedTasksTitle" className="text-base font-extrabold text-[#111111] dark:text-white">
                            {text.title(tasks.length)}
                        </h2>
                        <p className="mt-2 text-sm leading-relaxed text-on-surface-variant">{text.body}</p>
                    </div>
                </div>
                <ul className="mt-4 min-h-0 space-y-2 overflow-y-auto">
                    {tasks.map((task) => (
                        <InterruptedTaskRow key={task.task_id} task={task} text={text} outcome={outcomes[task.task_id]}/>
                    ))}
                </ul>
                {notice ? (
                    <p className="mt-3 text-[12px] font-semibold text-[#676970] dark:text-white/60">{notice}</p>
                ) : null}
                <div className="mt-5 flex justify-end gap-2">
                    {finished ? (
                        <button type="button" onClick={close} className="inline-flex h-10 items-center rounded-[12px] bg-[#111111] px-4 text-xs font-bold text-white transition hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                            {text.done}
                        </button>
                    ) : retryableCount ? (
                        <>
                            <button type="button" disabled={busy} onClick={dismiss} className="inline-flex h-10 items-center rounded-[12px] border border-[#dedada] px-4 text-xs font-bold hover:bg-[#efeeee] disabled:opacity-50 dark:border-white/[0.12] dark:hover:bg-white/[0.12]">
                                {text.later}
                            </button>
                            <button type="button" disabled={busy} onClick={retryAll} className="inline-flex h-10 items-center gap-2 rounded-[12px] bg-[#111111] px-4 text-xs font-bold text-white transition hover:bg-[#2a2a2a] disabled:opacity-60 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                                {busy ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : null}
                                {text.retryAll(retryableCount)}
                            </button>
                        </>
                    ) : (
                        <button type="button" disabled={busy} onClick={dismiss} className="inline-flex h-10 items-center rounded-[12px] bg-[#111111] px-4 text-xs font-bold text-white transition hover:bg-[#2a2a2a] disabled:opacity-60 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                            {text.done}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
};

export default InterruptedTasksDialog;
