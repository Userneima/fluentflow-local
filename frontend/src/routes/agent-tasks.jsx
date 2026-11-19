import {useEffect} from 'react';
import {useLocation, useNavigate} from 'react-router-dom';
import {useConfirm} from '../components/ConfirmDialog.jsx';
import {useI18n} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {useJobPolling} from '../lib/useJobPolling.js';
import {extraUrlsIgnoredNotice} from '../lib/linkRetry.js';
import {linkButton} from '../lib/ui.js';
import {QueueUploadBanner, LarkExportList} from './agent-tasks-banners.jsx';
import {useNavigationNotices} from './agent-tasks-notices.js';
import {useRecordList} from './agent-tasks-list.js';
import {useTaskActions} from './agent-tasks-actions.js';
import {RecordsToolbar, RecordsList} from './agent-tasks-records.jsx';

// The records page: every task, newest first, as one card each.
// Each part keeps its state and logic in its own file (agent-tasks-*.js(x)
// beside this one); this component calls their hooks and lays the parts out:
// which task a row is and the merged list `agent-tasks-jobs.js`, the words on a
// card `agent-tasks-labels.js`, the card `agent-tasks-card.jsx`, the filters,
// search and counts `agent-tasks-list.js`, the notices the page was sent with
// `agent-tasks-notices.js`, what the buttons do `agent-tasks-actions.js`, the
// filter bar and list `agent-tasks-records.jsx`, the upload banner and Feishu
// exports `agent-tasks-banners.jsx`.
//
// The hooks are called in the order their effects ran when they were all in
// this component: the confirm dialog, the notices from the navigation, the
// list (its filter follows a highlighted task), polling, then the seeded job
// and the queue-submit error and address cleanup below. useTaskActions has no
// effects and comes after polling because it needs loadJobs and setError.
const AgentTasks = () => {
    const {lang, t} = useI18n();
    const [confirm, confirmDialog] = useConfirm();
    // Read the shared task list + mutations from AppProvider; this page no
    // longer keeps a private jobs state, warm cache, or writes the cache
    // (plan Stage 3b).
    const {currentJob, tasks: jobs, ingestJobs, larkExports} = useApp();
    const location = useLocation();
    const navigate = useNavigate();
    const seededJob = location.state?.job && typeof location.state.job === 'object' ? location.state.job : null;
    const {extraUrlsIgnored, setExtraUrlsIgnored, highlightTaskId, duplicateNotice, setDuplicateNotice} = useNavigationNotices(location);
    const {
        queueUploadJob, displayJobs, queueAheadName, hasLiveOrUploadingJobs,
        query, setQuery, filter, setFilter, filterCounts, visibleJobs, restStartTaskId,
        queuedCount, runningCount,
    } = useRecordList({currentJob, jobs, lang, location, highlightTaskId});

    // Shared fetch + polling (see lib/useJobPolling.js), with task-oriented wording.
    const {loading, error, setError, loadJobs} = useJobPolling({
        hasLiveJobs: hasLiveOrUploadingJobs,
        refreshFailedZh: '处理记录刷新失败，先显示上次读到的。',
        refreshFailedEn: 'Failed to refresh tasks. Local cache is preserved.',
    });

    // Surface a job passed via navigation state immediately by ingesting it.
    useEffect(() => {
        if (seededJob) ingestJobs([seededJob]);
    }, [seededJob, ingestJobs]);

    useEffect(() => {
        setError(location.state?.queueSubmitError || null);
    }, [location.state?.queueSubmitError, setError]);

    useEffect(() => {
        if (location.state?.queueSubmitError || location.state?.queueSubmittedAt) {
            navigate('/agent', {replace: true, state: {}});
        }
    }, [location.state?.queueSubmitError, location.state?.queueSubmittedAt, navigate]);

    const {busy, retryError, cancelPendingUpload, cardHandlers} = useTaskActions({confirm, t, lang, navigate, loadJobs, setError});

    return (
        <main className="ml-[var(--sidebar-offset)] h-dvh flex-1 overflow-y-auto bg-[#f8f7fb] px-6 py-5 text-[#111111] transition-[margin] duration-200 ease-out hide-scrollbar dark:bg-[#101010] dark:text-white/[0.92] lg:px-10">
            <div className="mx-auto max-w-7xl space-y-5">

                {error && (
                    <div className="rounded-[16px] border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
                        {error}
                    </div>
                )}

                {extraUrlsIgnored ? (
                    <div data-testid="extra-urls-ignored" className="flex items-start justify-between gap-3 rounded-[16px] border border-[#f5c86b] bg-[#fffaf0] px-4 py-3 text-sm font-semibold text-[#8a5a00] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]">
                        <span>{extraUrlsIgnoredNotice(lang)}</span>
                        <button type="button" onClick={() => setExtraUrlsIgnored(false)} className={`shrink-0 ${linkButton('inherit', 'sm')}`}>
                            {lang === 'zh' ? '知道了' : 'Got it'}
                        </button>
                    </div>
                ) : null}

                {duplicateNotice ? (
                    <div data-testid="duplicate-link-notice" className="flex items-start justify-between gap-3 rounded-[16px] border border-[#c7d2fe] bg-[#eef2ff] px-4 py-3 text-sm font-semibold text-[#3730a3] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/80">
                        <span>{lang === 'zh' ? '这个链接已经在处理中，没有重复提交。' : 'This link is already being processed; it was not submitted again.'}</span>
                        <button type="button" onClick={() => setDuplicateNotice(false)} className={`shrink-0 ${linkButton('inherit', 'sm')}`}>
                            {lang === 'zh' ? '知道了' : 'Got it'}
                        </button>
                    </div>
                ) : null}

                {queueUploadJob ? <QueueUploadBanner upload={queueUploadJob} lang={lang} onCancel={cancelPendingUpload}/> : null}

                {/* One line instead of three tiles: the count of records only
                    once the full list is in (it used to climb from the cached 100
                    to the real total), and what is running only when something is. */}
                <RecordsToolbar
                    lang={lang}
                    loading={loading}
                    filter={filter}
                    setFilter={setFilter}
                    filterCounts={filterCounts}
                    runningCount={runningCount}
                    queuedCount={queuedCount}
                    query={query}
                    setQuery={setQuery}
                />

                <RecordsList
                    lang={lang}
                    loading={loading}
                    displayJobs={displayJobs}
                    visibleJobs={visibleJobs}
                    queueUploadJob={queueUploadJob}
                    restStartTaskId={restStartTaskId}
                    queueAheadName={queueAheadName}
                    retryError={retryError}
                    highlightTaskId={highlightTaskId}
                    busy={busy}
                    cardHandlers={cardHandlers}
                />

                <LarkExportList exports={larkExports} lang={lang}/>
            </div>
            {confirmDialog}
        </main>
    );
};

export default AgentTasks;
