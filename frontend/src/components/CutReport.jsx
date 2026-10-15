import {useEffect, useState} from 'react';
import {ChevronDown, ChevronRight, FileSearch, LoaderCircle, Play, Scissors, Undo2} from 'lucide-react';
import {API_BASE, apiFetch} from '../app/shared.jsx';
import {clock, post} from './CutReview.jsx';

// What the cut did to this recording, organised for reading (cut_report on the
// backend): groups, what is open and what is folded, one sentence per item.
// Times are the original recording's and always the program's. Each item can
// be heard in the original and turned around (cut becomes kept, kept becomes
// cut): the change is the feedback, recorded beside what the model decided.

const length = (seconds) => {
    const value = Number(seconds) || 0;
    if (value < 10) return `${value.toFixed(1)} 秒`;
    const total = Math.round(value);
    if (total < 60) return `${total} 秒`;
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s ? `${m} 分 ${s} 秒` : `${m} 分钟`;
};

export const hasReport = (job) => !!job?.result?.debreath?.survey?.has_report || !!job?.result?.debreath?.survey?.report;

const smallButton = 'inline-flex h-7 shrink-0 items-center gap-1 rounded-[9px] border border-[#dedada] bg-white px-2.5 text-[12px] font-bold text-[#57585d] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.10]';

const Item = ({item, taskId, busy, onFlip}) => {
    const [listening, setListening] = useState(false);
    const removed = item.action === 'removed';
    const timed = item.start !== undefined && item.end !== undefined;
    return (
        <li className="rounded-[12px] border border-[#ebe8e8] bg-white px-3 py-2.5 dark:border-white/[0.08] dark:bg-white/[0.03]">
            <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-extrabold ${removed
                    ? 'bg-[#fdecec] text-[#b42318] dark:bg-red-500/15 dark:text-red-300'
                    : 'bg-[#ecfdf3] text-[#067647] dark:bg-emerald-500/15 dark:text-emerald-300'}`}>
                    {removed ? '剪掉' : '保留'}
                </span>
                {timed ? (
                    <span className="text-[12px] font-extrabold tabular-nums text-[#111111] dark:text-white">
                        原片 {clock(item.start)} – {clock(item.end)}
                    </span>
                ) : null}
                <span className="text-[12px] font-semibold tabular-nums text-[#85868c] dark:text-white/55">{length(item.seconds)}</span>
            </div>
            <p className="mt-1 text-[13px] font-semibold leading-5 text-[#2b2c30] dark:text-white/80">{item.text}</p>
            {item.said ? <p className="mt-0.5 text-[12px] leading-5 text-[#85868c] dark:text-white/50">说的是：「{item.said}」</p> : null}
            {listening && timed ? (
                <video
                    className="mt-2 max-h-[220px] w-full rounded-[10px] bg-black"
                    controls
                    autoPlay
                    preload="metadata"
                    src={`${API_BASE}/jobs/${encodeURIComponent(taskId)}/source#t=${Math.max(0, item.start - 3)},${item.end + 1}`}
                />
            ) : null}
            {timed ? (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button type="button" className={smallButton} onClick={() => setListening((on) => !on)}>
                        <Play className="size-3" strokeWidth={2.15}/>
                        {listening ? '收起' : '在原片里听'}
                    </button>
                    <button type="button" className={smallButton} disabled={busy} onClick={() => onFlip(item.ref)}>
                        {removed ? <Undo2 className="size-3" strokeWidth={2.15}/> : <Scissors className="size-3" strokeWidth={2.15}/>}
                        {removed ? '这段不该剪' : '这段应该剪掉'}
                    </button>
                </div>
            ) : null}
        </li>
    );
};

const Section = ({taskId, section, busy, onFlip}) => {
    const [open, setOpen] = useState(!section.folded);
    const Chevron = open ? ChevronDown : ChevronRight;
    return (
        <section>
            <button
                type="button"
                className="flex w-full items-center gap-1.5 text-left text-[13px] font-extrabold text-[#111111] dark:text-white"
                onClick={() => setOpen((value) => !value)}
            >
                <Chevron className="size-4 shrink-0 text-[#85868c]" strokeWidth={2.15}/>
                {section.title}
                <span className="text-[12px] font-semibold text-[#85868c] dark:text-white/55">{section.items.length} 条</span>
            </button>
            {open ? (
                <ul className="mt-2 grid gap-2">
                    {section.items.map((item) => (
                        <Item key={item.ref} taskId={taskId} item={item} busy={busy} onFlip={onFlip}/>
                    ))}
                </ul>
            ) : null}
        </section>
    );
};

const REFRESH_MS = 3000;

export const CutReport = ({job}) => {
    const taskId = job?.task_id || job?.id;
    const [open, setOpen] = useState(false);
    const [survey, setSurvey] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const rerendering = !!survey?.rerendering || Number.isInteger(survey?.restoring);

    const load = async () => {
        const response = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}`);
        if (!response.ok) throw new Error(`读取失败（${response.status}）`);
        const data = await response.json();
        const latest = data?.result?.debreath?.survey || null;
        setSurvey(latest);
        if (latest?.restore_error) setError(latest.restore_error);
        return latest;
    };

    useEffect(() => {
        if (!open || !rerendering || !taskId) return undefined;
        const timer = window.setInterval(() => { load().catch(() => {}); }, REFRESH_MS);
        return () => window.clearInterval(timer);
    }, [open, rerendering, taskId]); // eslint-disable-line react-hooks/exhaustive-deps

    if (!taskId || !hasReport(job)) return null;

    const toggle = async () => {
        if (open) { setOpen(false); return; }
        setOpen(true);
        setLoading(true);
        setError('');
        try {
            await load();
        } catch (exc) {
            setError(exc.message);
        } finally {
            setLoading(false);
        }
    };

    const flip = async (ref) => {
        setError('');
        try {
            await post(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/cut-report/${encodeURIComponent(ref)}/flip`);
            setSurvey((current) => ({...current, rerendering: true}));
        } catch (exc) {
            setError(exc.message);
        }
    };

    const report = survey?.report;
    return (
        <div className="mt-3">
            <button type="button" className={smallButton} onClick={toggle}>
                <FileSearch className="size-3.5" strokeWidth={2.15}/>
                {open ? '收起处理说明' : '看看这条视频处理了什么'}
            </button>
            {open ? (
                <div className="mt-2 rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-3 py-3 dark:border-white/[0.10] dark:bg-white/[0.04]">
                    {loading ? (
                        <p className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-[#57585d] dark:text-white/62">
                            <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.15}/>
                            正在读取…
                        </p>
                    ) : report ? (
                        <>
                            <p className="text-[13px] font-extrabold tabular-nums text-[#111111] dark:text-white">
                                原片 {clock(report.before_seconds)}，剪后 {clock(report.after_seconds)}
                            </p>
                            {report.summary ? <p className="mt-1 text-[13px] font-semibold leading-5 text-[#57585d] dark:text-white/70">{report.summary}</p> : null}
                            {rerendering ? (
                                <p className="mt-2 inline-flex items-center gap-1.5 text-[12px] font-extrabold text-[#57585d] dark:text-white/62">
                                    <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.15}/>
                                    按你的改动重新导出剪后视频，导完这份说明会更新，笔记也会重写…
                                </p>
                            ) : null}
                            {error ? <p className="mt-2 text-[12px] font-semibold text-red-700 dark:text-red-300">{error}</p> : null}
                            <div className="mt-3 grid gap-3">
                                {report.sections.map((section) => (
                                    <Section key={`${section.title}-${section.items.length}`} taskId={taskId} section={section} busy={rerendering} onFlip={flip}/>
                                ))}
                            </div>
                        </>
                    ) : error ? (
                        <p className="text-[12px] font-semibold text-red-700 dark:text-red-300">{error}</p>
                    ) : (
                        <p className="text-[12px] font-semibold text-[#57585d] dark:text-white/62">这条视频没有处理说明。</p>
                    )}
                </div>
            ) : null}
        </div>
    );
};
