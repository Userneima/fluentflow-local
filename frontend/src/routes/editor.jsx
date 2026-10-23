import {memo,useState,useEffect,useRef,useCallback,useMemo} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {AlertCircle} from 'lucide-react';
import SvgIcon from '../components/SvgIcon.jsx';
import EditorSplitHandle from '../components/EditorSplitHandle.jsx';
import {
    loadSplitRatio,
    saveSplitRatio,
    splitPaneStyles,
} from '../lib/editorLayoutPrefs.js';
import {
    API_BASE,
    createTaskId,
    effectiveSttProvider,
    submittedSttProvider,
    autoSizeTextarea,
    buildTranscriptEditRecords,
    composeTranscriptText,
    fmtTime,
    apiFetch,
    fileNameStem,
    isLocalHistoryResult,
    larkExportRouteFromSettings,
    pickDisplayTranscriptSegments,
    normalizeSttModel,
    pickTranscriptBaselineSegments,
    pickTranscriptSegments,
    resultToHistoryEntry,
    resultDisplayTitle,
    simpleMd,
    useApi,
    useI18n,
    useSettings,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {notePendingSentence, noteWriterLabel, resolveNoteWriter} from '../lib/noteWriter.js';
import NoteEditor from '../components/NoteEditor.jsx';
import AgentNotePrompt from '../components/AgentNotePrompt.jsx';
import NoteExplanation from '../components/NoteExplanation.jsx';
import OutputFolderLine from '../components/OutputFolderLine.jsx';
import {probeMedia} from '../lib/probeMedia.js';
import {createPlaybackClock, usePlaybackTime} from '../lib/playbackClock.js';
import {RegenerateConfirmDialog, RetranscribeConfirmDialog, EditRecordsDialog, CutReviewDialog} from './editor-dialogs.jsx';
import {
    jobOptionsForResult,
    isLikelyVideoFile,
    isVideoResultSource,
    isAutoCutFlow,
    cutFlowSummary,
    cutItemsToJudge,
    findSegmentAt,
    sizeTextareas,
    textareaSizesItself,
    localSourceFileMatchesResult,
    playbackMediaChoice,
    shouldKeepVideoReviewMounted,
    summaryFailureNextStep,
    NOTE_POLL_INTERVAL_MS,
    noteIsBeingWritten,
    noteWritingSettled,
    claudeRewriteSettled,
    mergeNoteFields,
    mergeLarkFields,
    AUTO_EXPORT_WAIT_MS,
    autoExportPending,
    larkExportSettled,
    regenerateDialogCopy,
    visualNoteStartErrorMessage,
} from './editor-helpers.js';

// The records worth showing: a changed sentence whose current text is still the
// changed version. Shared by the records dialog and the save payload.
const filterVisibleEditRecords = (records, segments) => records.filter((record) => {
    const before = String(record?.before || '').trim();
    const after = String(record?.after || '').trim();
    if (!before && !after) return false;
    if (before === after) return false;
    const current = segments[record?.index];
    if (!current) return false;
    return String(current.text || '').trim() === after;
});

// Which result a pending edit belongs to. A result opened while an edit is
// still waiting to sync must neither inherit the edit nor be overwritten by it.
const resultIdentity = (result) => (result ? `${result.task_id || result.filename || 'current_result'}` : 'empty_result');

// The transcript can hold thousands of sentences (7104 on a 3.5-hour
// interview), so each one is its own memoised row: typing in one sentence or
// the player moving to the next re-renders that row, not the list. Callbacks
// passed in are stable, and a row's sentence object only changes when that
// sentence is edited. Boxes size themselves with CSS (field-sizing); where the
// browser cannot, the page sizes them all in one pass (sizeTextareas).
const SIZES_ITSELF = textareaSizesItself();
// Rows off screen are skipped in layout and paint until scrolled near
// (content-visibility); the browser's find-in-page still searches them. Laying
// out every one of 7104 rows made switching modes and resizing the pane slow.
// The rows have no CSS transitions: a mode switch restyles every row, and
// transitions there fired about 28,000 transitionend events per switch.
const OFFSCREEN_SKIP = {contentVisibility: 'auto', containIntrinsicSize: 'auto 3rem'};

const SentenceTime = memo(({seg, active, className, onSeek, children}) => (
    <button type="button" onClick={() => onSeek(seg)} className={className(active)}>
        {children}
    </button>
));

const TranscriptLine = memo(({index, seg, active, review, onSeek, onEdit, onFocusLine, registerNode}) => (
    <div
        ref={(node) => registerNode(index, node)}
        style={OFFSCREEN_SKIP}
        className={review
            ? `grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 px-1 py-2 ${active ? 'bg-[#eef2ff] dark:bg-white/[0.08]' : 'hover:bg-white/70 dark:hover:bg-white/[0.04]'}`
            : `group grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 rounded-[16px] px-3 py-2.5 ${active ? 'bg-[#eef2ff] dark:bg-white/[0.1]' : 'hover:bg-[#f8f7fb] dark:hover:bg-white/[0.06]'}`}
    >
        <SentenceTime
            seg={seg}
            active={active}
            onSeek={onSeek}
            className={(on) => (review
                ? `pt-[1px] text-left font-mono text-xs font-bold tabular-nums ${on ? 'text-primary dark:text-white' : 'text-[#8a8a8a] hover:text-primary dark:text-white/42 dark:hover:text-white'}`
                : `pt-[1px] text-left font-mono text-xs tabular-nums ${on ? 'font-bold text-primary' : 'text-[#8a8a8a] hover:text-primary dark:text-white/40'}`)}
        >
            {fmtTime(seg.start)}
        </SentenceTime>
        <div className="min-w-0 flex-1">
            <textarea
                data-transcript-segment="true"
                value={seg.text || ''}
                onChange={(e) => { if (!SIZES_ITSELF) autoSizeTextarea(e.target); onEdit(index, e.target.value); }}
                onFocus={onFocusLine}
                rows={1}
                className={review
                    ? 'min-h-[1.45rem] w-full resize-none overflow-hidden border-none bg-transparent p-0 text-sm font-semibold leading-snug text-[#111111] [field-sizing:content] focus:ring-0 dark:text-white'
                    : 'min-h-[1.75rem] w-full resize-none overflow-hidden border-none bg-transparent p-0 text-sm font-medium leading-snug text-[#111111] [field-sizing:content] focus:ring-0 dark:text-white'}
            />
        </div>
    </div>
));

const BilingualLine = memo(({index, seg, active, review, onSeek, registerNode}) => (
    <div
        ref={(node) => registerNode(index, node)}
        style={OFFSCREEN_SKIP}
        className={review
            ? `grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 px-1 py-2 ${active ? 'bg-[#eef2ff] dark:bg-white/[0.08]' : 'hover:bg-white/70 dark:hover:bg-white/[0.04]'}`
            : `grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 rounded-[16px] px-3 py-2.5 ${active ? 'bg-[#eef2ff] dark:bg-white/[0.1]' : 'hover:bg-[#f8f7fb] dark:hover:bg-white/[0.06]'}`}
    >
        <SentenceTime
            seg={seg}
            active={active}
            onSeek={onSeek}
            className={(on) => (review
                ? `pt-[1px] text-left font-mono text-xs font-bold tabular-nums ${on ? 'text-primary dark:text-white' : 'text-[#8a8a8a] hover:text-primary dark:text-white/42 dark:hover:text-white'}`
                : `flex flex-col items-start justify-start pt-[1px] font-mono text-xs tabular-nums ${on ? 'font-bold text-primary' : 'text-[#777] hover:text-primary dark:text-white/45'}`)}
        >
            {review ? fmtTime(seg.start) : (
                <>
                    <span className="block">{fmtTime(seg.start)}</span>
                    <span className="mt-0.5 block text-[10px] opacity-70">{fmtTime(seg.end)}</span>
                </>
            )}
        </SentenceTime>
        <div className="min-w-0 flex-1">
            <p className={`whitespace-pre-wrap text-sm leading-snug text-[#111111] dark:text-white ${review ? 'font-semibold' : 'font-medium'}`}>
                {seg.text}
            </p>
            <p className={`mt-1.5 border-l-2 border-primary/25 pl-3 text-sm leading-snug text-[#666] ${review ? 'font-semibold dark:text-white/68' : 'font-medium dark:text-white/65'}`}>
                {seg.text_zh}
            </p>
        </div>
    </div>
));

// The clock and the progress bar: the only parts of the page that follow the
// player every tick.
const PlaybackBar = ({clock, duration, onSeek}) => {
    const time = usePlaybackTime(clock);
    const progress = duration > 0 ? Math.min(100, Math.max(0, time / duration * 100)) : 0;
    return (
        <>
            <span className="text-xs font-mono text-on-surface-variant ml-auto">{fmtTime(time)} / {fmtTime(duration || time)}</span>
            <input
                type="range"
                min="0"
                max={Math.max(1, duration)}
                step="0.1"
                value={Math.min(time, Math.max(1, duration))}
                onChange={(e) => onSeek(Number(e.target.value) || 0)}
                className="order-last w-full basis-full accent-primary"
                style={{background: `linear-gradient(90deg, #3B82F6 ${progress}%, var(--c-surface-container-highest) ${progress}%)`}}
            />
        </>
    );
};

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
    } = useApp();
    const {processVideoSSE, retryJob, fetchJobArtifactFile, getJob, saveTranscriptEdit, saveSummaryEdit, startJobVisualNote, getVisualNoteAvailability, getCredentialsStatus} = useApi();
    const {loadSettings} = useSettings();
    const [regenerating, setRegenerating] = useState(false);
    const [retranscribing, setRetranscribing] = useState(false);
    const [toast, setToast] = useState(null);
    const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false);
    const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false);
    const [visualEvidenceVisible, setVisualEvidenceVisible] = useState(true);
    const summaryRef = useRef(null);
    const writeNoteButtonRef = useRef(null);
    const retranscribeInputRef = useRef(null);
    const fallbackTaskIdRef = useRef(createTaskId());
    const hydratedTaskIdsRef = useRef(new Set());
    const transcriptSaveSeqRef = useRef(0);
    const summarySaveSeqRef = useRef(0);
    const summaryDraftResultKeyRef = useRef('');
    const playbackSaveRef = useRef(0);
    const mediaObjectUrlRef = useRef('');
    // Timers that must die with the page: a toast closing itself, the "done"
    // card clearing itself. A toast shown twice in a row used to be closed by
    // the first one's timer.
    const toastTimerRef = useRef(null);
    const doneJobTimerRef = useRef(null);
    useEffect(() => () => {
        if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
        if (doneJobTimerRef.current) clearTimeout(doneJobTimerRef.current);
    }, []);

    // The editor's address names its task (?task=<id>), so a refresh, the back
    // button or a bookmark reopens the same result. ?review=cut also opens the
    // cut's processing report, for the records page's "有 N 处需要你判断".
    const [searchParams, setSearchParams] = useSearchParams();
    const urlTaskId = searchParams.get('task') || '';
    const urlWantsCutReview = searchParams.get('review') === 'cut';
    const [urlTask, setUrlTask] = useState({id: '', state: 'idle'});
    const urlTaskIdRef = useRef(urlTaskId);
    urlTaskIdRef.current = urlTaskId;
    useEffect(() => {
        if (!urlTaskId) return;
        // The address and the open result agree: nothing to open, and a later
        // switch to another result moves the address instead of reopening this.
        if (lastResult?.task_id === urlTaskId) {
            if (urlTask.id !== urlTaskId || urlTask.state !== 'loaded') setUrlTask({id: urlTaskId, state: 'loaded'});
            return;
        }
        if (urlTask.id === urlTaskId) return;
        setUrlTask({id: urlTaskId, state: 'loading'});
        getJob(urlTaskId)
            .then((job) => {
                if (urlTaskIdRef.current !== urlTaskId) return;
                if (!job?.result) {
                    setUrlTask({id: urlTaskId, state: 'missing'});
                    return;
                }
                setLastResult({...job.result, task_id: job.result.task_id || urlTaskId});
                setNoteReadFor(urlTaskId);
                setUrlTask({id: urlTaskId, state: 'loaded'});
            })
            .catch(() => {
                if (urlTaskIdRef.current === urlTaskId) setUrlTask({id: urlTaskId, state: 'missing'});
            });
    }, [urlTaskId, lastResult?.task_id, urlTask, getJob, setLastResult]);
    const openingUrlTask = !!urlTaskId && lastResult?.task_id !== urlTaskId
        && (urlTask.id !== urlTaskId || urlTask.state === 'loading');
    const urlTaskMissing = !!urlTaskId && lastResult?.task_id !== urlTaskId && urlTask.id === urlTaskId && urlTask.state === 'missing';
    // The open result writes itself into the address, unless the address names
    // a task that is still being opened or could not be found.
    const openTaskId = lastResult?.task_id || '';
    useEffect(() => {
        if (!openTaskId || openTaskId === urlTaskId) return;
        if (urlTaskId && (urlTask.id !== urlTaskId || urlTask.state !== 'loaded')) return;
        setSearchParams((prev) => { const next = new URLSearchParams(prev); next.set('task', openTaskId); return next; }, {replace: true});
    }, [openTaskId, urlTaskId, urlTask, setSearchParams]);

    const result = lastResult;
    const matchedLocalSourceFile = localSourceFileMatchesResult(lastSourceFile, result) ? lastSourceFile : null;
    // Effects keyed on a derived string (resultKey, mediaSourceKey) read the
    // latest result through this ref: listing `result` itself as a dependency
    // would re-run them on every edit that syncs into the provider.
    const resultRef = useRef(result);
    resultRef.current = result;
    const matchedLocalSourceFileRef = useRef(matchedLocalSourceFile);
    matchedLocalSourceFileRef.current = matchedLocalSourceFile;
    const langRef = useRef(lang);
    langRef.current = lang;
    const tRef = useRef(t);
    tRef.current = t;
    // Memoised: it copies every sentence, and the page renders on each keystroke.
    const resultSegmentCount = useMemo(() => pickTranscriptSegments(result).length, [result]);
    const resultTextLength = (result?.transcript_text || '').length;
    const resultKey = result
        ? `${result.task_id || result.filename || 'current_result'}:${result.transcript_edited ? 'edited' : `${resultSegmentCount}:${resultTextLength}`}`
        : 'empty_result';
    const summaryResultKey = resultIdentity(result);
    const editTargetRef = useRef('');
    // Which file plays is part of the key: a result opened from recent activity
    // is a light copy without the cut file's record, and when the full copy
    // arrives the player must move to the cut file the transcript belongs to.
    const mediaSourceKey = result
        ? [
            result.task_id || result.filename || 'current_result',
            playbackMediaChoice(result)?.kind || 'no-preferred-media',
            result.artifacts?.playback_audio?.filename || (result.playback_audio_available ? 'playback-audio' : 'no-playback-audio'),
            result.source_file_available ? 'stored' : 'unstored',
            matchedLocalSourceFile ? `${matchedLocalSourceFile.name}:${matchedLocalSourceFile.size}:${matchedLocalSourceFile.lastModified || 0}` : 'no-local-file',
        ].join(':')
        : 'empty_media_source';
    const [editedSegments, setEditedSegments] = useState([]);
    // The segments the provider last received. Edit records are diffed from
    // these, not from every keystroke.
    const [syncedSegments, setSyncedSegments] = useState([]);
    const [editedTranscript, setEditedTranscript] = useState('');
    const [baselineSegments, setBaselineSegments] = useState([]);
    const [transcriptDirty, setTranscriptDirty] = useState(false);
    const [transcriptUnsaved, setTranscriptUnsaved] = useState(false);
    const [mediaUrl, setMediaUrl] = useState('');
    const [mediaKind, setMediaKind] = useState('audio');
    const [mediaLoading, setMediaLoading] = useState(false);
    const [mediaError, setMediaError] = useState('');
    const playbackClockRef = useRef(null);
    if (!playbackClockRef.current) playbackClockRef.current = createPlaybackClock();
    const playbackClock = playbackClockRef.current;
    const [mediaDuration, setMediaDuration] = useState(0);
    const [mediaPlaying, setMediaPlaying] = useState(false);
    const [followPlayback, setFollowPlayback] = useState(true);
    const [transcriptReviewMode, setTranscriptReviewMode] = useState('text');
    const [transcriptSaveStatus, setTranscriptSaveStatus] = useState('idle');
    const [summaryDraft, setSummaryDraft] = useState('');
    // A table cell open in the note editor holds typing the page has not been
    // told about yet. Like an unsaved edit, it makes a newer copy of the note
    // wait: the poll skips a tick, and loading the latest note after a refused
    // save waits until the cell is left.
    const noteCellOpenRef = useRef(false);
    const deferredNoteReloadRef = useRef(null);
    const handleNoteEditingChange = useCallback((open) => {
        noteCellOpenRef.current = open;
        const reload = deferredNoteReloadRef.current;
        if (open || !reload) return;
        deferredNoteReloadRef.current = null;
        reload();
    }, []);
    const [summaryUnsaved, setSummaryUnsaved] = useState(false);
    const [summarySaveStatus, setSummarySaveStatus] = useState('idle');
    const [hydratingResult, setHydratingResult] = useState(false);
    // The task whose full note this page has read from the service. Until then
    // the note on the page may be the task list's or the browser cache's first
    // 240 characters, which must be neither edited nor saved: on 2026-10-22 the
    // page saved that preview over a 40,000-character note.
    const [noteReadFor, setNoteReadFor] = useState('');
    const noteReadForRef = useRef(noteReadFor);
    noteReadForRef.current = noteReadFor;
    const [hydrationFailed, setHydrationFailed] = useState(false);
    const [transcriptView, setTranscriptView] = useState('bilingual');
    const [editRecordsOpen, setEditRecordsOpen] = useState(false);
    const [splitRatio, setSplitRatio] = useState(loadSplitRatio);
    const mediaRef = useRef(null);
    const mediaInputRef = useRef(null);
    const splitContainerRef = useRef(null);
    const transcriptScrollRef = useRef(null);
    const segmentRefs = useRef({});
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

    useEffect(() => {
        const result = resultRef.current;
        if (!result?.task_id || transcriptUnsaved) {
            setHydratingResult(false);
            setHydrationFailed(false);
            return;
        }
        if (isLocalHistoryResult(result)) {
            setHydratingResult(false);
            setHydrationFailed(false);
            return;
        }
        const currentSegments = pickTranscriptSegments(result);
        const currentText = result.transcript_text || '';
        const noteUnread = noteReadForRef.current !== result.task_id;
        const needsHydration = currentSegments.length === 0 || currentText.length <= 260 || noteUnread;
        if (!needsHydration || (hydratedTaskIdsRef.current.has(result.task_id) && !noteUnread)) {
            setHydratingResult(false);
            if (!needsHydration) setHydrationFailed(false);
            return;
        }
        setHydratingResult(true);
        setHydrationFailed(false);
        let cancelled = false;
        getJob(result.task_id, resultJobOptions)
            .then((job) => {
                const full = job?.result;
                if (cancelled || !full) return;
                const fullSegments = pickTranscriptSegments(full);
                const fullText = full.transcript_text || '';
                const currentDisplayCount = pickDisplayTranscriptSegments(result, currentSegments).length;
                const fullDisplayCount = pickDisplayTranscriptSegments(full, fullSegments).length;
                // The service's transcript replaces the page's when it is
                // longer, or when it is a different version: a copy from the
                // browser cache carries no version and may predate an edit made
                // since (a fixed typo is no longer). This read happens only
                // with no unsaved edit of the page's own.
                const serverVersionDiffers = !!full.transcript_version && full.transcript_version !== result.transcript_version;
                if (serverVersionDiffers || fullSegments.length > currentSegments.length || fullText.length > currentText.length || fullDisplayCount > currentDisplayCount) {
                    const fullBaselineSegments = pickTranscriptBaselineSegments(full);
                    setLastResult(full);
                    setEditedSegments(fullSegments.map((seg) => ({...seg})));
                    setSyncedSegments(fullSegments);
                    setEditedTranscript(composeTranscriptText(fullSegments, fullText));
                    setBaselineSegments((prev) => {
                        if (fullBaselineSegments.length > 0) return fullBaselineSegments.map((seg) => ({...seg}));
                        if (full.transcript_edited && prev.length > 0) return prev;
                        return fullSegments.map((seg) => ({...seg}));
                    });
                    setTranscriptDirty(!!full.transcript_edited);
                    setTranscriptSaveStatus(full.transcript_edited ? 'saved' : 'idle');
                } else {
                    // The transcript on the page is already whole; the note
                    // and its version are taken from the service.
                    setLastResult((prev) => (prev?.task_id === result.task_id ? mergeNoteFields(prev, full) : prev));
                }
                hydratedTaskIdsRef.current.add(result.task_id);
                setNoteReadFor(result.task_id);
            })
            .catch(() => {
                if (!cancelled) setHydrationFailed(true);
            })
            .finally(() => {
                if (!cancelled) setHydratingResult(false);
            });
        return () => { cancelled = true; };
    }, [resultKey, transcriptUnsaved, resultJobOptions, getJob, setLastResult]);

    useEffect(() => {
        const result = resultRef.current;
        if (!result) {
            setEditedSegments([]);
            setSyncedSegments([]);
            setEditedTranscript('');
            setBaselineSegments([]);
            setTranscriptDirty(false);
            setTranscriptUnsaved(false);
            return;
        }
        // Local edits waiting to sync for this very result must not be replaced
        // by the result's older copy; a different result opened meanwhile is
        // shown as it is.
        if (transcriptUnsaved && editTargetRef.current === resultIdentity(result)) return;
        const sourceSegments = pickTranscriptSegments(result);
        const baselineSourceSegments = pickTranscriptBaselineSegments(result);
        const sourceText = result.transcript_text || '';
        setEditedSegments(sourceSegments.map((seg) => ({...seg})));
        setSyncedSegments(sourceSegments);
        setEditedTranscript(composeTranscriptText(sourceSegments, sourceText));
        setBaselineSegments((prev) => {
            if (baselineSourceSegments.length > 0) return baselineSourceSegments.map((seg) => ({...seg}));
            if (result.transcript_edited && prev.length > 0) return prev;
            return sourceSegments.map((seg) => ({...seg}));
        });
        setTranscriptDirty(!!result.transcript_edited);
        setTranscriptUnsaved(false);
        setTranscriptSaveStatus(result.transcript_edited ? 'saved' : 'idle');
    }, [resultKey, transcriptUnsaved]);

    useEffect(() => {
        const result = resultRef.current;
        if (!result) {
            setSummaryDraft('');
            setSummaryUnsaved(false);
            setSummarySaveStatus('idle');
            summaryDraftResultKeyRef.current = 'empty_result';
            return;
        }
        if (summaryDraftResultKeyRef.current !== summaryResultKey) {
            summaryDraftResultKeyRef.current = summaryResultKey;
            setSummaryDraft(result.summary_markdown || '');
            setSummaryUnsaved(false);
            setSummarySaveStatus(result.summary_edited ? 'saved' : 'idle');
            return;
        }
        if (summaryUnsaved) return;
        setSummaryDraft(result.summary_markdown || '');
        setSummarySaveStatus(result.summary_edited ? 'saved' : 'idle');
    }, [summaryResultKey, result?.summary_markdown, result?.summary_edited, summaryUnsaved]);

    // A keystroke changes this page's own state and nothing else. Diffing every
    // segment and pushing the whole result into the provider (which re-renders
    // the entire app tree) happens once the typing pauses, in the sync effect
    // below, together with the save.
    const applyTranscriptEdit = useCallback((nextSegments, nextText) => {
        const current = resultRef.current;
        if (!current) return;
        editTargetRef.current = resultIdentity(current);
        setEditedSegments(nextSegments);
        setEditedTranscript(nextText);
        setTranscriptDirty(true);
        setTranscriptUnsaved(true);
        setTranscriptSaveStatus(current.task_id ? 'saving' : 'failed');
    }, []);

    const handleSegmentTextChange = (index, text) => {
        const nextSegments = editedSegments.map((seg, i) => i === index ? {...seg, text} : seg);
        applyTranscriptEdit(nextSegments, composeTranscriptText(nextSegments, editedTranscript));
    };

    const handlePlainTranscriptChange = (text) => {
        applyTranscriptEdit([], text);
    };

    const noteEditableRef = useRef(false);
    const handleSummaryChange = useCallback((text) => {
        const result = resultRef.current;
        // Only a change made to the full note counts; anything the editor
        // reports before that (a preview, a load) is not the user's.
        if (!noteEditableRef.current) return;
        setSummaryDraft(text);
        setSummaryUnsaved(true);
        setSummarySaveStatus(result?.task_id ? 'saving' : 'local');
        if (!result) return;
        setLastResult({
            ...result,
            summary_markdown: text,
            summary_skipped: false,
            summary_status: text.trim() ? 'completed' : result.summary_status || 'completed',
            summary_error: null,
            summary_edited: true,
            summary_edited_at: new Date().toISOString(),
        });
    }, [setLastResult]);

    const summaryMarkdownForEditor = summaryUnsaved
        ? summaryDraft
        : (summaryDraft || result?.summary_markdown || '');

    const replaceMediaUrl = useCallback((nextUrl = '') => {
        const previousUrl = mediaObjectUrlRef.current;
        if (previousUrl && previousUrl !== nextUrl) URL.revokeObjectURL(previousUrl);
        mediaObjectUrlRef.current = nextUrl;
        setMediaUrl(nextUrl);
    }, []);

    const loadMediaFile = useCallback((file) => {
        if (!file) return;
        const url = URL.createObjectURL(file);
        setMediaKind(isLikelyVideoFile(file) ? 'video' : 'audio');
        replaceMediaUrl(url);
        setMediaError('');
        setMediaLoading(false);
    }, [replaceMediaUrl]);

    // The player reads the task's media straight from the service, which answers
    // range requests, so playback starts at once and only what is played is read.
    // Fetching the whole file into the browser first meant waiting for, and
    // holding in memory, a recording of several gigabytes before the first second
    // played. One byte is asked for first so a missing file still falls through
    // to the next choice the way a failed fetch did.
    const openJobMedia = useCallback(async (url, name) => {
        const probe = await probeMedia(apiFetch, url).catch(() => null);
        if (!probe?.ok) throw new Error(lang === 'zh' ? '读不到这个文件' : 'File not found');
        const type = probe.headers.get('content-type') || '';
        try { await probe.body?.cancel?.(); } catch (_) { /* nothing left to read */ }
        const kind = type.startsWith('video/') || isLikelyVideoFile(name) ? 'video' : 'audio';
        return () => {
            setMediaKind(kind);
            replaceMediaUrl(url);
            setMediaError('');
            setMediaLoading(false);
        };
    }, [replaceMediaUrl, lang]);
    const jobSourceUrl = (taskId) => `${API_BASE}/jobs/${encodeURIComponent(taskId)}/source`;
    const jobArtifactUrl = (taskId, kind) => `${API_BASE}/jobs/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(kind)}`;

    useEffect(() => {
        let cancelled = false;
        const result = resultRef.current;
        const matchedLocalSourceFile = matchedLocalSourceFileRef.current;
        const lang = langRef.current;
        replaceMediaUrl('');
        setMediaError('');
        setMediaLoading(false);
        setMediaKind('audio');
        if (!result) return () => { cancelled = true; };
        if (matchedLocalSourceFile) {
            loadMediaFile(matchedLocalSourceFile);
            return () => { cancelled = true; };
        }
        // The file this transcript belongs to, which for a task that was cut
        // before transcription is the shortened one. Loading the recording instead
        // would leave every seek and every highlighted line drifting further out
        // the longer it plays, with nothing on screen saying why.
        const preferred = playbackMediaChoice(result);
        // Same rule one step earlier: if this task's material is the cut file and
        // there is no cut file to load, nothing else may take its place — not the
        // recording, not the saved audio. The bar at the top says why.
        if (isAutoCutFlow(result) && !preferred?.isCut) {
            setMediaError(lang === 'zh' ? '剪后文件读不到了' : 'The cut file cannot be read');
            setMediaLoading(false);
            return () => { cancelled = true; };
        }
        if (preferred?.isCut) {
            setMediaLoading(true);
            openJobMedia(jobArtifactUrl(result.task_id, preferred.kind), preferred.filename)
                .then((play) => { if (!cancelled) play(); })
                .catch((cutErr) => {
                    if (cancelled) return;
                    // No falling back to the recording when the transcript came from
                    // the cut file. The original is longer, so every line and every
                    // seek would point at the wrong moment while the page looked
                    // perfectly normal. Say it is unavailable instead.
                    if (!isAutoCutFlow(result) && result.source_file_available) {
                        openJobMedia(jobSourceUrl(result.task_id), result.filename || 'source')
                            .then((play) => { if (!cancelled) play(); })
                            .catch((sourceErr) => {
                                if (!cancelled) {
                                    setMediaError(sourceErr.message || cutErr.message || 'Source file unavailable');
                                    setMediaLoading(false);
                                }
                            });
                        return;
                    }
                    setMediaError(cutErr.message || 'Cut file unavailable');
                    setMediaLoading(false);
                });
            return () => { cancelled = true; };
        }
        if (result.task_id && result.source_file_available) {
            setMediaLoading(true);
            openJobMedia(jobSourceUrl(result.task_id), result.filename || 'source')
                .then((play) => { if (!cancelled) play(); })
                .catch((sourceErr) => {
                    if (!cancelled && result.artifacts?.playback_audio) {
                        const playbackArtifact = result.artifacts.playback_audio;
                        openJobMedia(jobArtifactUrl(result.task_id, 'playback_audio'), playbackArtifact.filename || `${result.filename || 'source'}_audio.mp3`)
                            .then((play) => { if (!cancelled) play(); })
                            .catch((err) => {
                                if (!cancelled) {
                                    setMediaError(err.message || sourceErr.message || 'Source file unavailable');
                                    setMediaLoading(false);
                                }
                            });
                        return;
                    }
                    if (!cancelled) {
                        setMediaError(sourceErr.message || 'Source file unavailable');
                        setMediaLoading(false);
                    }
                });
            return () => { cancelled = true; };
        }
        const playbackArtifact = result.artifacts?.playback_audio;
        if (result.task_id && playbackArtifact) {
            setMediaLoading(true);
            openJobMedia(jobArtifactUrl(result.task_id, 'playback_audio'), playbackArtifact.filename || `${result.filename || 'source'}_audio.mp3`)
                .then((play) => { if (!cancelled) play(); })
                .catch((err) => {
                    if (!cancelled && result.source_file_available) {
                        openJobMedia(jobSourceUrl(result.task_id), result.filename || 'source')
                            .then((play) => { if (!cancelled) play(); })
                            .catch((sourceErr) => {
                                if (!cancelled) {
                                    setMediaError(sourceErr.message || err.message || 'Audio file unavailable');
                                    setMediaLoading(false);
                                }
                            });
                        return;
                    }
                    if (!cancelled) {
                        setMediaError(err.message || 'Audio file unavailable');
                        setMediaLoading(false);
                    }
            });
            return () => { cancelled = true; };
        }
        return () => { cancelled = true; };
    }, [mediaSourceKey, replaceMediaUrl, loadMediaFile, openJobMedia]);

    const segments = editedSegments;
    const transcript = editedTranscript || result?.transcript_text || '';
    const resultEditRecords = result?.transcript_edit_records;
    const editRecords = useMemo(
        () => buildTranscriptEditRecords(baselineSegments, syncedSegments, {transcript_edit_records: resultEditRecords}),
        [baselineSegments, syncedSegments, resultEditRecords]
    );
    const visibleEditRecords = useMemo(
        () => filterVisibleEditRecords(editRecords, syncedSegments),
        [editRecords, syncedSegments],
    );
    const summary = summaryMarkdownForEditor;
    const hasEditableSummary = !!result && (
        !!summary
        || summaryUnsaved
        || result?.summary_status === 'completed'
        || !!result?.summary_edited
    );
    // Editable once the page has the task's full note: read from the service
    // here, or a result with nothing on the service to read (no task, or one
    // kept only in this browser).
    const noteReady = !!result && (!result.task_id || isLocalHistoryResult(result) || noteReadFor === result.task_id);
    noteEditableRef.current = noteReady;
    const summarySaveLabel = summarySaveStatus === 'saving'
        ? (lang === 'zh' ? '保存中' : 'Saving')
        : summarySaveStatus === 'saved'
            ? (lang === 'zh' ? '已保存' : 'Saved')
            : summarySaveStatus === 'failed'
                ? (lang === 'zh' ? '保存失败' : 'Save failed')
                : summarySaveStatus === 'local'
                    ? (lang === 'zh' ? '本页已修改' : 'Edited locally')
                    : '';
    const inlineVisualEvidenceCount = (summary.match(/(^|\n)\s*!\[[^\]]+\]\([^)]+\)\s*(?=\n|$)/g) || []).length;
    const hasInlineVisualEvidence = inlineVisualEvidenceCount > 0;
    const renderedSummary = useMemo(
        () => simpleMd(summary, {renderImages: !hasInlineVisualEvidence || visualEvidenceVisible}),
        [summary, hasInlineVisualEvidence, visualEvidenceVisible]
    );
    const displayTranscriptSegments = useMemo(() => pickDisplayTranscriptSegments(result, segments), [result, segments]);
    const bilingualTranscriptSegments = useMemo(
        () => displayTranscriptSegments.filter((seg) => String(seg.text_zh || '').trim()),
        [displayTranscriptSegments],
    );
    const hasBilingualTranscript = bilingualTranscriptSegments.length > 0;
    const visibleTranscriptView = hasBilingualTranscript && transcriptView !== 'raw' ? 'bilingual' : 'raw';
    const visibleTranscriptSegments = visibleTranscriptView === 'bilingual' ? bilingualTranscriptSegments : segments;
    const isTranscriptHydrationPending = !!result?.task_id
        && !transcriptUnsaved
        && !isLocalHistoryResult(result)
        && segments.length === 0
        && (result.transcript_text || '').length > 0
        && !hydrationFailed
        && (!hydratedTaskIdsRef.current.has(result.task_id) || hydratingResult);
    const isTranscriptHydrationFailed = !!result?.task_id
        && !transcriptUnsaved
        && !isLocalHistoryResult(result)
        && segments.length === 0
        && (result.transcript_text || '').length > 0
        && hydrationFailed;
    const canUseStoredSource = !!result?.source_file_available && !!result?.task_id;
    const canUsePlaybackAudio = !!result?.artifacts?.playback_audio && !!result?.task_id;
    const canRetranscribeStoredMedia = !!matchedLocalSourceFile || canUseStoredSource || canUsePlaybackAudio;
    const retranscribeSourceLabel = canRetranscribeStoredMedia
        ? ((matchedLocalSourceFile || canUseStoredSource)
            ? (lang === 'zh' ? '原文件' : 'Source file')
            : (lang === 'zh' ? '已保存音频' : 'Saved audio'))
        : (lang === 'zh' ? '当前结果' : 'Current result');
    const retranscribeSourceName = matchedLocalSourceFile?.name
        || (canUseStoredSource ? result?.filename : result?.artifacts?.playback_audio?.filename)
        || result?.filename
        || t('edit.title');
    const durSec = result?.audio_duration_seconds || 0;
    const activeTaskId = result?.task_id || fallbackTaskIdRef.current;
    const playbackMemoryKey = result ? `fluentflow_playback_position_${activeTaskId}` : '';
    const summaryFailureHint = summaryFailureNextStep(result, lang);
    const resultTitle = resultDisplayTitle(result, {name: t('edit.title')});
    const rawEditorTitle = resultTitle || result?.filename || t('edit.title');
    const playbackDuration = mediaDuration || durSec || 0;
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
    const [cutReviewOpen, setCutReviewOpen] = useState(false);
    // Arriving with ?review=cut opens the report once the named task is open,
    // and the parameter is dropped so a refresh does not open it again.
    useEffect(() => {
        if (!urlWantsCutReview || !result?.task_id || result.task_id !== urlTaskId) return;
        setCutReviewOpen(true);
        setSearchParams((prev) => { const next = new URLSearchParams(prev); next.delete('review'); return next; }, {replace: true});
    }, [urlWantsCutReview, result?.task_id, urlTaskId, setSearchParams]);
    // Answers given in the report change how many places still wait, so the
    // task's cut record is read again when the report closes.
    const closeCutReview = () => {
        setCutReviewOpen(false);
        const taskId = result?.task_id;
        if (!taskId) return;
        getJob(taskId, resultJobOptions)
            .then((job) => {
                const fresh = job?.result;
                if (!fresh) return;
                setLastResult((prev) => (prev?.task_id === taskId ? {...prev, debreath: fresh.debreath} : prev));
            })
            .catch(() => {});
    };
    const canShowVideoReview = isVideoResultSource(result, matchedLocalSourceFile);
    const canUseVideoReview = canShowVideoReview && mediaKind === 'video' && !!mediaUrl && segments.length > 0;
    const activeReviewMode = canUseVideoReview ? transcriptReviewMode : 'text';
    const shouldShowVideoReview = shouldKeepVideoReviewMounted({activeReviewMode});
    // The sentence playing now, worked out on each tick of the player but
    // stored only when it changes, so the page re-renders once per sentence.
    const [activeSegmentIndex, setActiveSegmentIndex] = useState(-1);
    useEffect(() => {
        const follow = (time) => {
            const index = findSegmentAt(visibleTranscriptSegments, time);
            setActiveSegmentIndex((previous) => (previous === index ? previous : index));
        };
        follow(playbackClock.get());
        return playbackClock.subscribe(follow);
    }, [playbackClock, visibleTranscriptSegments]);
    const updateMediaCurrentTime = useCallback((time, options={}) => {
        const next = Math.max(0, Number(time) || 0);
        playbackClock.set(next);
        if (options.persist === false || !playbackMemoryKey) return;
        const now = Date.now();
        if (now - playbackSaveRef.current < 900 && !options.force) return;
        playbackSaveRef.current = now;
        try {
            localStorage.setItem(playbackMemoryKey, JSON.stringify({
                time: next,
                duration: Number(options.duration ?? playbackDuration) || 0,
                updatedAt: now,
            }));
        } catch(_) {}
    }, [playbackClock, playbackDuration, playbackMemoryKey]);
    const restoreMediaPosition = useCallback((media, duration) => {
        const nextDuration = Number(duration) || 0;
        setMediaDuration(nextDuration || durSec || 0);
        if (!media || !playbackMemoryKey) {
            updateMediaCurrentTime(media?.currentTime || 0, {persist: false});
            return;
        }
        try {
            const saved = JSON.parse(localStorage.getItem(playbackMemoryKey) || '{}');
            const savedTime = Number(saved?.time);
            const safeDuration = nextDuration || Number(saved?.duration) || durSec || 0;
            if (Number.isFinite(savedTime) && savedTime > 1 && (!safeDuration || savedTime < safeDuration - 1)) {
                media.currentTime = savedTime;
                updateMediaCurrentTime(savedTime, {persist: false});
                return;
            }
        } catch(_) {}
        updateMediaCurrentTime(media.currentTime || 0, {persist: false});
    }, [durSec, playbackMemoryKey, updateMediaCurrentTime]);
    const seekMediaTo = useCallback((time, {follow=true}={}) => {
        const media = mediaRef.current;
        const duration = playbackDuration || media?.duration || 0;
        const next = Math.max(0, Math.min(Number(time) || 0, duration || Number(time) || 0));
        if (media) media.currentTime = next;
        updateMediaCurrentTime(next, {duration, force: true});
        if (follow) setFollowPlayback(true);
    }, [playbackDuration, updateMediaCurrentTime]);

    const persistMediaPosition = useCallback(() => {
        const media = mediaRef.current;
        if (!media || !playbackMemoryKey) return;
        const next = Math.max(0, Number(media.currentTime) || 0);
        try {
            localStorage.setItem(playbackMemoryKey, JSON.stringify({
                time: next,
                duration: Number(media.duration) || playbackDuration || 0,
                updatedAt: Date.now(),
            }));
        } catch(_) {}
    }, [playbackDuration, playbackMemoryKey]);

    useEffect(() => {
        const followIndex = activeSegmentIndex;
        if (!followPlayback || followIndex < 0 || !mediaPlaying) return;
        const node = segmentRefs.current[followIndex];
        if (node) node.scrollIntoView({block:'center', behavior:'smooth'});
    }, [activeSegmentIndex, followPlayback, mediaPlaying]);

    // Switching between text review and video review keeps the reader's
    // place: while playing, the sentence playing is brought into view;
    // otherwise the sentence at the top of the list stays at the top.
    const reviewAnchorRef = useRef({index: -1, block: 'start'});
    const firstVisibleLine = () => {
        const box = transcriptScrollRef.current;
        if (!box) return -1;
        const top = box.getBoundingClientRect().top;
        let lo = 0;
        let hi = visibleTranscriptSegments.length - 1;
        let found = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const node = segmentRefs.current[mid];
            if (!node) return -1;
            if (node.getBoundingClientRect().bottom > top) { found = mid; hi = mid - 1; } else { lo = mid + 1; }
        }
        return found;
    };
    const switchReviewMode = (mode) => {
        persistMediaPosition();
        reviewAnchorRef.current = mediaPlaying
            ? {index: activeSegmentIndex, block: 'center'}
            : {index: firstVisibleLine(), block: 'start'};
        setTranscriptReviewMode(mode);
    };
    const reviewModeSeenRef = useRef(activeReviewMode);
    useEffect(() => {
        if (reviewModeSeenRef.current === activeReviewMode) return;
        reviewModeSeenRef.current = activeReviewMode;
        const {index, block} = reviewAnchorRef.current;
        const node = segmentRefs.current[index];
        if (node) node.scrollIntoView({block});
    }, [activeReviewMode]);

    // Only for a browser that cannot size the boxes with CSS: all of them in
    // one pass when a result, a view or the number of sentences changes. Not
    // on every saved edit: the page re-measured 7104 boxes after the first
    // autosave and froze for eleven seconds.
    useEffect(() => {
        if (SIZES_ITSELF) return;
        const root = transcriptScrollRef.current;
        if (!root) return;
        sizeTextareas(root.querySelectorAll('textarea[data-transcript-segment="true"]'));
    }, [summaryResultKey, visibleTranscriptView, activeReviewMode, segments.length]);

    // Stable handlers for the rows, reading the latest page state through refs.
    const rowHandlersRef = useRef({});
    rowHandlersRef.current = {seekToSegment: (seg) => seekToSegment(seg), handleSegmentTextChange: (i, text) => handleSegmentTextChange(i, text)};
    const seekRow = useCallback((seg) => rowHandlersRef.current.seekToSegment(seg), []);
    const editRow = useCallback((index, text) => rowHandlersRef.current.handleSegmentTextChange(index, text), []);
    const stopFollowing = useCallback(() => setFollowPlayback(false), []);
    const registerRow = useCallback((index, node) => { if (node) segmentRefs.current[index] = node; }, []);

    useEffect(() => {
        const persistBeforeBackground = () => {
            if (document.visibilityState === 'hidden') persistMediaPosition();
        };
        window.addEventListener('pagehide', persistMediaPosition);
        document.addEventListener('visibilitychange', persistBeforeBackground);
        return () => {
            persistMediaPosition();
            window.removeEventListener('pagehide', persistMediaPosition);
            document.removeEventListener('visibilitychange', persistBeforeBackground);
        };
    }, [persistMediaPosition]);

    useEffect(() => () => {
        const activeUrl = mediaObjectUrlRef.current;
        if (activeUrl) URL.revokeObjectURL(activeUrl);
        mediaObjectUrlRef.current = '';
    }, []);

    // The version of the note and of the transcript a save must name: the
    // latest the page knows, from the task as read or from the answer to the
    // page's own last save. Saves of one kind go out one at a time, each
    // reading this when it is sent; a save sent while the previous was still
    // on its way used to name the version before it and be refused as stale,
    // and the page reloaded over what had just been typed.
    const contentVersionRef = useRef({taskId: '', note: null, transcript: null});
    const resultTaskId = result?.task_id || '';
    const resultNoteVersion = result?.note_version || null;
    const resultTranscriptVersion = result?.transcript_version || null;
    useEffect(() => {
        contentVersionRef.current = {taskId: resultTaskId, note: resultNoteVersion, transcript: resultTranscriptVersion};
    }, [resultTaskId, resultNoteVersion, resultTranscriptVersion]);
    const takeSavedVersions = (taskId, saved) => {
        if (!saved || contentVersionRef.current.taskId !== taskId) return;
        contentVersionRef.current = {
            taskId,
            note: saved.note_version || contentVersionRef.current.note,
            transcript: saved.transcript_version || contentVersionRef.current.transcript,
        };
    };
    const noteSaveQueueRef = useRef(Promise.resolve());
    const transcriptSaveQueueRef = useRef(Promise.resolve());

    // After 800ms without a keystroke: diff the segments against the baseline,
    // hand the edited result to the provider, and save it. One timer for both,
    // so the app tree re-renders once per pause instead of once per key.
    useEffect(() => {
        if (!transcriptUnsaved) return;
        const seq = ++transcriptSaveSeqRef.current;
        const timer = setTimeout(() => {
            const current = resultRef.current;
            // The page moved on to another result while this edit waited.
            if (!current || resultIdentity(current) !== editTargetRef.current) return;
            const nextEditRecords = buildTranscriptEditRecords(baselineSegments, segments, current);
            const updated = {
                ...current,
                segments,
                transcript_text: transcript,
                transcript_edit_records: nextEditRecords,
                transcript_edit_record_count: nextEditRecords.length,
                transcript_edited: true,
                transcript_edited_at: new Date().toISOString(),
            };
            setSyncedSegments(segments);
            setLastResult(updated);
            if (!current.task_id) {
                setTranscriptSaveStatus('failed');
                return;
            }
            setTranscriptSaveStatus('saving');
            const send = () => saveTranscriptEdit(current.task_id, {
                transcript_text: transcript,
                segments,
                edit_records: filterVisibleEditRecords(nextEditRecords, segments),
                base_version: contentVersionRef.current.taskId === current.task_id
                    ? contentVersionRef.current.transcript
                    : current.transcript_version,
            }, resultJobOptions);
            const saving = transcriptSaveQueueRef.current.then(send, send);
            transcriptSaveQueueRef.current = saving.catch(() => {});
            saving
                .then((data) => {
                    // The new version counts whichever save is the latest.
                    takeSavedVersions(current.task_id, data?.result);
                    if (data?.result) {
                        const {transcript_version: savedVersion} = data.result;
                        setLastResult((prev) => (
                            prev?.task_id === current.task_id && savedVersion
                                ? {...prev, transcript_version: savedVersion}
                                : prev
                        ));
                    }
                    if (seq !== transcriptSaveSeqRef.current) return;
                    setTranscriptUnsaved(false);
                    setTranscriptDirty(true);
                    setTranscriptSaveStatus('saved');
                    if (data?.result) {
                        setLastResult((prev) => (
                            prev?.task_id === current.task_id
                                ? {...prev, ...data.result}
                                : prev
                        ));
                    }
                })
                .catch((err) => {
                    if (seq !== transcriptSaveSeqRef.current) return;
                    if (err?.stale) {
                        reloadAfterStaleSave(current.task_id, err.message, () => setTranscriptUnsaved(false));
                        return;
                    }
                    setTranscriptSaveStatus('failed');
                });
        }, 800);
        return () => clearTimeout(timer);
    }, [transcriptUnsaved, transcript, segments, baselineSegments, resultJobOptions, saveTranscriptEdit, setLastResult]);

    useEffect(() => {
        if (!summaryUnsaved) return;
        if (!result?.task_id) {
            setSummarySaveStatus('local');
            return;
        }
        // A save always names the version of the note it was made on. A copy
        // without one is not the task's note (the list's preview is such a
        // copy), and the service would take an unversioned save as it is.
        if (!result.note_version) {
            setSummarySaveStatus('failed');
            return;
        }
        const seq = ++summarySaveSeqRef.current;
        setSummarySaveStatus('saving');
        const taskId = result.task_id;
        const timer = setTimeout(() => {
            const send = () => saveSummaryEdit(taskId, {
                summary_markdown: summaryDraft,
                base_version: contentVersionRef.current.taskId === taskId
                    ? contentVersionRef.current.note
                    : result.note_version,
            }, resultJobOptions);
            const saving = noteSaveQueueRef.current.then(send, send);
            noteSaveQueueRef.current = saving.catch(() => {});
            saving
                .then((data) => {
                    // The new version counts whichever save is the latest.
                    takeSavedVersions(taskId, data?.result);
                    if (data?.result?.note_version) {
                        const savedVersion = data.result.note_version;
                        setLastResult((prev) => (prev?.task_id === taskId ? {...prev, note_version: savedVersion} : prev));
                    }
                    if (seq !== summarySaveSeqRef.current) return;
                    setSummaryUnsaved(false);
                    setSummarySaveStatus('saved');
                    if (data?.result) {
                        setLastResult((prev) => (
                            prev?.task_id === result.task_id
                                ? {...prev, ...data.result}
                                : prev
                        ));
                    }
                })
                .catch((err) => {
                    if (seq !== summarySaveSeqRef.current) return;
                    if (err?.stale) {
                        const reload = () => reloadAfterStaleSave(result.task_id, err.message, () => setSummaryUnsaved(false));
                        if (noteCellOpenRef.current) deferredNoteReloadRef.current = reload;
                        else reload();
                        return;
                    }
                    setSummarySaveStatus('failed');
                });
        }, 800);
        return () => clearTimeout(timer);
    }, [result?.task_id, summaryUnsaved, summaryDraft, resultJobOptions, saveSummaryEdit, setLastResult]);

    const seekToSegment = (seg) => {
        if (seg?.start == null) return;
        seekMediaTo(Number(seg.start) || 0);
    };

    const togglePlayback = () => {
        const media = mediaRef.current;
        if (!media) return;
        if (media.paused) media.play().catch((err) => setMediaError(err.message || 'Playback failed'));
        else media.pause();
    };

    const showToast = useCallback((msg, ok=true) => {
        setToast({msg, ok});
        if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
        toastTimerRef.current = setTimeout(() => {
            toastTimerRef.current = null;
            setToast(null);
        }, 3000);
    }, []);
    // The server refused a save made on an older copy (the note or subtitles
    // changed in Obsidian, by an agent or in another tab): load what the task
    // holds now and say so, rather than sending the old copy again.
    const reloadAfterStaleSave = useCallback((taskId, message, clearUnsaved) => {
        const done = (job) => {
            if (job?.result) setLastResult(job.result);
            clearUnsaved();
            showToast(message || '内容在别处被改过，已换成最新的版本。', false);
        };
        getJob(taskId, resultJobOptionsRef.current).then(done).catch(() => done(null));
    }, [getJob, setLastResult, showToast]);
    const buildAiOptions = (settings) => ({
        noteMode: settings.noteMode||'auto',
        generateVisuals: !!settings.autoIllustrate,
        sttProvider: effectiveSttProvider(settings, runtimeConfig),
    });


    // Who writes a regenerated note: the same answer the start page gives
    // (lib/noteWriter.js), so "重写笔记" uses the writer that wrote the note.
    const [credentialStatus, setCredentialStatus] = useState(null);
    useEffect(() => {
        let active = true;
        getCredentialsStatus?.()
            .then((status) => { if (active && status) setCredentialStatus(status); })
            .catch(() => {});
        return () => { active = false; };
    }, [getCredentialsStatus]);
    const noteWriter = useMemo(
        () => resolveNoteWriter({runtimeConfig, credentialStatus, settings: loadSettings()}),
        // loadSettings reads localStorage; stored settings only change on the
        // settings page, which is a different route.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [runtimeConfig, credentialStatus],
    );

    // A note still being written — the automatic one after transcription, or a
    // Claude rewrite started from this page — is fetched again every few
    // seconds until it settles, then shown. `noteWatch` is the rewrite this page
    // started: {taskId, requestedAtMs, sawRunning}.
    const [noteWatch, setNoteWatch] = useState(null);
    const noteWatchRef = useRef(noteWatch);
    noteWatchRef.current = noteWatch;
    const summaryUnsavedRef = useRef(summaryUnsaved);
    summaryUnsavedRef.current = summaryUnsaved;
    const resultJobOptionsRef = useRef(resultJobOptions);
    resultJobOptionsRef.current = resultJobOptions;
    // After the note, an automatic Feishu export may still be on its way:
    // {taskId, since} while the page waits for its link or its failure.
    const [exportWatch, setExportWatch] = useState(null);
    const exportWatchRef = useRef(exportWatch);
    exportWatchRef.current = exportWatch;
    const pollTaskId = result?.task_id || '';
    const notePolling = !!pollTaskId && (
        noteIsBeingWritten(result) || noteWatch?.taskId === pollTaskId || exportWatch?.taskId === pollTaskId
    );
    useEffect(() => {
        // A rewrite started for a task that is no longer open is no longer this
        // page's to report.
        if (noteWatchRef.current && noteWatchRef.current.taskId !== pollTaskId) {
            setNoteWatch(null);
            setRegenerating(false);
        }
        if (exportWatchRef.current && exportWatchRef.current.taskId !== pollTaskId) {
            setExportWatch(null);
        }
    }, [pollTaskId]);
    useEffect(() => {
        if (!notePolling) return undefined;
        const taskId = pollTaskId;
        let stopped = false;
        let inFlight = false;
        const tick = async () => {
            if (stopped || inFlight) return;
            inFlight = true;
            let job = null;
            try {
                job = await getJob(taskId, resultJobOptionsRef.current);
            } catch (_) {
                // A missed poll is retried on the next tick.
            } finally {
                inFlight = false;
            }
            const fresh = job?.result;
            if (stopped || !fresh) return;
            const stop = () => {
                stopped = true;
                clearInterval(timer);
            };
            // The note is already in; only the export's outcome is awaited, and
            // only that is taken, so a note edited meanwhile is left alone.
            const exportWait = exportWatchRef.current?.taskId === taskId ? exportWatchRef.current : null;
            if (exportWait) {
                const timedOut = Date.now() - exportWait.since > AUTO_EXPORT_WAIT_MS;
                if (!larkExportSettled(fresh) && !timedOut) return;
                stop();
                setLastResult((prev) => (prev?.task_id === taskId ? mergeLarkFields(prev, fresh) : prev));
                setExportWatch(null);
                return;
            }
            const watch = noteWatchRef.current?.taskId === taskId ? noteWatchRef.current : null;
            if (watch && fresh.visual_note?.status === 'running') watch.sawRunning = true;
            const settled = watch ? claudeRewriteSettled(fresh, watch) : noteWritingSettled(fresh);
            if (!settled) return;
            // A note typed here and not yet saved is the user's; wait for the save
            // and take the server's copy on a later tick.
            if (summaryUnsavedRef.current || noteCellOpenRef.current) return;
            setLastResult((prev) => (prev?.task_id === taskId ? mergeNoteFields(prev, fresh) : prev));
            if (!watch && autoExportPending(job)) {
                // Keep this timer: the page now waits for the export instead.
                setExportWatch({taskId, since: Date.now()});
            } else {
                stop();
            }
            if (watch) {
                setNoteWatch(null);
                setRegenerating(false);
                const zh = langRef.current === 'zh';
                if (fresh.visual_note?.status === 'failed') {
                    const reason = String(fresh.visual_note?.error || '').trim();
                    showToast(zh
                        ? `Claude 写笔记没成功${reason ? `：${reason}` : ''}。`
                        : `Claude could not write the note${reason ? `: ${reason}` : ''}.`, false);
                } else {
                    showToast(tRef.current('edit.regenDone'));
                }
            }
        };
        const timer = setInterval(tick, NOTE_POLL_INTERVAL_MS);
        return () => {
            stopped = true;
            clearInterval(timer);
        };
    }, [notePolling, pollTaskId, getJob, setLastResult, showToast]);



    // Whether Claude can rewrite this particular task from its frames. Asked
    // when the task opens, so the answer is there before the dialog is, and
    // again when the dialog opens, because a cut file can be cleaned up while
    // the page sits open. Unknown (null) offers both ways.
    const [frameNoteAnswer, setFrameNoteAnswer] = useState(null);
    const frameNoteTaskId = noteWriter.kind === 'claude' ? (result?.task_id || '') : '';
    const frameNote = frameNoteAnswer && frameNoteAnswer.taskId === frameNoteTaskId ? frameNoteAnswer : null;
    const frameNoteAskedForRef = useRef('');
    useEffect(() => {
        if (!frameNoteTaskId || !getVisualNoteAvailability) return undefined;
        // Once per task, and again each time the dialog opens.
        if (!regenerateConfirmOpen && frameNoteAskedForRef.current === frameNoteTaskId) return undefined;
        frameNoteAskedForRef.current = frameNoteTaskId;
        let active = true;
        getVisualNoteAvailability(frameNoteTaskId, resultJobOptionsRef.current)
            .then((answer) => { if (active && answer) setFrameNoteAnswer({...answer, taskId: frameNoteTaskId}); })
            .catch(() => {});
        return () => { active = false; };
    }, [frameNoteTaskId, regenerateConfirmOpen, getVisualNoteAvailability]);

    // The text model that "改用文本模型" would use: the one selected in
    // settings, whether or not Claude currently writes the notes.
    const textWriter = useMemo(
        () => resolveNoteWriter({
            runtimeConfig: {...(runtimeConfig || {}), writesItsOwnNote: false},
            credentialStatus,
            settings: {...loadSettings(), skipAiSummary: false},
        }),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [runtimeConfig, credentialStatus],
    );

    const regenerateCopy = regenerateDialogCopy({
        writerKind: noteWriter.kind,
        writerLabel: noteWriter.kind === 'claude' || noteWriter.kind === 'text_model'
            ? noteWriterLabel(noteWriter, lang)
            : (lang === 'zh' ? '设置里选择的模型' : 'the model chosen in settings'),
        frameNote,
        textWriterLabel: textWriter.kind === 'text_model' ? noteWriterLabel(textWriter, lang) : '',
        textWriterReady: textWriter.kind === 'text_model',
    }, lang);

    // Claude rewrites from the cut file's frames and the transcript, in the
    // background; the poll above shows the new note when it is written.
    const handleRegenerateWithClaude = async () => {
        const taskId = result?.task_id;
        if (!taskId) return;
        setRegenerating(true);
        const requestedAtMs = Date.now();
        try {
            await startJobVisualNote(taskId, {replace_note: true}, resultJobOptions);
            setNoteWatch({taskId, requestedAtMs, sawRunning: false});
            showToast(lang === 'zh'
                ? 'Claude 正在结合画面写笔记，写好会自动出现在右侧。'
                : 'Claude is rewriting the note from the frames; it replaces the note on the right when done.');
        } catch (err) {
            setRegenerating(false);
            showToast(visualNoteStartErrorMessage(err, lang), false);
        }
    };

    // `way` is 'claude' (rewrite from the frames) or 'text' (the text model,
    // with the chosen prompt). The dialog decides which, per task.
    const handleRegenerate = async (way = regenerateCopy.primary) => {
        setRegenerateConfirmOpen(false);
        if(!transcript || regenerating) return;
        if (way === 'claude') {
            await handleRegenerateWithClaude();
            return;
        }
        setRegenerating(true);
        try {
            const settings = loadSettings();
            const fd = new FormData();
            fd.append('transcript', transcript);
            fd.append('task_id', activeTaskId);
            if(result.source) fd.append('source_type', result.source);
            if(result.filename) fd.append('source_filename', result.filename);
            if(durSec > 0) fd.append('source_duration_seconds', String(durSec));
            if(settings.noteMode) fd.append('note_mode', settings.noteMode);
            // Written the default way; no prompt from this browser (decision 6.33).
            const r = await apiFetch(`${API_BASE}/regenerate-summary`, {method:'POST', body:fd});
            if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||'Regeneration failed');
            const data = await r.json();
                    setLastResult({
                        ...result,
                        task_id: data.task_id || activeTaskId,
                        transcript_text: transcript,
                        segments,
                        transcript_edited: transcriptDirty || !!result.transcript_edited,
                        summary_markdown: data.summary_markdown,
                        // Written from the text now, not from the frames.
                        summary_written_from: null,
                        summary_skipped: false,
                        summary_status: data.summary_status || 'completed',
                        summary_error: null,
                        requested_note_mode: data.requested_note_mode||settings.noteMode||'auto',
                        resolved_note_mode: data.resolved_note_mode||null,
                        note_mode_chunk_count: data.note_mode_chunk_count||null,
                        note_mode_segment_count: data.note_mode_segment_count||null,
                        note_mode_evidence_count: data.note_mode_evidence_count||null,
                        note_mode_chapter_count: data.note_mode_chapter_count||null,
                        note_mode_important_evidence_count: data.note_mode_important_evidence_count||null,
                        note_mode_covered_important_evidence_count: data.note_mode_covered_important_evidence_count||null,
                        note_mode_coverage_missing_count: data.note_mode_coverage_missing_count||null,
                        chapter_coverage: data.chapter_coverage||null,
                        prompt_preset: data.prompt_preset || null,
                        prompt_preset_label: data.prompt_preset_label || null,
                        });
            // The rewritten note has a new version, which this answer does not
            // carry. Until the task is read again the note is not editable, so
            // the first edit is saved against the rewritten note, not refused
            // as made on the old one.
            const rewrittenTaskId = data.task_id || activeTaskId;
            setNoteReadFor('');
            getJob(rewrittenTaskId, resultJobOptions)
                .then((job) => {
                    if (job?.result) setLastResult((prev) => (prev?.task_id === rewrittenTaskId ? mergeNoteFields(prev, job.result) : prev));
                    setNoteReadFor(rewrittenTaskId);
                })
                .catch(() => setHydrationFailed(true));
            showToast(t('edit.regenDone'));
        } catch(err) { showToast(err.message, false); }
        finally { setRegenerating(false); }
    };

    const runRetranscribe = async (file) => {
        if(!file || retranscribing) return;
        const validExts = /\.(mp4|mov|avi|mkv|wmv|flv|webm|m4v|mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i;
        if(!validExts.test(file.name)){
            showToast(t('dash.fileError'), false);
            return;
        }
        const settings = loadSettings();
        const sttModel = normalizeSttModel(settings.sttModel);
        const sttProvider = submittedSttProvider(settings, runtimeConfig);
        const retranscribeErrorMessage = (err) => (
            err?.message || (lang === 'zh' ? '重新转录失败' : 'Retranscription failed')
        );
        const taskId = createTaskId();
        const sourceType = /\.(mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i.test(file.name) ? "audio" : "video";
        const fileSizeMb = Math.round(file.size / 1024 / 1024 * 1000) / 1000;
        setRetranscribing(true);
        setLastSourceFile(file);
        setCurrentJob({
            taskId,
            fileName:file.name,
            stage:'upload',
            progress:2,
            startedAt: Date.now(),
            sourceType,
            fileSizeMb,
            sttProvider,
            sttModel,
            sttLanguage: 'auto',
            skipSummary: !!settings.skipAiSummary,
            exportToLark: !!settings.exportToLark,
            noteMode: settings.noteMode||'auto',
        });
        try {
            const resultData = await processVideoSSE(file, {
                taskId,
                sourceLastModifiedMs: file.lastModified||null,
                larkExportRoute: larkExportRouteFromSettings(settings),
                larkViaCli: !!settings.larkViaCli,
                folderToken: settings.larkFolder || undefined,
                title: file.name.replace(/\.[^/.]+$/,""),
                ...buildAiOptions(settings),
                skipSummary: !!settings.skipAiSummary,
                sttProvider,
                sttModel,
                sttLanguage: 'auto',
            }, (ev) => {
                setCurrentJob(prev => prev ? {
                    ...prev,
                    stage:ev.stage,
                        progress:ev.progress,
                        sttProgress: ev.stt_progress ?? prev.sttProgress,
                        transcribedSeconds: ev.transcribed_seconds ?? prev.transcribedSeconds,
                            durationSeconds: ev.duration_seconds ?? prev.durationSeconds,
                            sttElapsedSeconds: ev.stt_elapsed_seconds ?? prev.sttElapsedSeconds,
                            sttStatus: ev.stt_status ?? prev.sttStatus,
                            sttProvider: ev.stt_provider ?? prev.sttProvider,
                        } : null);
                if(ev.stage === 'transcript_ready' && ev.result) setLastResult(ev.result);
            });

            setLastResult(resultData);
            const larkUrl = resultData.lark_response?.url || null;
            addToHistory(resultToHistoryEntry(resultData, {taskId, name:file.name, requestedNoteMode: settings.noteMode||'auto'}));
            if(larkUrl) addLarkExport({url:larkUrl, title: resultData.lark_doc_title || fileNameStem(file.name), timestamp:Date.now()});
            setCurrentJob({fileName:file.name, stage:'done', progress:100});
            if (doneJobTimerRef.current) clearTimeout(doneJobTimerRef.current);
            doneJobTimerRef.current = setTimeout(() => {
                doneJobTimerRef.current = null;
                setCurrentJob(null);
            }, 3000);
            showToast(t('edit.retranscribeDone'));
        } catch(err) {
            setCurrentJob(null);
            showToast(retranscribeErrorMessage(err), false);
        } finally {
            setRetranscribing(false);
        }
    };

    const handleRetranscribe = () => {
        setRetranscribeConfirmOpen(true);
    };

    const confirmRetranscribe = async () => {
        setRetranscribeConfirmOpen(false);
        const fetchPlaybackAudioForRetranscribe = async () => {
            const artifact = result?.artifacts?.playback_audio;
            if (!result?.task_id || !artifact) throw new Error(t('edit.retranscribeUnavailableTitle'));
            return await fetchJobArtifactFile(
                result.task_id,
                'playback_audio',
                artifact.filename || `${result.filename || 'source'}_audio.mp3`,
                resultJobOptions,
            );
        };
        // Run it again from the recording the backend can reach: the original where
        // it sits for a file chosen on this machine (whose task records no stored
        // copy), else the backend's own copy. Fetching it into the browser and
        // posting it back, as before, moved the whole file twice. Only when the
        // backend cannot reach any recording does a file in the browser go up.
        if(result?.task_id) {
            try {
                const data = await retryJob(result.task_id, resultJobOptions);
                if (data?.job) ingestJobs([data.job]);
                showToast(lang === 'zh'
                    ? '已重新排队转录，用的是本机上的原文件。完成后在处理记录里打开新的结果，这一条保留不动。'
                    : 'Queued again from the file on this machine. Open the new result from the records when it is done; this one stays as it is.');
                return;
            } catch (err) {
                if (matchedLocalSourceFile) {
                    runRetranscribe(matchedLocalSourceFile);
                } else if (result?.artifacts?.playback_audio) {
                    try {
                        const audioFile = await fetchPlaybackAudioForRetranscribe();
                        runRetranscribe(audioFile);
                    } catch (playbackErr) {
                        showToast(playbackErr.message || err.message || t('edit.retranscribeUnavailableTitle'), false);
                        retranscribeInputRef.current?.click();
                    }
                } else {
                    showToast(err.message || t('edit.retranscribeUnavailableTitle'), false);
                    retranscribeInputRef.current?.click();
                }
            }
        } else if(matchedLocalSourceFile) {
            runRetranscribe(matchedLocalSourceFile);
        } else retranscribeInputRef.current?.click();
    };

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
                <div className="w-full max-w-xl rounded-[28px] border border-[#e4e0e0] bg-white px-8 py-10 text-center shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                    <SvgIcon name="edit_note" className="mb-4 text-6xl text-[#8a8a8a] dark:text-white/40"/>
                    <h2 className="mb-2 font-headline text-2xl font-extrabold text-[#111111] dark:text-white">{urlTaskMissing ? (lang === 'zh' ? '找不到这条记录' : 'Record not found') : t('edit.noResult')}</h2>
                    <p className="mb-6 text-sm font-semibold leading-relaxed text-[#666] dark:text-white/60">{urlTaskMissing ? (lang === 'zh' ? '它可能已被删除。从处理记录选一条打开。' : 'It may have been deleted. Open one from the records.') : t('edit.noResultDesc')}</p>
                    <Link to="/agent" className="inline-flex items-center gap-2 rounded-[14px] bg-[#111111] px-6 py-3 text-sm font-bold text-white transition-colors hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85">
                        <SvgIcon name="monitoring" className="text-lg"/>{t('edit.chooseRecord')}
                    </Link>
                                            </div>
            </main>
                                                </div>
    );


    // The one thing in the slim header that needs the user, if anything, and
    // where clicking it takes them.
    const larkDocUrl = result?.lark_response?.url || '';
    const larkDocTitle = result?.lark_doc_title || resultTitle || '';
    const autoExportError = !larkDocUrl && result?.lark_error ? String(result.lark_error) : '';
    const noteMissing = !!result && !hasEditableSummary && (result.summary_skipped || result.summary_status === 'failed' || !!result.summary_error);
    const noteBeingWritten = regenerating || noteIsBeingWritten(result);
    const headerStatus = cutFileUnavailable
        ? {tone: 'error', label: lang === 'zh' ? '剪后文件读不到了，重新处理' : 'The cut file cannot be read: process again', onClick: handleRetranscribe}
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
                                className={`inline-flex h-7 shrink-0 items-center gap-1.5 rounded-[10px] border px-2.5 text-[12px] font-bold disabled:cursor-default ${headerStatus.tone === 'error'
                                    ? 'border-[#fda29b] bg-[#fef3f2] text-[#b42318] hover:bg-[#fee4e2] dark:border-[#f04438]/40 dark:bg-[#f04438]/[0.14] dark:text-[#f97066]'
                                    : headerStatus.tone === 'busy'
                                        ? 'border-[#e4e0e0] bg-white text-[#666] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/65'
                                        : 'border-[#f5c86b] bg-[#fffaf0] text-[#8a5a00] hover:bg-[#fff3dc] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]'}`}
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
                                    className="inline-flex size-7 items-center justify-center rounded-[10px] border border-[#dedada] bg-white text-[#57585d] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.10]"
                                >
                                    <SvgIcon name="open_in_new" className="text-base"/>
                                </a>
                            )}
                            <OutputFolderLine taskId={activeTaskId} result={result} lang={lang} compact/>
                        </div>
                    </div>


                        <div ref={splitContainerRef} className="flex min-h-0 flex-1 gap-0 overflow-hidden">
                            <section style={paneStyles.left} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-[#e4e0e0] bg-white shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                                <div className="border-b border-[#e4e0e0] px-4 py-3 dark:border-white/[0.12]">
                                    <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                                        <div className="min-w-0">
                                            <div className="flex items-center gap-2">
                                                <SvgIcon name="subject" className="text-[18px] text-[#111111] dark:text-white"/>
                                                <h2 className="truncate font-headline text-base font-extrabold text-[#111111] dark:text-white">
                                                    {lang === 'zh' ? '转录原文' : 'Transcript'}
                                                </h2>
                                                {(transcriptDirty || transcriptSaveStatus !== 'idle') && (
                                                    <span className={`inline-flex h-5 items-center gap-1 rounded-[8px] px-1.5 text-[11px] font-bold leading-none ${
                                                        transcriptSaveStatus === 'failed'
                                                            ? 'bg-error-container text-error'
                                                            : transcriptSaveStatus === 'saving'
                                                                ? 'bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white'
                                                                : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                                                    }`}>
                                                        <SvgIcon
                                                            name={transcriptSaveStatus === 'saving'
                                                                ? 'sync'
                                                                : transcriptSaveStatus === 'failed'
                                                                ? 'error'
                                                                : 'check_circle'}
                                                            className={`text-[12px] ${transcriptSaveStatus === 'saving' ? 'animate-spin' : ''}`}
                                                        />
                                                        {transcriptSaveStatus === 'saving'
                                                            ? t('edit.transcriptSaving')
                                                            : transcriptSaveStatus === 'failed'
                                                                ? t('edit.transcriptSaveFailed')
                                                                : (lang === 'zh' ? '转录已保存' : 'Transcript saved')}
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                                            {canShowVideoReview && segments.length > 0 && (
                                                <div className="inline-flex h-9 items-center gap-1 rounded-[13px] border border-[#e4e0e0] bg-[#f8f7fb] p-0.5 dark:border-white/[0.12] dark:bg-white/[0.06]">
                                                    <button
                                                        type="button"
                                                        onClick={()=>switchReviewMode('text')}
                                                        className={`inline-flex h-full items-center justify-center rounded-[10px] px-2.5 text-xs font-bold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 ${
                                                            activeReviewMode === 'text'
                                                                ? 'bg-[#111111] text-white dark:bg-white dark:text-[#111111]'
                                                                : 'text-[#666] hover:bg-white hover:text-[#111111] dark:text-white/60 dark:hover:bg-white/[0.08] dark:hover:text-white'
                                                        }`}
                                                    >
                                                        {lang === 'zh' ? '文本校对' : 'Text review'}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={()=>{ if (canUseVideoReview) switchReviewMode('video'); }}
                                                        disabled={!canUseVideoReview}
                                                        title={!canUseVideoReview ? (lang === 'zh' ? '选择原视频并保留时间戳后可用' : 'Available after choosing source video with timestamps') : undefined}
                                                        className={`inline-flex h-full items-center justify-center rounded-[10px] px-2.5 text-xs font-bold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:text-[#9a9a9a] disabled:hover:bg-transparent disabled:hover:text-[#9a9a9a] dark:disabled:text-white/28 dark:disabled:hover:text-white/28 ${
                                                            activeReviewMode === 'video'
                                                                ? 'bg-[#111111] text-white dark:bg-white dark:text-[#111111]'
                                                                : 'text-[#666] hover:bg-white hover:text-[#111111] dark:text-white/60 dark:hover:bg-white/[0.08] dark:hover:text-white'
                                                        }`}
                                                    >
                                                        {lang === 'zh' ? '视频复查' : 'Video review'}
                                                    </button>
                                                </div>
                                            )}
                                            {hasBilingualTranscript && segments.length > 0 && (
                                                <div className="inline-flex h-9 overflow-hidden rounded-[13px] border border-[#e4e0e0] bg-[#f8f7fb] p-0.5 dark:border-white/[0.12] dark:bg-white/[0.06]">
                                                    <button
                                                        type="button"
                                                        onClick={()=>setTranscriptView('bilingual')}
                                                        className={`inline-flex items-center justify-center px-2.5 text-xs font-bold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 ${
                                                            visibleTranscriptView === 'bilingual'
                                                                ? 'bg-[#111111] text-white dark:bg-white dark:text-[#111111]'
                                                                : 'text-[#666] hover:bg-white hover:text-[#111111] dark:text-white/60 dark:hover:bg-white/[0.08] dark:hover:text-white'
                                                        }`}
                                                    >
                                                        {lang === 'zh' ? '中英对照' : 'Bilingual'}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={()=>setTranscriptView('raw')}
                                                        className={`inline-flex items-center justify-center px-2.5 text-xs font-bold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 ${
                                                            visibleTranscriptView === 'raw'
                                                                ? 'bg-[#111111] text-white dark:bg-white dark:text-[#111111]'
                                                                : 'text-[#666] hover:bg-white hover:text-[#111111] dark:text-white/60 dark:hover:bg-white/[0.08] dark:hover:text-white'
                                                        }`}
                                                    >
                                                        {lang === 'zh' ? '原始字幕' : 'Original'}
                                                    </button>
                                                </div>
                                            )}
                                            {visibleEditRecords.length > 0 && (
                                                <button
                                                    type="button"
                                                    onClick={()=>setEditRecordsOpen(true)}
                                                    className="inline-flex h-9 items-center justify-center gap-1.5 rounded-[13px] border border-[#e4e0e0] bg-white px-3 text-xs font-bold text-[#666] transition hover:bg-[#efeeee] hover:text-[#111111] active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/65 dark:hover:bg-white/[0.1] dark:hover:text-white"
                                                >
                                                    <SvgIcon name="edit_note" className="text-[17px]"/>
                                                    <span>{t('edit.editRecords')}</span>
                                                    <span className="tabular-nums text-primary">{visibleEditRecords.length}</span>
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                </div>
                        {/* One list and one player for both modes; switching only
                            changes the layout. Rebuilding them made a switch take
                            half a second on a 7104-sentence transcript, stopped
                            playback while the page still showed it playing, and
                            lost the reading position. */}
                        <div className={shouldShowVideoReview ? 'flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-4' : 'flex min-h-0 flex-1 flex-col overflow-hidden'}>
                            <video
                                ref={mediaRef}
                                src={mediaUrl || undefined}
                                controls={shouldShowVideoReview}
                                className={shouldShowVideoReview ? 'max-h-[min(58vh,36rem)] w-full shrink-0 rounded-[18px] bg-black object-contain' : 'hidden'}
                                onTimeUpdate={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration})}
                                onLoadedMetadata={(e)=>restoreMediaPosition(e.currentTarget, e.currentTarget.duration || durSec || 0)}
                                onSeeked={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true})}
                                onPlay={()=>setMediaPlaying(true)}
                                onPause={(e)=>{ updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true}); setMediaPlaying(false); }}
                                onEnded={()=>setMediaPlaying(false)}
                            />
                            <div
                                ref={transcriptScrollRef}
                                className={shouldShowVideoReview
                                    ? 'hide-scrollbar min-h-[10rem] flex-1 overflow-y-auto rounded-[18px] border border-[#e4e0e0] bg-[#fbfbfb] px-4 py-2 dark:border-white/[0.12] dark:bg-white/[0.05]'
                                    : 'hide-scrollbar min-h-0 flex-1 overflow-y-auto p-3'}
                            >
                                <div className={shouldShowVideoReview ? 'divide-y divide-[#e4e0e0] dark:divide-white/[0.08]' : 'space-y-1.5'}>
                            {visibleTranscriptView === 'bilingual' && bilingualTranscriptSegments.length > 0 ? bilingualTranscriptSegments.map((seg,i) => (
                                <BilingualLine key={`bilingual-${i}`} index={i} seg={seg} active={i===activeSegmentIndex && !!mediaUrl} review={shouldShowVideoReview} onSeek={seekRow} registerNode={registerRow}/>
                            )) : segments.length > 0 ? segments.map((seg,i) => (
                                <TranscriptLine key={i} index={i} seg={seg} active={i===activeSegmentIndex && !!mediaUrl} review={shouldShowVideoReview} onSeek={seekRow} onEdit={editRow} onFocusLine={stopFollowing} registerNode={registerRow}/>
                                )) : isTranscriptHydrationPending ? (
                                    <div className="flex min-h-[320px] items-center justify-center rounded-[16px] border border-dashed border-outline-variant bg-surface-container-low px-4 py-8 text-center">
                                        <div className="max-w-[28rem]">
                                            <SvgIcon name="sync" className="mx-auto mb-3 h-5 w-5 animate-spin text-primary"/>
                                            <p className="text-sm font-bold text-on-surface">
                                                {lang === 'zh' ? '正在加载逐段转录' : 'Loading timestamped transcript'}
                                            </p>
                                            <p className="mt-1 text-xs font-semibold leading-relaxed text-on-surface-variant">
                                                {lang === 'zh'
                                                    ? '刚从最近活动打开时会先读取轻量缓存，完整时间戳分段正在从处理记录补全。'
                                                    : 'FluentFlow opened a lightweight cached result first and is restoring the full timestamped segments from the processing record.'}
                                            </p>
                                        </div>
                                    </div>
                                ) : isTranscriptHydrationFailed ? (
                                    <div className="flex min-h-[320px] items-center justify-center rounded-[16px] border border-amber-200 bg-amber-50 px-4 py-8 text-center text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-100">
                                        <div className="max-w-[30rem]">
                                            <SvgIcon name="info" className="mx-auto mb-3 h-5 w-5 text-amber-600 dark:text-amber-200"/>
                                            <p className="text-sm font-bold">
                                                {lang === 'zh' ? '完整逐段转录还没加载出来' : 'Full timestamped transcript did not load'}
                                            </p>
                                            <p className="mt-1 text-xs font-semibold leading-relaxed text-amber-800 dark:text-amber-100/75">
                                                {lang === 'zh'
                                                    ? '当前只拿到了浏览器缓存预览，不能当作完整转录编辑。请回到处理记录刷新，或稍后重新打开结果。'
                                                    : 'Only a cached preview is available, so this is not safe to edit as the full transcript. Refresh processing records or reopen the result later.'}
                                            </p>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="min-h-full flex flex-col gap-2">
                                        <div className="flex items-center gap-2 rounded-[12px] border border-[#d6dcff] bg-[#eef2ff] px-3 py-2 dark:border-white/[0.12] dark:bg-white/[0.08]">
                                            <SvgIcon name="info" className="text-primary text-[15px] flex-shrink-0"/>
                                            <p className="truncate text-xs font-semibold text-on-surface-variant" title={lang==='zh'
                                                ? '当前结果没有时间戳分段。在首页把这条录像重新处理一遍，就能逐段校对。'
                                                : 'This result has no timestamped segments. Process the recording again from the start page to review it segment by segment.'}>
                                                {lang==='zh'
                                                    ? '当前结果没有时间戳分段。在首页把这条录像重新处理一遍，就能逐段校对。'
                                                    : 'No timestamped segments. Process the recording again from the start page to review it segment by segment.'}
                                            </p>
                                        </div>
                                    <textarea
                                        value={transcript}
                                        onChange={(e)=>handlePlainTranscriptChange(e.target.value)}
                                                                                onFocus={()=>setFollowPlayback(false)}
                                        className="min-h-[320px] w-full flex-1 resize-none whitespace-pre-wrap border-none bg-transparent p-0 text-sm font-medium leading-relaxed text-[#111111] focus:ring-0 dark:text-white"
                                    />
                                    </div>
                                )}
                                </div>
                            </div>
                            {!shouldShowVideoReview && (
                                <div className="border-t border-[#e4e0e0] bg-[#fbfbfb]/90 p-4 dark:border-white/[0.12] dark:bg-white/[0.04]">
                                    {mediaUrl ? (
                                        <div className="flex flex-wrap items-center gap-x-3 gap-y-3">
                                                <button type="button" onClick={togglePlayback} className="flex h-9 w-9 items-center justify-center rounded-[13px] bg-[#111111] text-white transition hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85">
                                                    <SvgIcon name={mediaPlaying ? 'pause' : 'play_arrow'} className="text-lg"/>
                                                </button>
                                                <button type="button" onClick={()=>setFollowPlayback(v=>!v)} className={`rounded-[12px] px-2.5 py-1.5 text-xs font-bold transition ${followPlayback?'bg-[#eef2ff] text-primary dark:bg-white/[0.1] dark:text-white':'bg-[#efeeee] text-[#666] dark:bg-white/[0.08] dark:text-white/60'}`}>
                                                    {t('edit.followPlayback')}
                                                </button>
                                                {/* Which of the two files is playing. Both exist for a
                                                    cut task and they are different lengths, so leaving
                                                    this out makes the timeline unexplainable. */}
                                                {playingCutVersion && (
                                                    <span className="inline-flex items-center gap-1 rounded-[12px] bg-[#eef2ff] px-2 py-1 text-[11px] font-bold text-primary dark:bg-white/[0.1] dark:text-white">
                                                        <SvgIcon name="content_cut" className="text-[13px]"/>
                                                        {lang === 'zh' ? '剪后的版本' : 'Cut version'}
                                                    </span>
                                                )}
                                                <PlaybackBar clock={playbackClock} duration={playbackDuration} onSeek={seekMediaTo}/>
                                        </div>
                                    ) : (
                                        <div className="flex items-center justify-between gap-3">
                                            <p className="text-xs font-semibold text-[#666] dark:text-white/60">{mediaLoading ? t('edit.sourceLoading') : (mediaError || t('edit.audioUnavailable'))}</p>
                                            <button type="button" onClick={()=>mediaInputRef.current?.click()} className="inline-flex items-center gap-1.5 rounded-[13px] bg-[#efeeee] px-3 py-2 text-xs font-bold text-[#111111] transition hover:bg-[#e4e0e0] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]">
                                                <SvgIcon name="audio_file" className="text-sm"/>{t('edit.chooseAudio')}
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                        {/* No de-breath entry here any more. Removing the breath gaps
                            is what an upload does to itself, so a panel asking the user
                            to run it described a flow that no longer exists — the owner
                            saw it on a finished task and asked what it was for. The
                            record of the automatic step is the bar under the title.
                            Tasks processed before the automatic flow simply do not
                            mention it; re-uploading is the way to give one the
                            treatment, and the Agent API still exposes the action for a
                            script that wants it. */}
                            </section>

                            <EditorSplitHandle
                                containerRef={splitContainerRef}
                                ratio={splitRatio}
                                onChange={setSplitRatio}
                                onCommit={commitSplitRatio}
                                onReset={resetSplitRatio}
                                lang={lang}
                            />

                            <section style={paneStyles.right} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-[#e4e0e0] bg-white shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] xl:w-[38rem] xl:flex-none 2xl:w-[42rem] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                                <div className="flex items-center justify-between gap-3 border-b border-[#e4e0e0] bg-[#fbfbfb] px-4 py-3 dark:border-white/[0.12] dark:bg-white/[0.04]">
                                    <h2 className="flex items-center gap-2 font-headline text-base font-extrabold text-[#111111] dark:text-white">
                                        <SvgIcon name="psychology" className="text-[#111111] dark:text-white"/>
                                        {lang === 'zh' ? '笔记正文' : 'Note'}
                                    </h2>
                                    {summarySaveLabel && (
                                        <span className={`mr-auto rounded-full border px-2 py-1 text-[11px] font-bold ${
                                            summarySaveStatus === 'failed'
                                                ? 'border-error/25 bg-error-container text-on-error-container'
                                                : 'border-[#e4e0e0] bg-white text-[#666] dark:border-white/[0.12] dark:bg-white/[0.05] dark:text-white/60'
                                        }`}>
                                            {summarySaveLabel}
                                        </span>
                                    )}
                                    <div className="flex shrink-0 items-center gap-2">
                                        {hasInlineVisualEvidence && (
                                            <button
                                                type="button"
                                                onClick={()=>setVisualEvidenceVisible((value)=>!value)}
                                                className="inline-flex h-8 items-center justify-center gap-1.5 rounded-[13px] border border-[#e4e0e0] bg-white px-3 text-xs font-bold text-[#555] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-white/70 dark:hover:bg-white/[0.08]"
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
                                        />
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
                                            <p className="text-sm italic text-[#666] dark:text-white/60">{t(result.summary_skipped_reason === 'no_model_key' ? 'edit.summaryNoKey' : 'edit.summarySkipped')}</p>
                                            {!noteBeingWritten && (
                                                <button
                                                    ref={writeNoteButtonRef}
                                                    type="button"
                                                    data-testid="write-note"
                                                    onClick={()=>setRegenerateConfirmOpen(true)}
                                                    disabled={!transcript}
                                                    className="my-3 inline-flex h-8 items-center gap-1.5 rounded-[12px] bg-[#111111] px-3 text-[13px] font-bold not-italic text-white transition hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/85"
                                                >
                                                    <SvgIcon name="edit_note" className="text-[15px]"/>
                                                    {lang === 'zh' ? '写笔记' : 'Write the note'}
                                                </button>
                                            )}
                                            <AgentNotePrompt result={result} lang={lang}/>
                                        </>
                                    ) : result.summary_status === 'failed' || result.summary_error ? (
                                        <div className="space-y-2 text-sm text-[#666] dark:text-white/60">
                                            <p className="italic">{t('edit.summaryFailed')}</p>
                                            {!noteBeingWritten && (
                                                <button
                                                    ref={writeNoteButtonRef}
                                                    type="button"
                                                    data-testid="write-note"
                                                    onClick={()=>setRegenerateConfirmOpen(true)}
                                                    disabled={!transcript}
                                                    className="inline-flex h-8 items-center gap-1.5 rounded-[12px] bg-[#111111] px-3 text-[13px] font-bold not-italic text-white transition hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/85"
                                                >
                                                    <SvgIcon name="edit_note" className="text-[15px]"/>
                                                    {lang === 'zh' ? '写笔记' : 'Write the note'}
                                                </button>
                                            )}
                                            {summaryFailureHint && (
                                                <p className="rounded-[14px] border border-error/20 bg-error-container px-3 py-2 text-xs font-semibold leading-relaxed text-on-error-container">
                                                    {summaryFailureHint}
                                                    {result?.task_id && (
                                                        <>
                                                            {' '}
                                                            <Link data-testid="task-steps-link" to={`/tasks/${encodeURIComponent(result.task_id)}/agent`} className="underline hover:no-underline">
                                                                {lang === 'zh' ? '处理过程' : 'Processing steps'}
                                                            </Link>
                                                        </>
                                                    )}
                                                </p>
                                            )}
                                            <AgentNotePrompt result={result} lang={lang}/>
                                        </div>
                                    ) : (
                                        <p className="text-sm italic text-[#666] dark:text-white/60">{notePendingSentence(noteWriter, result, lang)}</p>
                                    )}
                                    </div>
                                )}
                            </section>
                        </div>
                    </div>
                </main>
            </div>
        );
};

export default Editor;
