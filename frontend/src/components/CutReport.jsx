import {useState} from 'react';
import {ChevronDown, ChevronRight, FileSearch, LoaderCircle, MessageSquare, Play} from 'lucide-react';
import {API_BASE, apiFetch} from '../app/shared.jsx';
import {clock, post} from './CutReview.jsx';

// What the cut did to this recording, organised for reading (cut_report on the
// backend): groups, what is open and what is folded, one sentence per item.
// Times are the original recording's and always the program's. Each item can
// be heard in the original and commented on; so can the whole.

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

const Feedback = ({taskId, refId, placeholder, onSent}) => {
    const [text, setText] = useState('');
    const [sending, setSending] = useState(false);
    const [error, setError] = useState('');
    const send = async () => {
        setSending(true);
        setError('');
        try {
            const data = await post(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/cut-report/feedback`, {text, ref: refId || null});
            setText('');
            onSent(data.feedback || []);
        } catch (exc) {
            setError(exc.message);
        } finally {
            setSending(false);
        }
    };
    return (
        <div className="mt-2 grid gap-1.5">
            <textarea
                className="min-h-[60px] w-full rounded-[10px] border border-[#dedada] bg-white px-2.5 py-2 text-[13px] text-[#111111] outline-none focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-white"
                value={text}
                placeholder={placeholder}
                onChange={(event) => setText(event.target.value)}
            />
            <div className="flex items-center gap-2">
                <button type="button" className={smallButton} disabled={sending || !text.trim()} onClick={send}>
                    {sending ? <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.15}/> : null}
                    发送
                </button>
                {error ? <span className="text-[12px] font-semibold text-red-700 dark:text-red-300">{error}</span> : null}
            </div>
        </div>
    );
};

const Item = ({taskId, item, feedback, onFeedback}) => {
    const [listening, setListening] = useState(false);
    const [commenting, setCommenting] = useState(false);
    const removed = item.action === 'removed';
    const timed = item.start !== undefined && item.end !== undefined;
    const mine = feedback.filter((entry) => entry.ref === item.ref);
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
            <div className="mt-2 flex flex-wrap items-center gap-2">
                {timed ? (
                    <button type="button" className={smallButton} onClick={() => setListening((on) => !on)}>
                        <Play className="size-3" strokeWidth={2.15}/>
                        {listening ? '收起' : '在原片里听'}
                    </button>
                ) : null}
                <button type="button" className={smallButton} onClick={() => setCommenting((on) => !on)}>
                    <MessageSquare className="size-3" strokeWidth={2.15}/>
                    有意见
                </button>
                {mine.length ? <span className="text-[12px] font-bold text-emerald-700 dark:text-emerald-300">已记下你的意见</span> : null}
            </div>
            {commenting ? (
                <Feedback
                    taskId={taskId}
                    refId={item.ref}
                    placeholder="这一处哪里不对？比如这段其实在讲重点、不该剪"
                    onSent={(entries) => { onFeedback(entries); setCommenting(false); }}
                />
            ) : null}
        </li>
    );
};

const Section = ({taskId, section, feedback, onFeedback}) => {
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
                        <Item key={item.ref} taskId={taskId} item={item} feedback={feedback} onFeedback={onFeedback}/>
                    ))}
                </ul>
            ) : null}
        </section>
    );
};

export const CutReport = ({job}) => {
    const taskId = job?.task_id || job?.id;
    const [open, setOpen] = useState(false);
    const [survey, setSurvey] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    if (!taskId || !hasReport(job)) return null;

    const toggle = async () => {
        if (open) { setOpen(false); return; }
        setOpen(true);
        setLoading(true);
        setError('');
        try {
            const response = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}`);
            if (!response.ok) throw new Error(`读取失败（${response.status}）`);
            const data = await response.json();
            setSurvey(data?.result?.debreath?.survey || null);
        } catch (exc) {
            setError(exc.message);
        } finally {
            setLoading(false);
        }
    };

    const report = survey?.report;
    const feedback = Array.isArray(survey?.feedback) ? survey.feedback : [];
    const setFeedback = (entries) => setSurvey((current) => ({...current, feedback: entries}));
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
                    ) : error ? (
                        <p className="text-[12px] font-semibold text-red-700 dark:text-red-300">{error}</p>
                    ) : report ? (
                        <>
                            <p className="text-[13px] font-extrabold tabular-nums text-[#111111] dark:text-white">
                                原片 {clock(report.before_seconds)}，剪后 {clock(report.after_seconds)}
                            </p>
                            {report.summary ? <p className="mt-1 text-[13px] font-semibold leading-5 text-[#57585d] dark:text-white/70">{report.summary}</p> : null}
                            <div className="mt-3 grid gap-3">
                                {report.sections.map((section) => (
                                    <Section key={section.title} taskId={taskId} section={section} feedback={feedback} onFeedback={setFeedback}/>
                                ))}
                            </div>
                            <div className="mt-4 border-t border-[#ebe8e8] pt-3 dark:border-white/[0.08]">
                                <p className="text-[13px] font-extrabold text-[#111111] dark:text-white">对这次处理整体有什么意见？</p>
                                {feedback.some((entry) => !entry.ref) ? <p className="mt-0.5 text-[12px] font-bold text-emerald-700 dark:text-emerald-300">已记下你的意见</p> : null}
                                <Feedback taskId={taskId} refId={null} placeholder="比如整体剪得太狠、某类内容不该去掉" onSent={setFeedback}/>
                            </div>
                        </>
                    ) : (
                        <p className="text-[12px] font-semibold text-[#57585d] dark:text-white/62">这条视频没有处理说明。</p>
                    )}
                </div>
            ) : null}
        </div>
    );
};
