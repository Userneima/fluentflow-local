import {useEffect, useState} from 'react';
import {AlertCircle, Check, CheckCircle2, LoaderCircle, Play, Undo2} from 'lucide-react';
import {API_BASE, apiFetch} from '../app/shared.jsx';

// The cuts FluentFlow asks the user to confirm after a task (survey_cut on the
// backend): every stretch with talk in it that the model removed, every single
// cut longer than a minute, gaps in which words were transcribed. Times are the
// original recording's. Answers are recorded to check the model's judgement;
// "这段不该剪" puts the stretch back and renders the cut again.

const RESTORE_POLL_MS = 3000;

export const clock = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
};

const length = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const m = Math.floor(total / 60);
    const s = total % 60;
    if (!m) return `剪掉 ${s} 秒`;
    return s ? `剪掉 ${m} 分 ${s} 秒` : `剪掉 ${m} 分钟`;
};

export const reviewOf = (job) => {
    const survey = job?.result?.debreath?.survey;
    return Array.isArray(survey?.review) ? survey : null;
};

const buttonClass = 'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-[10px] border border-[#dedada] bg-white px-3 text-[12px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]';

export const post = async (url, body) => {
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

const CutItem = ({taskId, index, item, state, busy, onAnswer, onRestore}) => {
    const [listening, setListening] = useState(false);
    const range = `原片 ${clock(item.start)} – ${clock(item.end)}`;
    if (state === 'right') {
        return (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[#dedada] bg-white/70 px-3 py-2 dark:border-white/[0.10] dark:bg-white/[0.03]">
                <p className="text-[12px] font-extrabold tabular-nums text-[#57585d] dark:text-white/62">{range} · {length(item.seconds)}</p>
                <span className="inline-flex items-center gap-1.5 text-[12px] font-extrabold text-emerald-700 dark:text-emerald-300">
                    <CheckCircle2 className="size-3.5" strokeWidth={2.15}/>
                    已确认剪得对
                    <button type="button" disabled={busy} className="ml-1 text-[12px] font-extrabold text-[#85868c] underline hover:no-underline dark:text-white/55" onClick={() => onAnswer(index, 'pending')}>撤回</button>
                </span>
            </div>
        );
    }
    return (
        <div className="rounded-[12px] border border-[#dedada] bg-white px-3 py-2.5 dark:border-white/[0.10] dark:bg-white/[0.04]">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-[13px] font-extrabold tabular-nums text-[#111111] dark:text-white">{range}</p>
                <p className="text-[12px] font-semibold tabular-nums text-[#85868c] dark:text-white/55">{length(item.seconds)}</p>
            </div>
            <p className="mt-1 text-[12px] font-semibold leading-5 text-[#57585d] dark:text-white/62">{item.reason}</p>
            {listening ? (
                <video
                    className="mt-2 max-h-[240px] w-full rounded-[10px] bg-black"
                    controls
                    autoPlay
                    preload="metadata"
                    src={`${API_BASE}/jobs/${encodeURIComponent(taskId)}/source#t=${Math.max(0, item.start - 3)},${item.end}`}
                />
            ) : null}
            <div className="mt-2 flex flex-wrap items-center gap-2">
                {state === 'restoring' ? (
                    <span className="inline-flex items-center gap-1.5 text-[12px] font-extrabold text-[#57585d] dark:text-white/62">
                        <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.15}/>
                        正在把这一段放回去，重新导出剪后视频…
                    </span>
                ) : state === 'restored' ? (
                    <span className="inline-flex items-center gap-1.5 text-[12px] font-extrabold text-emerald-700 dark:text-emerald-300">
                        <CheckCircle2 className="size-3.5" strokeWidth={2.15}/>
                        已放回去，剪后视频已更新，笔记正在按新版本重写
                    </span>
                ) : (
                    <>
                        <button type="button" className={buttonClass} onClick={() => setListening((on) => !on)}>
                            <Play className="size-3.5" strokeWidth={2.15}/>
                            {listening ? '收起' : '在原片里听这段'}
                        </button>
                        <button type="button" disabled={busy} className={buttonClass} onClick={() => onAnswer(index, 'right')}>
                            <Check className="size-3.5" strokeWidth={2.15}/>
                            剪得对
                        </button>
                        <button type="button" disabled={busy} className={buttonClass} onClick={() => onRestore(index)}>
                            <Undo2 className="size-3.5" strokeWidth={2.15}/>
                            这段不该剪
                        </button>
                    </>
                )}
            </div>
        </div>
    );
};

const stateOf = (survey, index) => {
    const item = survey.review[index];
    if (item?.restored) return 'restored';
    if (survey.restoring === index) return 'restoring';
    return survey.answers?.[String(index)] === 'right' ? 'right' : 'pending';
};

export const CutReview = ({job}) => {
    const taskId = job?.task_id || job?.id;
    const [survey, setSurvey] = useState(() => reviewOf(job));
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const incoming = reviewOf(job);

    useEffect(() => {
        if (incoming) setSurvey(incoming);
    }, [JSON.stringify(incoming)]); // eslint-disable-line react-hooks/exhaustive-deps

    const restoring = survey && Number.isInteger(survey.restoring);
    useEffect(() => {
        if (!restoring || !taskId) return undefined;
        const timer = window.setInterval(async () => {
            try {
                const response = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}`);
                if (!response.ok) return;
                const latest = reviewOf(await response.json());
                if (latest) setSurvey(latest);
                if (latest?.restore_error) setError(latest.restore_error);
            } catch (_) { /* next tick */ }
        }, RESTORE_POLL_MS);
        return () => window.clearInterval(timer);
    }, [restoring, taskId]);

    if (!survey || !survey.review.length || !taskId) return null;

    const answer = async (index, value) => {
        setBusy(true);
        setError('');
        try {
            const data = await post(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/cut-review/${index}`, {answer: value});
            setSurvey(data.survey);
        } catch (exc) {
            setError(exc.message);
        } finally {
            setBusy(false);
        }
    };
    const restore = async (index) => {
        setBusy(true);
        setError('');
        try {
            await post(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/cut-review/${index}/restore`);
            setSurvey((current) => ({...current, restoring: index}));
        } catch (exc) {
            setError(exc.message);
        } finally {
            setBusy(false);
        }
    };

    const states = survey.review.map((_, index) => stateOf(survey, index));
    const open = states.filter((state) => state === 'pending' || state === 'restoring').length;
    const allDone = open === 0;
    return (
        <div className={`mt-3 rounded-[14px] border px-3 py-3 ${allDone
            ? 'border-[#dedada] bg-[#fbfbfb] dark:border-white/[0.10] dark:bg-white/[0.04]'
            : 'border-[#f5c86b] bg-[#fffaf0] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10]'}`}>
            <div className={`flex items-start gap-2 ${allDone ? 'text-[#57585d] dark:text-white/62' : 'text-[#8a5a00] dark:text-[#fdb022]'}`}>
                {allDone ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/> : <AlertCircle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>}
                <div className="min-w-0">
                    <p className="text-[13px] font-extrabold">{allDone ? `${survey.review.length} 处剪辑都已确认` : `有 ${open} 处剪得比较多，请确认`}</p>
                    {!allDone ? <p className="mt-0.5 text-[12px] font-semibold leading-5">时间是原片里的时间，剪后视频里已经没有这些段了。</p> : null}
                </div>
            </div>
            <div className="mt-2.5 grid gap-2">
                {survey.review.map((item, index) => (
                    <CutItem
                        key={`${item.start}-${item.end}`}
                        taskId={taskId}
                        index={index}
                        item={item}
                        state={states[index]}
                        busy={busy || restoring}
                        onAnswer={answer}
                        onRestore={restore}
                    />
                ))}
            </div>
            {error ? <p className="mt-2 text-[12px] font-semibold text-red-700 dark:text-red-300">{error}</p> : null}
        </div>
    );
};
