// The editor's header bar, moved out of editor.jsx: where the page is
// (处理记录 / title), the one thing that needs the user if anything
// (headerStatus), the Feishu document, pinning the task and its output
// folder. useTaskPin is the pin's state, kept by the page.
import {useMemo, useState} from 'react';
import {Link} from 'react-router-dom';
import {AlertCircle, Pin} from 'lucide-react';
import {markBackendJob} from '../lib/taskState.js';
import SvgIcon from '../components/SvgIcon.jsx';
import OutputFolderLine from '../components/OutputFolderLine.jsx';
import {isPinnedJob} from '../app/shared.jsx';
import {iconButton, statusButton} from '../lib/ui.js';

export const useTaskPin = ({activeTaskId, tasks, pinJob, ingestJobs}) => {
    // Pinned or not comes from the shared task list, which the records page
    // reads too, so both show the same state.
    const activeTaskPinned = useMemo(
        () => isPinnedJob((tasks || []).find((job) => String(job?.task_id || '') === String(activeTaskId || ''))),
        [tasks, activeTaskId],
    );
    const [pinBusy, setPinBusy] = useState(false);
    const [pinError, setPinError] = useState('');
    const togglePin = async () => {
        if (!activeTaskId || pinBusy) return;
        setPinBusy(true);
        setPinError('');
        try {
            const updated = await pinJob(activeTaskId, !activeTaskPinned);
            if (updated?.task_id) ingestJobs([markBackendJob(updated)]);
        } catch (err) {
            setPinError(err?.message || String(err));
        } finally {
            setPinBusy(false);
        }
    };
    return {activeTaskPinned, pinBusy, pinError, togglePin};
};

const EditorHeader = ({
    lang,
    result,
    resultTitle,
    rawEditorTitle,
    activeTaskId,
    pin,
    taskResume,
    cutFileUnavailable,
    cutJudgeCount,
    noteBeingWritten,
    hasEditableSummary,
    handleRetranscribe,
    setCutReviewOpen,
    writeNoteButtonRef,
}) => {
    const {activeTaskPinned, pinBusy, pinError, togglePin} = pin;
    // The one thing in the slim header that needs the user, if anything, and
    // where clicking it takes them.
    const larkDocUrl = result?.lark_response?.url || '';
    const larkDocTitle = result?.lark_doc_title || resultTitle || '';
    const autoExportError = !larkDocUrl && result?.lark_error ? String(result.lark_error) : '';
    const noteMissing = !!result && !hasEditableSummary && (result.summary_skipped || result.summary_status === 'failed' || !!result.summary_error);
    const resumeLabel = taskResume.offer
        ? `${taskResume.offer.sentence.replace(/[。.]$/, '')}${lang === 'zh' ? '，' : ': '}${taskResume.resuming ? (lang === 'zh' ? '正在入队…' : 'queuing…') : taskResume.offer.label}`
        : '';
    const headerStatus = cutFileUnavailable
        ? {tone: 'error', label: lang === 'zh' ? '剪后文件读不到了，重新处理' : 'The cut file cannot be read: process again', onClick: handleRetranscribe}
        // A stopped task, or a step that failed, comes first: the task is not
        // finished until it is carried on.
        : taskResume.offer
            ? {tone: 'ask', label: resumeLabel, onClick: taskResume.resuming ? null : taskResume.resume}
        : cutJudgeCount > 0
            ? {tone: 'ask', label: lang === 'zh' ? `剪辑有 ${cutJudgeCount} 处需要你判断` : `${cutJudgeCount} cut decision${cutJudgeCount === 1 ? '' : 's'} need your call`, onClick: () => setCutReviewOpen(true)}
            : noteBeingWritten
                ? {tone: 'busy', label: lang === 'zh' ? '写笔记中' : 'Writing the note', onClick: null}
                : noteMissing
                    ? {tone: 'ask', label: lang === 'zh' ? '笔记没生成' : 'No note yet', onClick: () => { writeNoteButtonRef.current?.scrollIntoView({block: 'center'}); writeNoteButtonRef.current?.focus(); }}
                    : autoExportError
                        // Nothing on this page redoes the export; the reason is on hover.
                        ? {tone: 'error', label: lang === 'zh' ? '自动导出到飞书没成功' : 'The automatic Feishu export failed', title: autoExportError, onClick: null}
                        : null;
    return (
        <div data-testid="editor-header" className="flex h-10 shrink-0 items-center gap-3">
            <nav className="flex min-w-0 items-center gap-1.5 text-[13px] leading-none">
                <Link to="/agent" className="shrink-0 font-semibold text-[#666] hover:text-[#111111] dark:text-white/60 dark:hover:text-white">
                    {lang === 'zh' ? '处理记录' : 'Records'}
                </Link>
                <span className="shrink-0 text-[#b4b4b4] dark:text-white/30">/</span>
                <h1 className="min-w-0 truncate text-[13px] font-extrabold text-[#111111] dark:text-white" title={rawEditorTitle}>
                    {rawEditorTitle}
                </h1>
            </nav>
            {headerStatus && (
                <button
                    type="button"
                    data-testid="editor-header-status"
                    title={headerStatus.title || undefined}
                    onClick={headerStatus.onClick}
                    disabled={!headerStatus.onClick}
                    className={statusButton(headerStatus.tone === 'error' ? 'error' : headerStatus.tone === 'busy' ? 'busy' : 'warning')}
                >
                    {headerStatus.tone === 'busy'
                        ? <SvgIcon name="sync" className="text-[13px] animate-spin"/>
                        : <AlertCircle className="size-3.5" strokeWidth={2.15}/>}
                    {headerStatus.label}
                </button>
            )}
            <div className="ml-auto flex shrink-0 items-center gap-1.5">
                {/* The Feishu document this note was exported to, when there is one. */}
                {larkDocUrl && (
                    <a
                        href={larkDocUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        data-testid="lark-export-link"
                        aria-label={lang === 'zh' ? '打开飞书文档' : 'Open the Feishu document'}
                        title={`${lang === 'zh' ? '打开飞书文档' : 'Open the Feishu document'}：${larkDocTitle}`}
                        className={iconButton('secondary', 'sm')}
                    >
                        <SvgIcon name="open_in_new" className="text-base"/>
                    </a>
                )}
                {activeTaskId ? (
                    <button
                        type="button"
                        data-testid="editor-pin"
                        onClick={togglePin}
                        disabled={pinBusy}
                        aria-pressed={activeTaskPinned}
                        aria-label={activeTaskPinned ? (lang === 'zh' ? '取消置顶' : 'Unpin') : (lang === 'zh' ? '置顶到处理记录最前面' : 'Pin to the top of records')}
                        title={pinError || (activeTaskPinned ? (lang === 'zh' ? '已置顶，点一下取消' : 'Pinned. Click to unpin') : (lang === 'zh' ? '置顶到处理记录最前面' : 'Pin to the top of records'))}
                        className={iconButton('secondary', 'sm')}
                    >
                        <Pin className={`size-4 ${activeTaskPinned ? 'fill-current' : ''}`} strokeWidth={2.15}/>
                    </button>
                ) : null}
                <OutputFolderLine taskId={activeTaskId} result={result} lang={lang} compact labelled/>
            </div>
        </div>
    );
};

export default EditorHeader;
