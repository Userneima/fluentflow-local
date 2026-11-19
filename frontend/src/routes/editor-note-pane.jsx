// The editor's note pane, moved out of editor.jsx: its title and save state,
// the note-follow switch and screenshots toggle, the note editor (or its
// loading line), and when there is no note, why and the 写笔记 button. The
// state it shows is held by the page: editor-note-draft.js (the working copy),
// editor-playback.js (noteFollow) and editor-note-regenerate.js.
import {useRef} from 'react';
import {Link} from 'react-router-dom';
import SvgIcon from '../components/SvgIcon.jsx';
import NoteWritingProgress from '../components/NoteWritingProgress.jsx';
import NoteEditor from '../components/NoteEditor.jsx';
import AgentNotePrompt from '../components/AgentNotePrompt.jsx';
import NoteExplanation from '../components/NoteExplanation.jsx';
import {FeatureHint} from '../components/FeatureHint.jsx';
import {API_BASE} from '../app/shared.jsx';
import {notePendingSentence} from '../lib/noteWriter.js';
import {button, linkButton, chip, surfaceCard} from '../lib/ui.js';

const NotePane = ({
    lang,
    t,
    paneStyles,
    result,
    noteDraft,
    noteFollow,
    mediaPlaying,
    hydrationFailed,
    noteBeingWritten,
    writeNoteButtonRef,
    setRegenerateConfirmOpen,
    transcript,
    summaryFailureHint,
    taskResume,
    noteWriter,
    resultJobOptions,
    setResumeWatch,
}) => {
    const {
        summary,
        summarySaveStatus,
        summarySaveLabel,
        summaryResultKey,
        summaryMarkdownForEditor,
        handleNoteEditingChange,
        handleSummaryChange,
        hasEditableSummary,
        noteReady,
        inlineVisualEvidenceCount,
        hasInlineVisualEvidence,
        visualEvidenceVisible,
        setVisualEvidenceVisible,
        renderedSummary,
    } = noteDraft;
    const summaryRef = useRef(null);
    return (
        <section style={paneStyles.right} className={`relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden xl:w-[38rem] xl:flex-none 2xl:w-[42rem] ${surfaceCard}`}>
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-[#e4e0e0] bg-[#fbfbfb] px-4 py-3 dark:border-white/[0.12] dark:bg-white/[0.04]">
                <h2 className="flex shrink-0 items-center gap-2 whitespace-nowrap font-headline text-[15px] font-extrabold text-[#111111] dark:text-white">
                    <SvgIcon name="psychology" className="text-[#111111] dark:text-white"/>
                    <FeatureHint id="review-note" when={hasEditableSummary && noteReady} lang={lang}>
                        <FeatureHint id="note-versions" when={hasEditableSummary && noteReady} lang={lang}>
                            {lang === 'zh' ? '笔记正文' : 'Note'}
                        </FeatureHint>
                    </FeatureHint>
                </h2>
                {summarySaveLabel && (
                    <span className={`mr-auto whitespace-nowrap rounded-full border px-2 py-1 text-[11px] font-bold ${
                        summarySaveStatus === 'failed'
                            ? 'border-error/25 bg-error-container text-on-error-container'
                            : 'border-[#e4e0e0] bg-white text-[#666] dark:border-white/[0.12] dark:bg-white/[0.05] dark:text-white/60'
                    }`}>
                        {summarySaveLabel}
                    </span>
                )}
                <div className="flex shrink-0 items-center gap-2">
                    {noteFollow.available && (
                        <FeatureHint id="note-follow" align="right" lang={lang}>
                            <button type="button" data-testid="note-follow-toggle" onClick={noteFollow.toggle} aria-pressed={noteFollow.following} className={chip(noteFollow.following)}>
                                {lang === 'zh' ? '笔记跟随' : 'Note follows'}
                            </button>
                        </FeatureHint>
                    )}
                    {noteFollow.failedReason && (
                        <span data-testid="note-follow-failed" title={noteFollow.failedReason} className="text-[12px] font-semibold text-[#666] dark:text-white/60">
                            {lang === 'zh' ? '章节时间没对上' : 'No section times'}
                        </span>
                    )}
                    {noteFollow.aligning && (
                        <span className="text-[12px] font-semibold text-[#666] dark:text-white/60">
                            {lang === 'zh' ? '正在对章节时间' : 'Matching sections to the recording'}
                        </span>
                    )}
                    {hasInlineVisualEvidence && (
                        <button
                            type="button"
                            onClick={()=>setVisualEvidenceVisible((value)=>!value)}
                            className={button('secondary', 'sm')}
                            title={lang === 'zh' ? `当前笔记包含 ${inlineVisualEvidenceCount} 张关键截图` : `${inlineVisualEvidenceCount} key screenshots in this note`}
                        >
                            <SvgIcon name={visualEvidenceVisible ? 'visibility_off' : 'visibility'} className="text-sm"/>
                            {visualEvidenceVisible
                                ? (lang === 'zh' ? '隐藏截图' : 'Hide screenshots')
                                : (lang === 'zh' ? '显示截图' : 'Show screenshots')}
                        </button>
                    )}
                    {summary && <NoteExplanation result={result} lang={lang}/>}
                </div>
            </div>
            {hasEditableSummary && !noteReady ? (
                <div data-testid="note-loading" className="hide-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
                    <p className="text-sm italic text-[#666] dark:text-white/60">
                        {hydrationFailed
                            ? (lang === 'zh' ? '笔记全文没有读出来，刷新页面再试。' : 'The full note did not load. Refresh the page to try again.')
                            : (lang === 'zh' ? '正在读取笔记全文…' : 'Loading the full note…')}
                    </p>
                </div>
            ) : hasEditableSummary ? (
                <>
                    <NoteEditor
                        key={summaryResultKey}
                        lang={lang}
                        markdown={summaryMarkdownForEditor}
                        onChange={handleSummaryChange}
                        onEditingChange={handleNoteEditingChange}
                        taskId={result?.task_id || ''}
                        apiBase={API_BASE}
                        follow={noteFollow.editorProps}
                    />
                    {noteFollow.available && !noteFollow.following && noteFollow.activeKey && mediaPlaying && (
                        <div className="absolute bottom-5 left-1/2 z-10 -translate-x-1/2 rounded-[10px] shadow-[0_12px_32px_-12px_rgba(17,17,17,0.5)]">
                            <button type="button" data-testid="note-follow-resume" onClick={noteFollow.resume} className={button('primary', 'sm')}>
                                <SvgIcon name="graphic_eq" className="text-[15px]"/>
                                {lang === 'zh' ? '回到正在播的一节' : 'Back to the section playing'}
                            </button>
                        </div>
                    )}
                    <div
                        ref={summaryRef}
                        className="fixed left-[-10000px] top-0 w-[720px] bg-white p-6 text-black"
                        dangerouslySetInnerHTML={{__html: renderedSummary}}
                    />
                </>
            ) : (
                <div className="hide-scrollbar min-h-0 flex-1 overflow-y-auto p-6 text-[#111111] dark:text-white">
                {result.summary_skipped ? (
                    <>
                        <p className="text-sm text-[#666] dark:text-white/60">{t(result.summary_skipped_reason === 'no_model_key' ? 'edit.summaryNoKey' : 'edit.summarySkipped')}</p>
                        {!noteBeingWritten && (
                            <button
                                ref={writeNoteButtonRef}
                                type="button"
                                data-testid="write-note"
                                onClick={()=>setRegenerateConfirmOpen(true)}
                                disabled={!transcript}
                                className={`my-3 ${button('primary', 'md')}`}
                            >
                                <SvgIcon name="edit_note" className="text-[15px]"/>
                                {lang === 'zh' ? '写笔记' : 'Write the note'}
                            </button>
                        )}
                        <AgentNotePrompt result={result} lang={lang}/>
                    </>
                ) : result.summary_status === 'failed' || result.summary_error ? (
                    <div className="space-y-3 text-sm text-[#666] dark:text-white/60">
                        <p>{t('edit.summaryFailed')}</p>
                        {!noteBeingWritten && (
                            <button
                                ref={writeNoteButtonRef}
                                type="button"
                                data-testid="write-note"
                                onClick={()=>setRegenerateConfirmOpen(true)}
                                disabled={!transcript}
                                className={button('primary', 'md')}
                            >
                                <SvgIcon name="edit_note" className="text-[15px]"/>
                                {lang === 'zh' ? '写笔记' : 'Write the note'}
                            </button>
                        )}
                        {summaryFailureHint && (
                            // A note the user stopped did not fail: said in grey, not red.
                            <p data-testid="note-failure-reason" data-tone={taskResume.offer?.kind === 'resume' ? 'stopped' : 'failed'} className={taskResume.offer?.kind === 'resume'
                                ? 'rounded-[12px] border border-[#e4e1e1] bg-[#f7f7f7] px-3 py-2 text-[13px] font-semibold leading-relaxed text-[#57585d] dark:border-white/[0.10] dark:bg-white/[0.04] dark:text-white/60'
                                : 'rounded-[12px] border border-[#fda29b] bg-[#fef3f2] px-3 py-2 text-[13px] font-semibold leading-relaxed text-[#b42318] dark:border-[#f04438]/40 dark:bg-[#f04438]/[0.12] dark:text-[#f97066]'}>
                                {summaryFailureHint}
                                {result?.task_id && (
                                    <>
                                        {' '}
                                        <Link data-testid="task-steps-link" to={`/tasks/${encodeURIComponent(result.task_id)}/agent`} className={linkButton()}>
                                            {lang === 'zh' ? '处理过程' : 'Processing steps'}
                                        </Link>
                                    </>
                                )}
                            </p>
                        )}
                        <AgentNotePrompt result={result} lang={lang}/>
                    </div>
                ) : (
                    <NoteWritingProgress
                        taskId={result?.task_id}
                        fallback={notePendingSentence(noteWriter, result, lang)}
                        requestOptions={resultJobOptions}
                        // The writer finished: fetch the note once the task is done,
                        // as after carrying a task on (some routes never mark the
                        // note pending, so the page would not poll for it).
                        onWriterDone={() => { if (result?.task_id) setResumeWatch({taskId: result.task_id}); }}
                    />
                )}
                </div>
            )}
        </section>
    );
};

export default NotePane;
