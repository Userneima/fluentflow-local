// The records page's filter bar (filters, live counts, title search) and the
// list under it (loading, empty and no-match states, the cards).
import {Fragment} from 'react';
import {Link} from 'react-router-dom';
import {History, LoaderCircle, Plus, Search} from 'lucide-react';
import {button, chip} from '../lib/ui.js';
import AgentTaskCard from './agent-tasks-card.jsx';
import {taskIdForJob} from './agent-tasks-jobs.js';
import {RECORD_FILTERS} from './agent-tasks-list.js';

export const RecordsToolbar = ({lang, loading, filter, setFilter, filterCounts, runningCount, queuedCount, query, setQuery}) => (
    <section className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={lang === 'zh' ? '筛选' : 'Filter'}>
            {RECORD_FILTERS.map((item) => {
                const count = filterCounts[item.id];
                if (item.id !== 'all' && !count && filter !== item.id) return null;
                const active = filter === item.id;
                return (
                    <button
                        key={item.id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setFilter(item.id)}
                        className={chip(active)}
                    >
                        {lang === 'zh' ? item.zh : item.en}
                        {loading && item.id === 'all' ? null : <span className="tabular-nums opacity-70">{count}</span>}
                    </button>
                );
            })}
            {runningCount + queuedCount > 0 ? (
                <span data-testid="live-summary" className="ml-1 inline-flex items-center gap-1.5 text-[12px] font-extrabold text-blue-700 dark:text-blue-200">
                    <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.15}/>
                    {lang === 'zh'
                        ? [runningCount ? `${runningCount} 个在处理` : '', queuedCount ? `${queuedCount} 个排队` : ''].filter(Boolean).join(' · ')
                        : [runningCount ? `${runningCount} running` : '', queuedCount ? `${queuedCount} queued` : ''].filter(Boolean).join(' · ')}
                </span>
            ) : null}
        </div>
        <label className="relative block sm:w-64">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#9a9ba0]" strokeWidth={2.15}/>
            <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={lang === 'zh' ? '搜索标题' : 'Search titles'}
                className="h-8 w-full rounded-[10px] border border-[#dedada] bg-white pl-9 pr-3 text-[13px] font-semibold text-[#111111] outline-none placeholder:text-[#a2a3a8] focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:focus:border-white/40"
            />
        </label>
    </section>
);

export const RecordsList = ({lang, loading, displayJobs, visibleJobs, queueUploadJob, restStartTaskId, queueAheadName, retryError, highlightTaskId, busy, cardHandlers}) => (
    <section className="space-y-2">
        {loading && !displayJobs.length && !queueUploadJob && (
            <div className="flex h-48 items-center justify-center rounded-[16px] border border-[#e4e0e0] bg-white text-sm font-semibold text-[#676970] dark:border-white/[0.10] dark:bg-white/[0.055] dark:text-white/60">
                <LoaderCircle className="mr-2 size-4 animate-spin" strokeWidth={2.15}/>
                {lang === 'zh' ? '正在读取处理记录…' : 'Loading records…'}
            </div>
        )}
        {!loading && displayJobs.length === 0 && !queueUploadJob && (
            <div className="rounded-[16px] border border-[#e4e0e0] bg-white p-8 text-center dark:border-white/[0.10] dark:bg-white/[0.055]">
                <History className="mx-auto size-9 text-[#85868c] dark:text-white/45" strokeWidth={2.15}/>
                <h2 className="mt-3 font-headline text-[18px] font-extrabold text-[#111111] dark:text-white">
                    {lang === 'zh' ? '当前没有处理记录' : 'No processing records'}
                </h2>
                <p className="mx-auto mt-2 max-w-[54ch] text-[13px] font-semibold leading-5 text-[#676970] dark:text-white/60">
                    {lang === 'zh' ? '开始一个新任务后，处理进度会直接作为记录显示在这里。' : 'Start a task and its processing record will appear here.'}
                </p>
                <div className="mt-5 flex flex-wrap justify-center gap-2">
                    <Link to="/media-text?mode=media" className={button('primary', 'lg')}>
                        <Plus className="size-4" strokeWidth={2.15}/>
                        {lang === 'zh' ? '开始处理' : 'Start'}
                    </Link>
                </div>
            </div>
        )}
        {displayJobs.length > 0 && visibleJobs.length === 0 && (
            <p className="rounded-[16px] border border-dashed border-[#dedada] bg-white px-4 py-8 text-center text-[13px] font-semibold text-[#85868c] dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-white/50">
                {lang === 'zh' ? '没有符合的记录。' : 'No matching records.'}
            </p>
        )}
        {visibleJobs.map((job) => (
            <Fragment key={taskIdForJob(job)}>
            {restStartTaskId && restStartTaskId === taskIdForJob(job) ? (
                <p data-testid="records-after-pinned" className="px-1 pt-2 text-[12px] font-bold text-[#85868c] dark:text-white/50">
                    {lang === 'zh' ? '其余记录' : 'Other records'}
                </p>
            ) : null}
            <AgentTaskCard
                job={job}
                lang={lang}
                aheadName={queueAheadName(job)}
                retryError={retryError?.taskId === taskIdForJob(job) ? retryError.message : ''}
                highlighted={!!highlightTaskId && highlightTaskId === taskIdForJob(job)}
                {...busy}
                {...cardHandlers}
            />
            </Fragment>
        ))}
    </section>
);
