// The editor page (note and playback). It only calls the page's hooks and
// lays its parts out; each part lives beside it as editor-<part>.js(x), and a
// change goes into the part's file: the header bar editor-header.jsx, the
// transcript pane editor-transcript-pane.jsx and its rows
// editor-transcript-rows.jsx, the transcript's working copy and reading the
// full result editor-transcript-state.js, the note pane editor-note-pane.jsx
// and its working copy editor-note-draft.js, saving both editor-saves.js,
// playback and the transcript and note following it editor-playback.js, which
// file plays editor-media.js, the address editor-address.js, the cut report
// editor-cut-review.js, rewriting the note editor-note-regenerate.js, waiting
// for a note, a carried-on task or a Feishu export editor-note-polling.js,
// transcribing again editor-retranscribe.js. The hooks are called below in the
// order their effects must run; keep that order when adding one.
import {useState, useRef, useMemo} from 'react';
import {Link} from 'react-router-dom';
import SvgIcon from '../components/SvgIcon.jsx';
import EditorSplitHandle from '../components/EditorSplitHandle.jsx';
import {
    loadSplitRatio,
    saveSplitRatio,
    splitPaneStyles,
} from '../lib/editorLayoutPrefs.js';
import {
    createTaskId,
    resultDisplayTitle,
    useApi,
    useI18n,
    useSettings,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {FeatureHintScope} from '../components/FeatureHint.jsx';
import {RegenerateConfirmDialog, RetranscribeConfirmDialog, EditRecordsDialog, CutReviewDialog} from './editor-dialogs.jsx';
import {
    jobOptionsForResult,
    isVideoResultSource,
    cutFlowSummary,
    cutItemsToJudge,
    localSourceFileMatchesResult,
    playbackMediaChoice,
    summaryFailureNextStep,
    noteIsBeingWritten,
} from './editor-helpers.js';
import {button, surfaceCard} from '../lib/ui.js';
import {useEditorToast} from './editor-toast.js';
import {useEditorAddress} from './editor-address.js';
import {useResultTranscript} from './editor-transcript-state.js';
import {useNoteDraft} from './editor-note-draft.js';
import {useMediaSource} from './editor-media.js';
import {useCutReview} from './editor-cut-review.js';
import {usePlaybackFollow} from './editor-playback.js';
import {useContentSaves} from './editor-saves.js';
import {useResumeWatch, useNotePolling} from './editor-note-polling.js';
import {useNoteRegeneration} from './editor-note-regenerate.js';
import {useRetranscribe} from './editor-retranscribe.js';
import EditorHeader, {useTaskPin, useTaskStudied} from './editor-header.jsx';
import TranscriptPane from './editor-transcript-pane.jsx';
import NotePane from './editor-note-pane.jsx';

// The editor page: a result's transcript beside its note, with playback.
// Each part keeps its state and logic in its own file (editor-*.js(x) beside
// this one); this component calls their hooks and lays the parts out.
//
// The hooks are called in the order their effects ran when they were all in
// this component, and that order matters where effects share state (the
// transcript's working copy, scrolling and following, the saves, polling):
// toast timers, address, full result and transcript copy, note draft, the
// file playing, ?review=cut, playback and following (note following inside
// it), saves, carrying a task on, note rewrite, note polling.
const Editor = () => {
    const {t, lang} = useI18n();
    const {
        lastResult,
        setLastResult,
        lastSourceFile,
        setLastSourceFile,
        addToHistory,
        setCurrentJob,
        addLarkExport,
        runtimeConfig,
        ingestJobs,
        tasks,
    } = useApp();
    const {processVideoSSE, retryJob, resumeJob, pinJob, markStudied, getJobActivity, fetchJobArtifactFile, getJob, saveTranscriptEdit, saveSummaryEdit, startJobVisualNote, getVisualNoteAvailability, getCredentialsStatus} = useApi();
    const {loadSettings} = useSettings();
    const {toast, showToast, doneJobTimerRef} = useEditorToast();
    const writeNoteButtonRef = useRef(null);
    const fallbackTaskIdRef = useRef(createTaskId());
    // The task whose full note this page has read from the service. Until then
    // the note on the page may be the task list's or the browser cache's first
    // 240 characters, which must be neither edited nor saved: on 2026-10-22 the
    // page saved that preview over a 40,000-character note.
    const [noteReadFor, setNoteReadFor] = useState('');
    const {urlTaskId, urlWantsCutReview, setSearchParams, openingUrlTask, urlTaskMissing} = useEditorAddress({lastResult, getJob, setLastResult, setNoteReadFor, ingestJobs});

    const result = lastResult;
    const matchedLocalSourceFile = localSourceFileMatchesResult(lastSourceFile, result) ? lastSourceFile : null;
    // Only these three fields decide the options, so only they are watched; the
    // result object itself changes on every synced edit.
    const resultSttProvider = result?.stt_provider;
    const resultPlaybackStorage = result?.playback_audio_storage;
    const resultSourceStorage = result?.source_file_storage;
    const resultJobOptions = useMemo(() => jobOptionsForResult({
        stt_provider: resultSttProvider,
        playback_audio_storage: resultPlaybackStorage,
        source_file_storage: resultSourceStorage,
    }), [resultSttProvider, resultPlaybackStorage, resultSourceStorage]);

    const transcriptDraft = useResultTranscript({result, setLastResult, getJob, resultJobOptions, noteReadFor, setNoteReadFor, ingestJobs});
    const {segments, transcript, transcriptDirty, setHydrationFailed, visibleEditRecords} = transcriptDraft;
    const noteDraft = useNoteDraft({result, setLastResult, noteReadFor, lang});
    const {hasEditableSummary, noteReady, summaryMarkdownForEditor, summaryResultKey} = noteDraft;
    const media = useMediaSource({result, matchedLocalSourceFile, lang});
    const {mediaUrl, mediaKind, mediaError, setMediaError, loadMediaFile, mediaObjectUrlRef} = media;

    const durSec = result?.audio_duration_seconds || 0;
    const activeTaskId = result?.task_id || fallbackTaskIdRef.current;
    const pin = useTaskPin({activeTaskId, tasks, pinJob, ingestJobs});
    // A note edited in the last moment is still on its way to the service: the
    // study record is made from the note the service has, so wait for it.
    const study = useTaskStudied({activeTaskId, tasks, markStudied, ingestJobs, saving: noteDraft.summaryUnsaved});
    const summaryFailureHint = summaryFailureNextStep(result, lang);
    const resultTitle = resultDisplayTitle(result, {name: t('edit.title')});
    const rawEditorTitle = resultTitle || result?.filename || t('edit.title');
    const playingCutVersion = !matchedLocalSourceFile && playbackMediaChoice(result)?.isCut === true;
    // What already happened to this recording, for the one line under the title.
    // Gated on the same condition as the badge above: the bar's sentence is about
    // what is playing, so once the reader has pointed the player at a file of their
    // own it would be describing something that is no longer on screen.
    const cutFlow = matchedLocalSourceFile ? null : cutFlowSummary(result);
    // No cut file means nothing may play, so this has to be said rather than left
    // as an empty player. Either the record of the file is gone, or reading it
    // failed — the effect above refuses to substitute the recording in both cases.
    const cutFileUnavailable = !!cutFlow && (!cutFlow.mediaArtifact || (!!mediaError && !mediaUrl));
    const cutJudgeCount = cutItemsToJudge(result);
    const {cutReviewOpen, setCutReviewOpen, closeCutReview} = useCutReview({result, urlWantsCutReview, urlTaskId, setSearchParams, getJob, resultJobOptions, setLastResult});
    const canShowVideoReview = isVideoResultSource(result, matchedLocalSourceFile);
    const canUseVideoReview = canShowVideoReview && mediaKind === 'video' && !!mediaUrl && segments.length > 0;

    const playback = usePlaybackFollow({
        result,
        activeTaskId,
        durSec,
        mediaUrl,
        setMediaError,
        mediaObjectUrlRef,
        canUseVideoReview,
        segments,
        visibleTranscriptSegments: transcriptDraft.visibleTranscriptSegments,
        visibleTranscriptView: transcriptDraft.visibleTranscriptView,
        handleSegmentTextChange: transcriptDraft.handleSegmentTextChange,
        noteReady,
        summaryMarkdownForEditor,
        summaryResultKey,
        playingCutVersion,
    });
    const {seekToSegment, noteFollow, mediaPlaying} = playback;

    useContentSaves({
        result,
        setLastResult,
        getJob,
        saveTranscriptEdit,
        saveSummaryEdit,
        resultJobOptions,
        showToast,
        transcriptDraft,
        noteDraft,
    });

    const {setResumeWatch, resumeWatch, taskResume} = useResumeWatch({
        taskId: result?.task_id || '',
        getJobActivity,
        resumeJob,
        jobOptions: resultJobOptions,
        lang,
        showToast,
    });

    const {
        regenerating,
        setRegenerating,
        regenerateConfirmOpen,
        setRegenerateConfirmOpen,
        noteWatch,
        setNoteWatch,
        noteWriter,
        regenerateCopy,
        handleRegenerate,
    } = useNoteRegeneration({
        result,
        runtimeConfig,
        loadSettings,
        lang,
        t,
        getCredentialsStatus,
        getVisualNoteAvailability,
        startJobVisualNote,
        getJob,
        setLastResult,
        resultJobOptions,
        transcript,
        segments,
        transcriptDirty,
        activeTaskId,
        durSec,
        setNoteReadFor,
        setHydrationFailed,
        showToast,
    });

    useNotePolling({
        result,
        getJob,
        setLastResult,
        showToast,
        resultJobOptions,
        summaryUnsaved: noteDraft.summaryUnsaved,
        noteCellOpenRef: noteDraft.noteCellOpenRef,
        noteWatch,
        setNoteWatch,
        setRegenerating,
        resumeWatch,
        setResumeWatch,
        lang,
        t,
    });

    const {
        retranscribeConfirmOpen,
        setRetranscribeConfirmOpen,
        retranscribeInputRef,
        canRetranscribeStoredMedia,
        retranscribeSourceLabel,
        retranscribeSourceName,
        runRetranscribe,
        handleRetranscribe,
        confirmRetranscribe,
    } = useRetranscribe({
        result,
        matchedLocalSourceFile,
        lang,
        t,
        runtimeConfig,
        loadSettings,
        showToast,
        doneJobTimerRef,
        setLastSourceFile,
        setCurrentJob,
        setLastResult,
        addToHistory,
        addLarkExport,
        ingestJobs,
        processVideoSSE,
        retryJob,
        fetchJobArtifactFile,
        resultJobOptions,
    });

    const [editRecordsOpen, setEditRecordsOpen] = useState(false);
    const [splitRatio, setSplitRatio] = useState(loadSplitRatio);
    const mediaInputRef = useRef(null);
    const splitContainerRef = useRef(null);

    // A file picked here plays for this visit only. FluentFlow keeps no copy:
    // on this machine that would mean copying a recording of several gigabytes
    // through the browser, for the rare case where both the original and the
    // cut file have gone missing.
    const handleMediaFileSelected = (file) => {
        if (!file) return;
        setLastSourceFile(file);
        loadMediaFile(file);
        if (!result?.task_id) return;
        showToast(lang === 'zh'
            ? '这次可以播放了。FluentFlow 不另存这个文件，下次打开还读不到的话需要再选一次。'
            : 'Playing now. FluentFlow does not keep a copy, so choose it again next time if it still cannot be found.');
    };

    if (openingUrlTask) return (
        <div className="ml-[var(--sidebar-offset)] flex min-h-dvh items-center justify-center bg-[#f8f7fb] text-sm font-semibold text-[#666] dark:bg-[#101010] dark:text-white/60">
            <SvgIcon name="sync" className="mr-2 h-4 w-4 animate-spin"/>
            {lang === 'zh' ? '正在打开这条记录…' : 'Opening this record…'}
        </div>
    );

    if(!result || urlTaskMissing) return (
        <div className="ml-[var(--sidebar-offset)] min-h-dvh bg-[#f8f7fb] pb-8 text-[#111111] dark:bg-[#101010] dark:text-white/[0.92]">
            <main className="flex h-[calc(100vh-2rem)] items-center justify-center px-8">
                <div className={`w-full max-w-xl px-8 py-10 text-center ${surfaceCard}`}>
                    <SvgIcon name="edit_note" className="mb-4 text-5xl text-[#8a8a8a] dark:text-white/40"/>
                    <h2 className="mb-2 font-headline text-[18px] font-extrabold text-[#111111] dark:text-white">{urlTaskMissing ? (lang === 'zh' ? '找不到这条记录' : 'Record not found') : t('edit.noResult')}</h2>
                    <p className="mb-6 text-sm font-semibold leading-relaxed text-[#666] dark:text-white/60">{urlTaskMissing ? (lang === 'zh' ? '它可能已被删除。从处理记录选一条打开。' : 'It may have been deleted. Open one from the records.') : t('edit.noResultDesc')}</p>
                    <Link to="/agent" className={button('primary', 'lg')}>
                        <SvgIcon name="monitoring" className="text-lg"/>{t('edit.chooseRecord')}
                    </Link>
                                            </div>
            </main>
                                                </div>
    );

    const noteBeingWritten = regenerating || noteIsBeingWritten(result);
    const paneStyles = splitPaneStyles(splitRatio);
    const commitSplitRatio = (ratio) => saveSplitRatio(ratio === undefined ? splitRatio : ratio);
    const resetSplitRatio = () => { setSplitRatio(null); saveSplitRatio(null); };

    return (
    <div className="ml-[var(--sidebar-offset)] min-h-dvh bg-[#f8f7fb] pb-8 text-[#111111] dark:bg-[#101010] dark:text-white/[0.92]">
        {toast && (
            <div className={`fixed right-8 top-6 z-50 rounded-[16px] px-5 py-3 text-sm font-bold shadow-[0_18px_44px_-26px_rgba(17,17,17,.55)] ${toast.ok?'bg-[#111111] text-white dark:bg-white dark:text-[#111111]':'bg-red-600 text-white'}`}>
                {toast.msg}
                                            </div>
        )}
        {regenerateConfirmOpen && (
            <RegenerateConfirmDialog
                transcriptTitle={rawEditorTitle}
                description={regenerateCopy.desc}
                warning={regenerateCopy.warning}
                confirmDisabled={regenerateCopy.confirmDisabled}
                alternative={regenerateCopy.alternative}
                onAlternative={()=>handleRegenerate('text')}
                onCancel={()=>setRegenerateConfirmOpen(false)}
                onConfirm={()=>handleRegenerate(regenerateCopy.primary)}
            />
        )}
        {retranscribeConfirmOpen && (
            <RetranscribeConfirmDialog
                canRetranscribe={canRetranscribeStoredMedia}
                sourceLabel={retranscribeSourceLabel}
                sourceName={retranscribeSourceName}
                onCancel={()=>setRetranscribeConfirmOpen(false)}
                onConfirm={confirmRetranscribe}
            />
        )}
        {cutReviewOpen && (
            <CutReviewDialog result={result} onClose={closeCutReview}/>
        )}
        {editRecordsOpen && (
            <EditRecordsDialog
                records={visibleEditRecords}
                onClose={()=>setEditRecordsOpen(false)}
                onSeek={(record)=>{ setEditRecordsOpen(false); seekToSegment(record); }}
            />
        )}
        <input
            ref={retranscribeInputRef}
            type="file"
            accept="video/*,audio/*,.mp4,.mov,.avi,.mkv,.webm,.mp3,.wav,.flac,.aac,.ogg,.m4a,.wma,.opus"
            className="hidden"
            onChange={e=>{
                const file=e.target.files?.[0];
                if(e.target) e.target.value='';
                if(file) runRetranscribe(file);
            }}
        />
        <input
            ref={mediaInputRef}
            type="file"
            accept="video/*,audio/*,.mp4,.mov,.avi,.mkv,.webm,.mp3,.wav,.flac,.aac,.ogg,.m4a,.wma,.opus"
            className="hidden"
            onChange={e=>{
                const file=e.target.files?.[0];
                if(e.target) e.target.value='';
                if(file) handleMediaFileSelected(file);
            }}
        />
        <main className="h-dvh overflow-hidden px-4 pb-4 pt-5 xl:px-5 2xl:px-6">
            <div className="mx-auto h-full min-h-0 w-full flex flex-col gap-3">
                    <EditorHeader
                        lang={lang}
                        result={result}
                        resultTitle={resultTitle}
                        rawEditorTitle={rawEditorTitle}
                        activeTaskId={activeTaskId}
                        pin={pin}
                        study={result?.task_id ? study : null}
                        taskResume={taskResume}
                        cutFileUnavailable={cutFileUnavailable}
                        cutJudgeCount={cutJudgeCount}
                        noteBeingWritten={noteBeingWritten}
                        hasEditableSummary={hasEditableSummary}
                        handleRetranscribe={handleRetranscribe}
                        setCutReviewOpen={setCutReviewOpen}
                        writeNoteButtonRef={writeNoteButtonRef}
                    />


                        <div ref={splitContainerRef} className="flex min-h-0 flex-1 gap-0 overflow-hidden">
                            <TranscriptPane
                                lang={lang}
                                t={t}
                                paneStyles={paneStyles}
                                transcriptDraft={transcriptDraft}
                                playback={playback}
                                media={media}
                                canShowVideoReview={canShowVideoReview}
                                canUseVideoReview={canUseVideoReview}
                                playingCutVersion={playingCutVersion}
                                durSec={durSec}
                                mediaInputRef={mediaInputRef}
                                setEditRecordsOpen={setEditRecordsOpen}
                            />

                            <EditorSplitHandle
                                containerRef={splitContainerRef}
                                ratio={splitRatio}
                                onChange={setSplitRatio}
                                onCommit={commitSplitRatio}
                                onReset={resetSplitRatio}
                                lang={lang}
                            />

                            <NotePane
                                lang={lang}
                                t={t}
                                paneStyles={paneStyles}
                                result={result}
                                noteDraft={noteDraft}
                                noteFollow={noteFollow}
                                mediaPlaying={mediaPlaying}
                                hydrationFailed={transcriptDraft.hydrationFailed}
                                noteBeingWritten={noteBeingWritten}
                                writeNoteButtonRef={writeNoteButtonRef}
                                setRegenerateConfirmOpen={setRegenerateConfirmOpen}
                                transcript={transcript}
                                summaryFailureHint={summaryFailureHint}
                                taskResume={taskResume}
                                noteWriter={noteWriter}
                                resultJobOptions={resultJobOptions}
                                setResumeWatch={setResumeWatch}
                            />
                        </div>
                    </div>
                </main>
            </div>
        );
};

// Each visit to the editor is one round of first-use hints (FeatureHint).
const EditorPage = () => <FeatureHintScope><Editor/></FeatureHintScope>;

export default EditorPage;