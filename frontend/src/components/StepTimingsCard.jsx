import {fmtDuration} from '../lib/format.js';
import {surfaceCard} from '../lib/ui.js';

// How long each step of this task took (task_steps.timings on the server):
// the whole run first, then one row per step that ran, the queue included.
// The cut is broken into its parts; a note the user's agent wrote says so.

const LABELS_EN = {
    queue: 'Queue',
    fetch: 'Fetch',
    transcribe: 'Transcribe',
    correct: 'Correct transcript',
    cut: 'Cut',
    note: 'Write note',
    compress: 'Compressed copy',
    feishu: 'Send to Feishu',
};

const PART_LABELS_EN = {
    判断剪哪里: 'Decide what to cut',
    生成剪后视频: 'Render the cut video',
    整理处理报告: 'Write the cut report',
};

const durationText = (seconds, zh) => {
    if (seconds === null || seconds === undefined) return '';
    if (seconds < 1) return zh ? '不到一秒' : 'under a second';
    return fmtDuration(seconds, zh ? 'zh' : 'en');
};

const statusTag = (row, zh) => {
    if (row.running) {
        return {text: zh ? '进行中' : 'Running', className: 'text-[#57585d] dark:text-white/[0.75]'};
    }
    if (row.status === 'failed') return {text: zh ? '失败' : 'Failed', className: 'text-[#b42318] dark:text-[#f97066]'};
    if (row.status === 'stopped') return {text: zh ? '已停下' : 'Stopped', className: 'text-[#8a5a00] dark:text-[#fdb022]'};
    return null;
};

const TimingRow = ({row, zh}) => {
    const tag = statusTag(row, zh);
    const label = zh ? row.label : (LABELS_EN[row.step] || row.label);
    return (
        <li data-testid="timing-step" className="py-2.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-0.5">
                <p className="text-[14px] font-extrabold text-[#111111] dark:text-white">
                    {label}
                    {row.by_agent && (
                        <span className="ml-2 text-[12px] font-semibold text-[#85868c] dark:text-white/[0.65]">
                            {zh ? '你的 agent' : 'your agent'}
                        </span>
                    )}
                </p>
                <p className="text-[13px] font-semibold text-[#57585d] dark:text-white/[0.75]">
                    {tag && <span className={`mr-2 font-extrabold ${tag.className}`}>{tag.text}</span>}
                    {row.running && (zh ? '已用 ' : '')}{durationText(row.seconds, zh)}
                </p>
            </div>
            {(row.parts || []).length > 0 && (
                <ul className="mt-1 space-y-0.5 pl-4">
                    {row.parts.map((part) => (
                        <li key={part.label} data-testid="timing-part" className="flex flex-wrap justify-between gap-x-6 text-[12px] font-semibold text-[#85868c] dark:text-white/[0.65]">
                            <span>{zh ? part.label : (PART_LABELS_EN[part.label] || part.label)}</span>
                            <span>{durationText(part.seconds, zh)}</span>
                        </li>
                    ))}
                </ul>
            )}
        </li>
    );
};

const StepTimingsCard = ({timings, lang}) => {
    const rows = timings?.rows || [];
    if (!rows.length) return null;
    const zh = lang === 'zh';
    const total = durationText(timings.total_seconds, zh);
    return (
        <section data-testid="step-timings" className={`${surfaceCard} p-5`}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <h2 className="font-headline text-[15px] font-extrabold text-[#111111] dark:text-white">
                    {zh ? '各环节用时' : 'Time per step'}
                </h2>
                {total && (
                    <p data-testid="timings-total" className="text-[15px] font-extrabold text-[#111111] dark:text-white">
                        {timings.running ? (zh ? `已用 ${total}` : `${total} so far`) : (zh ? `全程 ${total}` : `${total} in all`)}
                    </p>
                )}
            </div>
            {timings.overlapping && (
                <p className="mt-1 text-[12px] font-semibold text-[#85868c] dark:text-white/[0.65]">
                    {zh ? '有的环节同时进行，各环节加起来比全程长。' : 'Some steps ran at the same time, so the steps add up to more than the whole run.'}
                </p>
            )}
            <ul className="mt-3 divide-y divide-[#ece8e8] border-t border-[#ece8e8] dark:divide-white/[0.08] dark:border-white/[0.08]">
                {rows.map((row) => <TimingRow key={row.step} row={row} zh={zh}/>)}
            </ul>
        </section>
    );
};

export default StepTimingsCard;
