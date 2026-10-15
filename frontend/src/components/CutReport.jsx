import {useEffect, useState} from 'react';
import {AlertCircle, Check, CheckCircle2, ChevronDown, ChevronRight, FileSearch, LoaderCircle, Play, RotateCcw, Scissors, Undo2} from 'lucide-react';
import {API_BASE, apiFetch} from '../app/shared.jsx';

// What the cut did to this recording, organised for reading (cut_report on the
// backend): groups, what is open and what is folded, one sentence per item.
// Times are the original recording's and always the program's.
//
// The user is asked only about the first group, 需要你判断 (owner, 2026-10-15):
// what Claude was unsure of, with why, and what the rules always ask. There
// "剪得对" / "确实该保留" is recorded at once and the item leaves the group for a
// folded 你已确认的, where it can be taken back; "这段不该剪" / "这段应该剪掉"
// change the video, so they ask first. Every other item is Claude's account,
// with a quiet way to change it. Every change lands in the bin at the bottom.

export const clock = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
};

const post = async (url, body) => {
    const response = await apiFetch(url, {
        method: 'POST',
        headers: body ? {'Content-Type': 'application/json'} : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
        let detail = '';
        try { detail = (await response.json())?.detail || ''; } catch (_) { /* not json */ }
        throw new Error(detail || `请求失败（${response.status}）`);
    }
    return response.json();
};

const length = (seconds) => {
    const value = Number(seconds) || 0;
    if (value < 10) return `${value.toFixed(1)} 秒`;
    const total = Math.round(value);
    if (total < 60) return `${total} 秒`;
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s ? `${m} 分 ${s} 秒` : `${m} 分钟`;
};

const surveyOf = (job) => job?.result?.debreath?.survey;
export const hasReport = (job) => !!surveyOf(job)?.has_report || !!surveyOf(job)?.report;

const smallButton = 'inline-flex h-7 shrink-0 items-center gap-1 rounded-[9px] border border-[#dedada] bg-white px-2.5 text-[12px] font-bold text-[#57585d] transition hover:bg-[#efeeee] disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.10]';
const linkButton = 'text-[12px] font-extrabold text-[#85868c] underline hover:no-underline disabled:opacity-50 dark:text-white/55';
const okText = 'inline-flex items-center gap-1 text-[12px] font-extrabold text-emerald-700 dark:text-emerald-300';

const RERENDER_NOTE = '会按你的改动重新导出剪后视频、重写笔记，要等几分钟。';

const Confirm = ({label, onYes, onNo}) => (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-[10px] bg-[#fffaf0] px-2.5 py-2 text-[12px] font-semibold text-[#8a5a00] dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]">
        <span>{RERENDER_NOTE}</span>
        <button type="button" className={smallButton} onClick={onYes}>{label}</button>
        <button type="button" className={linkButton} onClick={onNo}>取消</button>
    </div>
);

const Badge = ({removed}) => (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-extrabold ${removed
        ? 'bg-[#fdecec] text-[#b42318] dark:bg-red-500/15 dark:text-red-300'
        : 'bg-[#ecfdf3] text-[#067647] dark:bg-emerald-500/15 dark:text-emerald-300'}`}>
        {removed ? '剪掉' : '保留'}
    </span>
);

// mode: 'judge' (asked: agree or change), 'agreed' (answered: take back),
// 'read' (Claude's account: listen, or quietly change it).
const Item = ({item, taskId, mode, busy, onAgree, onFlip}) => {
    const [listening, setListening] = useState(false);
    const [confirming, setConfirming] = useState(false);
    // What is put to the user starts as one line each; opening one shows its
    // details and buttons.
    const [expanded, setExpanded] = useState(mode !== 'judge');
    const removed = item.action === 'removed';
    const timed = item.start !== undefined && item.end !== undefined;
    const theirs = item.kind === 'user_change';
    const Chevron = expanded ? ChevronDown : ChevronRight;
    return (
        <li className="rounded-[12px] border border-[#ebe8e8] bg-white px-3 py-2.5 dark:border-white/[0.08] dark:bg-white/[0.03]">
            <div
                className={`flex flex-wrap items-center gap-2 ${mode === 'judge' ? 'cursor-pointer' : ''}`}
                onClick={mode === 'judge' ? () => setExpanded((value) => !value) : undefined}
            >
                {mode === 'judge' ? <Chevron className="size-3.5 shrink-0 text-[#85868c]" strokeWidth={2.15}/> : null}
                <Badge removed={removed}/>
                {timed ? (
                    <span className="text-[12px] font-extrabold tabular-nums text-[#111111] dark:text-white">
                        原片 {clock(item.start)} – {clock(item.end)}
                    </span>
                ) : null}
                <span className="text-[12px] font-semibold tabular-nums text-[#85868c] dark:text-white/55">
                    {item.count ? `${item.count} 处，共 ` : ''}{length(item.seconds)}
                </span>
            </div>
            <p className={`mt-1 text-[13px] font-semibold leading-5 text-[#2b2c30] dark:text-white/80 ${expanded ? '' : 'line-clamp-1'}`}>{item.text}</p>
            {!expanded ? null : <>
            {item.said ? <p className="mt-0.5 text-[12px] leading-5 text-[#85868c] dark:text-white/50">说的是：「{item.said}」</p> : null}
            {mode === 'judge' && item.doubt ? (
                <p className="mt-1 text-[12px] font-semibold leading-5 text-[#8a5a00] dark:text-[#fdb022]">请你判断：{item.doubt}</p>
            ) : null}
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
                    {mode === 'judge' ? (
                        <>
                            <button type="button" className={smallButton} disabled={busy} onClick={() => onAgree([item.key], 'agree')}>
                                <Check className="size-3" strokeWidth={2.15}/>
                                {removed ? '剪得对' : '确实该保留'}
                            </button>
                            <button type="button" className={smallButton} disabled={busy} onClick={() => setConfirming(true)}>
                                {removed ? <Undo2 className="size-3" strokeWidth={2.15}/> : <Scissors className="size-3" strokeWidth={2.15}/>}
                                {removed ? '这段不该剪' : '这段应该剪掉'}
                            </button>
                        </>
                    ) : mode === 'agreed' ? (
                        <span className={okText}>
                            <CheckCircle2 className="size-3.5" strokeWidth={2.15}/>
                            {removed ? '你确认了剪得对' : '你确认了该保留'}
                            <button type="button" className={`ml-1 ${linkButton}`} disabled={busy} onClick={() => onAgree([item.key], 'pending')}>撤回</button>
                        </span>
                    ) : theirs ? (
                        <span className="text-[12px] font-semibold text-[#85868c] dark:text-white/55">你改的，要改回去在下面"你改过的地方"里撤销</span>
                    ) : (
                        <button type="button" className={linkButton} disabled={busy} onClick={() => setConfirming(true)}>
                            {removed ? '改成保留' : '改成剪掉'}
                        </button>
                    )}
                </div>
            ) : null}
            {confirming ? (
                <Confirm
                    label={removed ? '确定放回去' : '确定剪掉'}
                    onYes={() => { setConfirming(false); onFlip(item.ref); }}
                    onNo={() => setConfirming(false)}
                />
            ) : null}
            </>}
        </li>
    );
};

const Section = ({taskId, title, items, folded, mode, busy, onAgree, onFlip, action}) => {
    const [open, setOpen] = useState(!folded);
    const Chevron = open ? ChevronDown : ChevronRight;
    return (
        <section>
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    className="flex items-center gap-1.5 text-left text-[13px] font-extrabold text-[#111111] dark:text-white"
                    onClick={() => setOpen((value) => !value)}
                >
                    <Chevron className="size-4 shrink-0 text-[#85868c]" strokeWidth={2.15}/>
                    {title}
                    <span className="text-[12px] font-semibold text-[#85868c] dark:text-white/55">{items.length} 条</span>
                </button>
                {action}
            </div>
            {open ? (
                <ul className="mt-2 grid gap-2">
                    {items.map((item) => (
                        <Item key={item.key || item.ref} taskId={taskId} item={item} mode={mode} busy={busy} onAgree={onAgree} onFlip={onFlip}/>
                    ))}
                </ul>
            ) : null}
        </section>
    );
};

const Change = ({change, index, busy, onUndo}) => {
    const [confirming, setConfirming] = useState(false);
    const said = (value) => (value === 'removed' ? '剪掉' : '保留');
    const undone = !!change.undone_at;
    return (
        <li className={`rounded-[12px] border border-[#ebe8e8] bg-white px-3 py-2 dark:border-white/[0.08] dark:bg-white/[0.03] ${undone ? 'opacity-55' : ''}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[12px] font-extrabold tabular-nums text-[#111111] dark:text-white">
                    原片 {clock(change.start)} – {clock(change.end)} · 原来{said(change.model_said)}，你改成{said(change.user_said)}
                </p>
                {undone ? (
                    <span className="text-[12px] font-bold text-[#85868c] dark:text-white/55">已撤销</span>
                ) : (
                    <button type="button" className={smallButton} disabled={busy} onClick={() => setConfirming(true)}>
                        <RotateCcw className="size-3" strokeWidth={2.15}/>
                        撤销这次改动
                    </button>
                )}
            </div>
            {change.model_reason ? <p className="mt-0.5 text-[12px] leading-5 text-[#85868c] dark:text-white/50">原来的理由：{change.model_reason}</p> : null}
            {confirming ? (
                <Confirm label="确定撤销" onYes={() => { setConfirming(false); onUndo(index); }} onNo={() => setConfirming(false)}/>
            ) : null}
        </li>
    );
};

const REFRESH_MS = 3000;

export const CutReport = ({job}) => {
    const taskId = job?.task_id || job?.id;
    const [open, setOpen] = useState(false);
    const [survey, setSurvey] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const rerendering = !!survey?.rerendering;

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

    const base = `${API_BASE}/jobs/${encodeURIComponent(taskId)}/cut-report`;
    const agree = async (keys, verdict) => {
        setSaving(true);
        setError('');
        try {
            const data = await post(`${base}/verdict`, {keys, verdict});
            setSurvey((current) => ({...current, verdicts: data.verdicts || {}}));
        } catch (exc) {
            setError(exc.message);
        } finally {
            setSaving(false);
        }
    };
    const rerender = async (url) => {
        setError('');
        try {
            await post(url);
            setSurvey((current) => ({...current, rerendering: true}));
        } catch (exc) {
            setError(exc.message);
        }
    };
    const flip = (ref) => rerender(`${base}/${encodeURIComponent(ref)}/flip`);

    const report = survey?.report;
    const verdicts = survey?.verdicts || {};
    const changes = Array.isArray(survey?.user_changes) ? survey.user_changes : [];
    const sections = report?.sections || [];
    const judgeItems = sections.filter((section) => section.judge).flatMap((section) => section.items);
    const waiting = judgeItems.filter((item) => !verdicts[item.key]);
    const answered = judgeItems.filter((item) => verdicts[item.key]);
    const busy = rerendering || saving;
    // Before the task is opened, the list row says how many wait.
    const toJudge = open && report ? waiting.length : Number(surveyOf(job)?.to_judge) || 0;
    return (
        <div className="mt-3">
            <button
                type="button"
                className={toJudge && !open
                    ? `${smallButton} border-[#f5c86b] bg-[#fffaf0] text-[#8a5a00] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]`
                    : smallButton}
                onClick={toggle}
            >
                {toJudge && !open ? <AlertCircle className="size-3.5" strokeWidth={2.15}/> : <FileSearch className="size-3.5" strokeWidth={2.15}/>}
                {open ? '收起处理说明' : toJudge ? `有 ${toJudge} 处需要你判断` : '看看这条视频处理了什么'}
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
                                {judgeItems.length ? (
                                    <div className={`rounded-[12px] border px-2.5 py-2.5 ${waiting.length
                                        ? 'border-[#f5c86b] bg-[#fffaf0] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.06]'
                                        : 'border-[#dedada] bg-white/60 dark:border-white/[0.10] dark:bg-white/[0.02]'}`}>
                                        {waiting.length ? (
                                            <Section
                                                taskId={taskId}
                                                title="需要你判断"
                                                items={waiting}
                                                folded={false}
                                                mode="judge"
                                                busy={busy}
                                                onAgree={agree}
                                                onFlip={flip}
                                                action={(
                                                    <button type="button" className={smallButton} disabled={busy} onClick={() => agree(waiting.map((item) => item.key), 'agree')}>
                                                        <Check className="size-3" strokeWidth={2.15}/>
                                                        剩下的都没问题
                                                    </button>
                                                )}
                                            />
                                        ) : (
                                            <p className={okText}>
                                                <CheckCircle2 className="size-3.5" strokeWidth={2.15}/>
                                                需要你判断的都处理完了
                                            </p>
                                        )}
                                        {answered.length ? (
                                            <div className="mt-2.5">
                                                <Section
                                                    key={`agreed-${answered.length}`}
                                                    taskId={taskId}
                                                    title="你已确认的"
                                                    items={answered}
                                                    folded
                                                    mode="agreed"
                                                    busy={busy}
                                                    onAgree={agree}
                                                    onFlip={flip}
                                                />
                                            </div>
                                        ) : null}
                                    </div>
                                ) : null}
                                {sections.filter((section) => !section.judge).map((section) => (
                                    <Section
                                        key={`${section.title}-${section.items.length}`}
                                        taskId={taskId}
                                        title={section.title}
                                        items={section.items}
                                        folded={section.folded}
                                        mode="read"
                                        busy={busy}
                                        onAgree={agree}
                                        onFlip={flip}
                                    />
                                ))}
                            </div>
                            {changes.length ? (
                                <div className="mt-4 border-t border-[#ebe8e8] pt-3 dark:border-white/[0.08]">
                                    <p className="text-[13px] font-extrabold text-[#111111] dark:text-white">你改过的地方</p>
                                    <p className="mt-0.5 text-[12px] font-semibold text-[#85868c] dark:text-white/55">每一处都可以撤销，撤销后按原来的判断重新导出。</p>
                                    <ul className="mt-2 grid gap-2">
                                        {changes.map((change, index) => (
                                            <Change
                                                key={`${change.start}-${change.at}`}
                                                change={change}
                                                index={index}
                                                busy={busy}
                                                onUndo={(i) => rerender(`${base}/changes/${i}/undo`)}
                                            />
                                        ))}
                                    </ul>
                                </div>
                            ) : null}
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
