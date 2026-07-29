import {useState,useEffect,useRef,useCallback,useMemo} from 'react';
import {Link} from 'react-router-dom';
import {
    DEFAULT_PROMPT_PRESET,
    getBuiltinExtraPromptBody,
    getDefaultPromptBody,
    isBuiltinPromptPresetHidden,
    normalizeUserPresets,
    presetDisplayLabel,
    resolveSystemPromptFromSettings,
} from '../lib/promptPresets.js';
import SvgIcon from '../components/SvgIcon.jsx';
import {
    API_BASE,
    createTaskId,
    effectiveSttProvider,
    autoSizeTextarea,
    buildTranscriptEditRecords,
    composeTranscriptText,
    dlSummaryMd,
    dlSummaryPdf,
    dlSummaryTxt,
    dlSummaryWord,
    dlBilingualTranscriptSrt,
    dlBilingualTranscriptVtt,
    dlTranscriptSrt,
    dlTranscriptTxt,
    dlTranscriptVtt,
    fmtTime,
    apiFetch,
    DropdownMenu,
    fileNameStem,
    isLocalHistoryResult,
    isLocalLarkExportRoute,
    larkExportRouteFromSettings,
    localExecutionHeaders,
    pickDisplayTranscriptSegments,
    normalizeSttModel,
    pickTranscriptBaselineSegments,
    pickTranscriptSegments,
    resultToHistoryEntry,
    resultDisplayTitle,
    shouldUseLocalSingleUserClientId,
    simpleMd,
    useApi,
    useI18n,
    useSettings,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import PromptTemplateDialog from '../components/PromptTemplateDialog.jsx';
import RichNoteEditor from '../components/RichNoteEditor.jsx';
import {FeishuExportPrompt, RegenerateConfirmDialog, RetranscribeConfirmDialog, EditRecordsDialog} from './editor-dialogs.jsx';
import {
    jobOptionsForResult,
    isLikelyVideoFile,
    isVideoResultSource,
    localSourceFileMatchesResult,
    shouldKeepVideoReviewMounted,
    summaryFailureNextStep,
    formatElapsedMinuteSecond,
    formatSttOriginalRatio,
    downloadBrowserFile,
} from './editor-helpers.js';

// The default editor is the local workspace. Hosted result access, visitor
// trial artifacts, account OAuth, and cross-device read-only policy are
// supplied by HostedEditorWorkspace.jsx only in the hosted route.
const Editor = ({hosted = null}) => {
    const {t, lang} = useI18n();
    const {
        lastResult,
        setLastResult,
        lastSourceFile,
        setLastSourceFile,
        addToHistory,
        currentJob,
        setCurrentJob,
        addLarkExport,
        runtimeConfig,
    } = useApp();
    const {processVideoSSE, fetchJobSourceFile, fetchJobArtifactFile, uploadJobPlaybackAudio, recordEvent, getJob, saveTranscriptEdit, saveSummaryEdit} = useApi();
    const {loadSettings, saveSettings} = useSettings();
    const [exporting, setExporting] = useState(false);
    const [regenerating, setRegenerating] = useState(false);
    const [retranscribing, setRetranscribing] = useState(false);
    const [downloading, setDownloading] = useState(null);
    const [toast, setToast] = useState(null);
    const [larkUrl, setLarkUrl] = useState(null);
    const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false);
    const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false);
    const [feishuExportPromptOpen, setFeishuExportPromptOpen] = useState(false);
    const [feishuExportConnecting, setFeishuExportConnecting] = useState(false);
    const [feishuReconnectRequired, setFeishuReconnectRequired] = useState(false);
    const [visualEvidenceVisible, setVisualEvidenceVisible] = useState(true);
    const summaryRef = useRef(null);
    const retranscribeInputRef = useRef(null);
    const fallbackTaskIdRef = useRef(createTaskId());
    const hydratedTaskIdsRef = useRef(new Set());
    const transcriptSaveSeqRef = useRef(0);
    const summarySaveSeqRef = useRef(0);
    const summaryDraftResultKeyRef = useRef('');
    const playbackSaveRef = useRef(0);
    const mediaObjectUrlRef = useRef('');

    const initSettings = loadSettings();
    let initPk = initSettings.promptPreset || DEFAULT_PROMPT_PRESET;
    if (isBuiltinPromptPresetHidden(initPk, initSettings)) initPk = 'default';
    const [promptKey, setPromptKey] = useState(initPk);
    const [customText, setCustomText] = useState(initSettings.customPromptText || '');
    const [defaultPromptEdit, setDefaultPromptEdit] = useState(() => getDefaultPromptBody(initSettings));
    const [autoTranscriptNotesEdit, setAutoTranscriptNotesEdit] = useState(() => getBuiltinExtraPromptBody('autoTranscriptNotes', initSettings));
    const [meetingEdit, setMeetingEdit] = useState(() => getBuiltinExtraPromptBody('meeting', initSettings));
    const [researchEdit, setResearchEdit] = useState(() => getBuiltinExtraPromptBody('research', initSettings));
    const [quickBulletsEdit, setQuickBulletsEdit] = useState(() => getBuiltinExtraPromptBody('quickBullets', initSettings));
    const [userPresetEdit, setUserPresetEdit] = useState(() => {
        if (initPk.startsWith('user_')) {
            const p = (initSettings.userPromptPresets || []).find((x) => x.id === initPk);
            return p?.prompt || '';
        }
        return '';
    });
    const [presetNameInput, setPresetNameInput] = useState('');
    const [, setPresetListTick] = useState(0);
    const [promptOpen, setPromptOpen] = useState(false);

    useEffect(() => {
        if (!promptOpen) return;
        const s = loadSettings();
        const pkRaw = s.promptPreset || DEFAULT_PROMPT_PRESET;
        const pk = isBuiltinPromptPresetHidden(pkRaw, s) ? 'default' : pkRaw;
        setPromptKey(pk);
        setDefaultPromptEdit(getDefaultPromptBody(s));
        setAutoTranscriptNotesEdit(getBuiltinExtraPromptBody('autoTranscriptNotes', s));
        setMeetingEdit(getBuiltinExtraPromptBody('meeting', s));
        setResearchEdit(getBuiltinExtraPromptBody('research', s));
        setQuickBulletsEdit(getBuiltinExtraPromptBody('quickBullets', s));
        setCustomText(s.customPromptText || '');
        if (pk.startsWith('user_')) {
            const p = (s.userPromptPresets || []).find((x) => x.id === pk);
            setUserPresetEdit(p?.prompt || '');
        }
    }, [promptOpen]);

    const result = lastResult;
    const resultAccess = hosted?.resultAccess?.(result) || {};
    const isTransientResult = !!resultAccess.transient;
    const isReadOnlyResult = !!resultAccess.readOnly;
    const resultNotice = resultAccess.notice || '';
    const canPersistResult = !isTransientResult && !isReadOnlyResult;
    const matchedLocalSourceFile = localSourceFileMatchesResult(lastSourceFile, result) ? lastSourceFile : null;
    const resultSegmentCount = pickTranscriptSegments(result).length;
    const resultTextLength = (result?.transcript_text || '').length;
    const resultKey = result
        ? `${result.task_id || result.filename || 'current_result'}:${result.transcript_edited ? 'edited' : `${resultSegmentCount}:${resultTextLength}`}`
        : 'empty_result';
    const summaryResultKey = result
        ? `${result.task_id || result.filename || 'current_result'}`
        : 'empty_result';
    const mediaSourceKey = result
        ? [
            result.task_id || result.filename || 'current_result',
            result.artifacts?.playback_audio?.filename || (result.playback_audio_available ? 'playback-audio' : 'no-playback-audio'),
            result.source_file_available ? 'stored' : 'unstored',
            matchedLocalSourceFile ? `${matchedLocalSourceFile.name}:${matchedLocalSourceFile.size}:${matchedLocalSourceFile.lastModified || 0}` : 'no-local-file',
        ].join(':')
        : 'empty_media_source';
    const [editedSegments, setEditedSegments] = useState([]);
    const [editedTranscript, setEditedTranscript] = useState('');
    const [baselineSegments, setBaselineSegments] = useState([]);
    const [transcriptDirty, setTranscriptDirty] = useState(false);
    const [transcriptUnsaved, setTranscriptUnsaved] = useState(false);
    const [mediaUrl, setMediaUrl] = useState('');
    const [mediaKind, setMediaKind] = useState('audio');
    const [mediaLoading, setMediaLoading] = useState(false);
    const [mediaError, setMediaError] = useState('');
    const [mediaCurrentTime, setMediaCurrentTime] = useState(0);
    const [mediaDuration, setMediaDuration] = useState(0);
    const [mediaPlaying, setMediaPlaying] = useState(false);
    const [followPlayback, setFollowPlayback] = useState(true);
    const [transcriptReviewMode, setTranscriptReviewMode] = useState('text');
    const [transcriptSaveStatus, setTranscriptSaveStatus] = useState('idle');
    const [summaryDraft, setSummaryDraft] = useState('');
    const [summaryUnsaved, setSummaryUnsaved] = useState(false);
    const [summarySaveStatus, setSummarySaveStatus] = useState('idle');
    const [hydratingResult, setHydratingResult] = useState(false);
    const [hydrationFailed, setHydrationFailed] = useState(false);
    const [transcriptView, setTranscriptView] = useState('bilingual');
    const [editRecordsOpen, setEditRecordsOpen] = useState(false);
    const mediaRef = useRef(null);
    const mediaInputRef = useRef(null);
    const transcriptScrollRef = useRef(null);
    const segmentRefs = useRef({});
    const resultJobOptions = useMemo(() => resultAccess.jobOptions || jobOptionsForResult(result), [
        resultAccess.jobOptions,
        result?.stt_provider,
        result?.playback_audio_storage,
        result?.source_file_storage,
    ]);

    useEffect(() => {
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
        const needsHydration = currentSegments.length === 0 || currentText.length <= 260;
        if (!needsHydration || hydratedTaskIdsRef.current.has(result.task_id)) {
            setHydratingResult(false);
            if (!needsHydration) setHydrationFailed(false);
            return;
        }
        setHydratingResult(true);
        setHydrationFailed(false);
        let cancelled = false;
        const jobRequest = hosted?.getResultJob?.(result.task_id, resultJobOptions)
            || getJob(result.task_id, resultJobOptions);
        jobRequest
            .then((job) => {
                const full = job?.result;
                if (cancelled || !full) return;
                const fullSegments = pickTranscriptSegments(full);
                const fullText = full.transcript_text || '';
                const currentDisplayCount = pickDisplayTranscriptSegments(result, currentSegments).length;
                const fullDisplayCount = pickDisplayTranscriptSegments(full, fullSegments).length;
                if (fullSegments.length > currentSegments.length || fullText.length > currentText.length || fullDisplayCount > currentDisplayCount) {
                    const fullBaselineSegments = pickTranscriptBaselineSegments(full);
                    setLastResult(hosted?.mergeHydratedResult?.(full, result) || full);
                    setEditedSegments(fullSegments.map((seg) => ({...seg})));
                    setEditedTranscript(composeTranscriptText(fullSegments, fullText));
                    setBaselineSegments((prev) => {
                        if (fullBaselineSegments.length > 0) return fullBaselineSegments.map((seg) => ({...seg}));
                        if (full.transcript_edited && prev.length > 0) return prev;
                        return fullSegments.map((seg) => ({...seg}));
                    });
                    setTranscriptDirty(!!full.transcript_edited);
                    setTranscriptSaveStatus(full.transcript_edited ? 'saved' : 'idle');
                }
                hydratedTaskIdsRef.current.add(result.task_id);
            })
            .catch(() => {
                if (!cancelled) setHydrationFailed(true);
            })
            .finally(() => {
                if (!cancelled) setHydratingResult(false);
            });
        return () => { cancelled = true; };
    }, [resultKey, transcriptUnsaved, hosted, resultJobOptions]);

    useEffect(() => {
        if (!result) {
            setEditedSegments([]);
            setEditedTranscript('');
            setBaselineSegments([]);
            setTranscriptDirty(false);
            setTranscriptUnsaved(false);
            return;
        }
        if (result.transcript_edited && transcriptUnsaved) return;
        const sourceSegments = pickTranscriptSegments(result);
        const baselineSourceSegments = pickTranscriptBaselineSegments(result);
        const sourceText = result.transcript_text || '';
        setEditedSegments(sourceSegments.map((seg) => ({...seg})));
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

    const applyTranscriptEdit = useCallback((nextSegments, nextText) => {
        if (!result) return;
        const nextEditRecords = buildTranscriptEditRecords(baselineSegments, nextSegments, result);
        const updated = {
            ...result,
            segments: nextSegments,
            transcript_text: nextText,
            transcript_edit_records: nextEditRecords,
            transcript_edit_record_count: nextEditRecords.length,
            transcript_edited: true,
            transcript_edited_at: new Date().toISOString(),
        };
        setEditedSegments(nextSegments);
        setEditedTranscript(nextText);
        setTranscriptDirty(true);
        setTranscriptUnsaved(true);
        setTranscriptSaveStatus(result.task_id ? 'saving' : 'failed');
        setLastResult(updated);
    }, [baselineSegments, result, setLastResult]);

    const handleSegmentTextChange = (index, text) => {
        const nextSegments = editedSegments.map((seg, i) => i === index ? {...seg, text} : seg);
        applyTranscriptEdit(nextSegments, composeTranscriptText(nextSegments, editedTranscript));
    };

    const handlePlainTranscriptChange = (text) => {
        applyTranscriptEdit([], text);
    };

    const handleSummaryChange = useCallback((text) => {
        setSummaryDraft(text);
        setSummaryUnsaved(true);
        setSummarySaveStatus(result?.task_id && canPersistResult ? 'saving' : 'local');
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
    }, [canPersistResult, result, setLastResult]);

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

    useEffect(() => {
        let cancelled = false;
        replaceMediaUrl('');
        setMediaError('');
        setMediaLoading(false);
        setMediaKind('audio');
        if (!result) return () => { cancelled = true; };
        if (matchedLocalSourceFile) {
            loadMediaFile(matchedLocalSourceFile);
            return () => { cancelled = true; };
        }
        if (result.task_id && result.source_file_available && canPersistResult) {
            setMediaLoading(true);
            fetchJobSourceFile(result.task_id, result.filename || 'source', resultJobOptions)
                .then((file) => { if (!cancelled) loadMediaFile(file); })
                .catch((sourceErr) => {
                    if (!cancelled && result.artifacts?.playback_audio) {
                        const playbackArtifact = result.artifacts.playback_audio;
                        const fetchArtifact = hosted?.fetchResultArtifact || fetchJobArtifactFile;
                        fetchArtifact(result.task_id, 'playback_audio', playbackArtifact.filename || `${result.filename || 'source'}_audio.mp3`, resultJobOptions)
                            .then((file) => { if (!cancelled) loadMediaFile(file); })
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
            const fetchArtifact = hosted?.fetchResultArtifact || fetchJobArtifactFile;
            const artifactOptions = resultJobOptions;
            fetchArtifact(result.task_id, 'playback_audio', playbackArtifact.filename || `${result.filename || 'source'}_audio.mp3`, artifactOptions)
                .then((file) => { if (!cancelled) loadMediaFile(file); })
                .catch((err) => {
                    if (!cancelled && result.source_file_available && canPersistResult) {
                        fetchJobSourceFile(result.task_id, result.filename || 'source', resultJobOptions)
                            .then((file) => { if (!cancelled) loadMediaFile(file); })
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
    }, [mediaSourceKey, canPersistResult, hosted, resultJobOptions, replaceMediaUrl]);

    const segments = editedSegments;
    const transcript = editedTranscript || result?.transcript_text || '';
    const editRecords = useMemo(
        () => buildTranscriptEditRecords(baselineSegments, segments, result),
        [baselineSegments, segments, result?.transcript_edit_records]
    );
    const visibleEditRecords = useMemo(
        () => editRecords.filter((record) => {
            const before = String(record?.before || '').trim();
            const after = String(record?.after || '').trim();
            if (!before && !after) return false;
            if (before === after) return false;
            const current = segments[record?.index];
            if (!current) return false;
            return String(current.text || '').trim() === after;
        }),
        [editRecords, segments],
    );
    const summary = summaryMarkdownForEditor;
    const hasEditableSummary = !!result && (
        !!summary
        || summaryUnsaved
        || result?.summary_status === 'completed'
        || !!result?.summary_edited
    );
    const canEditSummary = hasEditableSummary && !isReadOnlyResult;
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
    const displayTranscriptSegments = pickDisplayTranscriptSegments(result, segments);
    const bilingualTranscriptSegments = displayTranscriptSegments
        .filter((seg) => String(seg.text_zh || '').trim());
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
    const retranscribeBlockedByJob = !!currentJob && !['summary', 'export', 'done'].includes(currentJob.stage);
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
    const sttElapsedSec = result?.stt_elapsed_seconds || 0;
    const sttRealtimeFactor = result?.stt_realtime_factor || (durSec > 0 && sttElapsedSec > 0 ? sttElapsedSec / durSec : null);
    const sttElapsedLabel = sttElapsedSec > 0
        ? `${t('edit.sttElapsed')} ${formatElapsedMinuteSecond(sttElapsedSec)}${sttRealtimeFactor ? (lang === 'zh' ? `（${formatSttOriginalRatio(sttRealtimeFactor, lang)}）` : ` (${formatSttOriginalRatio(sttRealtimeFactor, lang)})`) : ''}`
        : '';
    const activeTaskId = result?.task_id || fallbackTaskIdRef.current;
    const playbackMemoryKey = result ? `fluentflow_playback_position_${activeTaskId}` : '';
    const summaryFailureHint = summaryFailureNextStep(result, lang);
    const resultTitle = resultDisplayTitle(result, {name: t('edit.title')});
    const resultDownloadName = resultTitle || result?.filename;
    const rawEditorTitle = resultTitle || result?.filename || t('edit.title');
    const agentWorkflowHref = result?.task_id ? `/tasks/${encodeURIComponent(result.task_id)}/agent` : '/agent';
    const playbackDuration = mediaDuration || durSec || 0;
    const canDownloadSourceVideo = !!(
        isLikelyVideoFile(matchedLocalSourceFile || result?.filename)
        && (matchedLocalSourceFile || (result?.task_id && result?.source_file_available))
    );
    const canShowVideoReview = isVideoResultSource(result, matchedLocalSourceFile);
    const canUseVideoReview = canShowVideoReview && mediaKind === 'video' && !!mediaUrl && segments.length > 0;
    const activeReviewMode = canUseVideoReview ? transcriptReviewMode : 'text';
    const shouldShowVideoReview = shouldKeepVideoReviewMounted({activeReviewMode});
    const activeSegmentIndex = visibleTranscriptSegments.length > 0
        ? (() => {
            const found = visibleTranscriptSegments.findIndex((seg, index) => {
            const start = Number(seg.start) || 0;
            const nextStart = Number(visibleTranscriptSegments[index + 1]?.start);
            const end = Number(seg.end) || (Number.isFinite(nextStart) ? nextStart : start + 6);
            return mediaCurrentTime >= start && mediaCurrentTime < end;
            });
            return found >= 0 ? found : -1;
        })()
        : -1;
    const updateMediaCurrentTime = useCallback((time, options={}) => {
        const next = Math.max(0, Number(time) || 0);
        setMediaCurrentTime(next);
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
    }, [playbackDuration, playbackMemoryKey]);
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

    useEffect(() => {
        const root = transcriptScrollRef.current;
        if (!root) return;
        root.querySelectorAll('textarea[data-transcript-segment="true"]').forEach(autoSizeTextarea);
    }, [segments]);

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

    useEffect(() => {
        if (!result?.task_id || !transcriptUnsaved) return;
        if (!canPersistResult) {
            setTranscriptSaveStatus('idle');
            return;
        }
        const seq = ++transcriptSaveSeqRef.current;
        setTranscriptSaveStatus('saving');
        const timer = setTimeout(() => {
            saveTranscriptEdit(result.task_id, {
                transcript_text: transcript,
                segments,
                edit_records: visibleEditRecords,
            }, resultJobOptions)
                .then((data) => {
                    if (seq !== transcriptSaveSeqRef.current) return;
                    setTranscriptUnsaved(false);
                    setTranscriptDirty(true);
                    setTranscriptSaveStatus('saved');
                    if (data?.result) {
                        setLastResult((prev) => (
                            prev?.task_id === result.task_id
                                ? {...prev, ...data.result}
                                : prev
                        ));
                    }
                })
                .catch(() => {
                    if (seq !== transcriptSaveSeqRef.current) return;
                    setTranscriptSaveStatus('failed');
                });
        }, 800);
        return () => clearTimeout(timer);
    }, [result?.task_id, transcriptUnsaved, transcript, segments, visibleEditRecords, canPersistResult, resultJobOptions]);

    useEffect(() => {
        if (!summaryUnsaved) return;
        if (!result?.task_id || !canPersistResult) {
            setSummarySaveStatus('local');
            return;
        }
        const seq = ++summarySaveSeqRef.current;
        setSummarySaveStatus('saving');
        const timer = setTimeout(() => {
            saveSummaryEdit(result.task_id, {
                summary_markdown: summaryDraft,
            }, resultJobOptions)
                .then((data) => {
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
                .catch(() => {
                    if (seq !== summarySaveSeqRef.current) return;
                    setSummarySaveStatus('failed');
                });
        }, 800);
        return () => clearTimeout(timer);
    }, [result?.task_id, summaryUnsaved, summaryDraft, canPersistResult, resultJobOptions]);

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

    const showToast = (msg, ok=true) => { setToast({msg,ok}); setTimeout(()=>setToast(null), 3000); };
    const buildAiOptions = (settings) => ({
        aiProvider: settings.aiProvider||'deepseek',
        aiModel: settings.aiModel||null,
        systemPrompt: resolveSystemPromptFromSettings(settings)||null,
        noteMode: settings.noteMode||'auto',
        speakerDiarization: !!settings.speakerDiarization,
        generateVisuals: !!settings.autoIllustrate,
        sttProvider: effectiveSttProvider(settings, runtimeConfig),
    });

    const presetLabel = (key) => presetDisplayLabel(key, loadSettings(), lang);

    const handlePromptKeyChange = (newKey) => {
        setPromptKey(newKey);
        const s = loadSettings();
        saveSettings({ ...s, promptPreset: newKey });
        if (newKey === 'default') setDefaultPromptEdit(getDefaultPromptBody({ ...s, promptPreset: newKey }));
        if (newKey === 'autoTranscriptNotes') setAutoTranscriptNotesEdit(getBuiltinExtraPromptBody('autoTranscriptNotes', { ...s, promptPreset: newKey }));
        if (newKey === 'meeting') setMeetingEdit(getBuiltinExtraPromptBody('meeting', { ...s, promptPreset: newKey }));
        if (newKey === 'research') setResearchEdit(getBuiltinExtraPromptBody('research', { ...s, promptPreset: newKey }));
        if (newKey === 'quickBullets') setQuickBulletsEdit(getBuiltinExtraPromptBody('quickBullets', { ...s, promptPreset: newKey }));
        if (newKey.startsWith('user_')) {
            const p = (s.userPromptPresets || []).find((x) => x.id === newKey);
            setUserPresetEdit(p?.prompt || '');
        }
    };

    const handleCustomTextChange = (val) => {
        setCustomText(val);
        const s = loadSettings();
        saveSettings({ ...s, customPromptText: val });
    };

    const handleDefaultPromptChange = (val) => {
        setDefaultPromptEdit(val);
        const s = loadSettings();
        saveSettings({ ...s, defaultPromptOverride: val });
    };

    const handleBuiltinExtraChange = (key, val) => {
        if (key === 'autoTranscriptNotes') setAutoTranscriptNotesEdit(val);
        else if (key === 'meeting') setMeetingEdit(val);
        else if (key === 'research') setResearchEdit(val);
        else if (key === 'quickBullets') setQuickBulletsEdit(val);
        const s = loadSettings();
        saveSettings({ ...s, promptOverrides: { ...(s.promptOverrides || {}), [key]: val } });
    };

    const resetBuiltinExtra = (key) => {
        if (!window.confirm(t('set.deleteBuiltinPromptConfirm'))) return;
        const s = loadSettings();
        const hidden = new Set(Array.isArray(s.hiddenPromptPresets) ? s.hiddenPromptPresets : []);
        hidden.add(key);
        const next = { ...s, hiddenPromptPresets: Array.from(hidden) };
        if (next.promptPreset === key) next.promptPreset = 'default';
        saveSettings(next);
        // 如果当前选中该模板，则切回默认，避免面板状态与选中项不一致
        if (promptKey === key) {
            setPromptKey('default');
            setDefaultPromptEdit(getDefaultPromptBody(next));
        }
        // 触发面板重新渲染：否则 hiddenPromptPresets 更新了但 UI 不会立刻消失
        setPresetListTick((x) => x + 1);
    };

    const handleUserPresetChange = (val) => {
        setUserPresetEdit(val);
        const s = loadSettings();
        const ups = (s.userPromptPresets || []).map((p) => (p.id === promptKey ? { ...p, prompt: val } : p));
        saveSettings({ ...s, userPromptPresets: ups });
    };

    const saveCustomAsPresetFromEditor = () => {
        const name = presetNameInput.trim();
        if (!name || !customText.trim()) {
            showToast(lang === 'zh' ? '请填写预设名称和提示词内容' : 'Enter a name and prompt text', false);
            return;
        }
        const s = loadSettings();
        const id = 'user_' + Date.now();
        const next = {
            ...s,
            userPromptPresets: [{ id, nameZh: name, nameEn: name, prompt: customText }, ...(s.userPromptPresets || [])],
        };
        saveSettings(next);
        setPresetNameInput('');
        setPresetListTick((x) => x + 1);
        showToast(t('set.presetSaved'));
    };

    const handleDeleteUserPreset = (id, e) => {
        e.stopPropagation();
        e.preventDefault();
        if (!window.confirm(t('set.deletePresetConfirm'))) return;
        const s = loadSettings();
        const ups = normalizeUserPresets(s).filter((p) => p.id !== id);
        const next = { ...s, userPromptPresets: ups };
        if (next.promptPreset === id) next.promptPreset = 'default';
        saveSettings(next);
        if (promptKey === id) {
            setPromptKey('default');
            setDefaultPromptEdit(getDefaultPromptBody(next));
        }
        setPresetListTick((x) => x + 1);
        showToast(lang === 'zh' ? '已删除预设' : 'Preset deleted', true);
    };

    const handleExportLark = async () => {
        if(!result || exporting) return;
        if (hosted?.blockResultExport?.({result, showToast, lang})) return;
        setExporting(true);
        let larkExportRoute = larkExportRouteFromSettings(loadSettings());
        try {
            const settings = loadSettings();
            larkExportRoute = larkExportRouteFromSettings(settings);
            if (await hosted?.prepareLarkExport?.({
                route: larkExportRoute,
                setFeishuReconnectRequired,
                setFeishuExportPromptOpen,
                showToast,
                lang,
            })) return;
            const fd = new FormData();
            fd.append('markdown', summary || '');
            fd.append('title', fileNameStem(resultDownloadName));
            fd.append('task_id', activeTaskId);
            if(result.source) fd.append('source_type', result.source);
            if(result.filename) fd.append('source_filename', result.filename);
            if(durSec > 0) fd.append('source_duration_seconds', String(durSec));
            fd.append('lark_export_route', larkExportRoute);
            fd.append('lark_via_cli', isLocalLarkExportRoute(larkExportRoute) ? 'true' : 'false');
            const headers = shouldUseLocalSingleUserClientId()
                ? localExecutionHeaders({localExecution: true, larkExportRoute})
                : localExecutionHeaders({larkExportRoute});
            const r = await apiFetch(`${API_BASE}/export-lark`, {method:'POST', headers, body:fd});
            const data = await r.json().catch(()=>({}));
            if(!r.ok) {
                const d = data.detail;
                const msg = Array.isArray(d) ? d.map(x=>x.msg||x).join('; ') : (d || 'Export failed');
                const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
                err.status = r.status;
                err.payload = data;
                throw err;
            }
            const exportUrl = data.url || null;
            if(!exportUrl && !data.dry_run) throw new Error(data.msg || 'No document URL returned');
            setLarkUrl(exportUrl);
            const dispTitle = data.doc_title || fileNameStem(resultDownloadName) || "Export";
            if(exportUrl) addLarkExport({url:exportUrl, title: dispTitle, timestamp:Date.now()});
            showToast(t('edit.exportDone'));
        } catch(err) {
            if (!(await hosted?.handleLarkExportError?.({
                error: err,
                route: larkExportRoute,
                setFeishuReconnectRequired,
                setFeishuExportPromptOpen,
                showToast,
                lang,
            }))) {
                showToast(t('edit.exportFail')+': '+err.message, false);
            }
        }
        finally { setExporting(false); }
    };

    const connectFeishuForExport = async () => {
        setFeishuExportConnecting(true);
        if (!hosted?.connectFeishuForExport) return;
        try {
            const nextUrl = `${window.location.pathname || '/editor'}${window.location.search || ''}`;
            const data = await hosted.connectFeishuForExport(nextUrl);
            window.location.assign(data.authorize_url);
        } catch {
            showToast(lang === 'zh'
                ? '飞书连接暂不可用，请下载 Markdown，或稍后再试。'
                : 'Feishu connection is unavailable. Download Markdown or try again later.', false);
            setFeishuExportConnecting(false);
        }
    };

    const handleRegenerate = async () => {
        setRegenerateConfirmOpen(false);
        if (isReadOnlyResult) {
            showToast(resultNotice || (lang === 'zh' ? '此结果不可修改。' : 'This result is read-only.'), false);
            return;
        }
        if(!transcript || regenerating) return;
        if (hosted?.blockRegenerate?.({result, showToast, lang})) return;
        setRegenerating(true);
        try {
            const settings = loadSettings();
            const fd = new FormData();
            fd.append('transcript', transcript);
            fd.append('task_id', activeTaskId);
            if(result.source) fd.append('source_type', result.source);
            if(result.filename) fd.append('source_filename', result.filename);
            if(durSec > 0) fd.append('source_duration_seconds', String(durSec));
            if(settings.aiProvider) fd.append('ai_provider', settings.aiProvider);
            if(settings.aiModel) fd.append('ai_model', settings.aiModel);
            if(settings.noteMode) fd.append('note_mode', settings.noteMode);
            const activePrompt = resolveSystemPromptFromSettings(settings);
            if(activePrompt) fd.append('system_prompt', activePrompt);
            fd.append('prompt_preset', settings.promptPreset || DEFAULT_PROMPT_PRESET);
            fd.append('prompt_preset_label', presetDisplayLabel(settings.promptPreset || DEFAULT_PROMPT_PRESET, settings, lang));
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
                        prompt_preset: data.prompt_preset || settings.promptPreset || DEFAULT_PROMPT_PRESET,
                        prompt_preset_label: data.prompt_preset_label || presetDisplayLabel(settings.promptPreset || DEFAULT_PROMPT_PRESET, settings, lang),
                        });
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
        const sttProvider = effectiveSttProvider(settings, runtimeConfig);
        if (!(await (hosted?.ensureSttReady?.({sttProvider, showToast, lang}) ?? true))) return;
        const retranscribeErrorMessage = (err) => {
            return hosted?.retranscribeErrorMessage?.({error: err, sttProvider, lang})
                || err?.message || (lang === 'zh' ? '重新转录失败' : 'Retranscription failed');
        };
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
            sttSpeed: settings.sttSpeed||'balanced',
            sttLanguage: 'auto',
            skipSummary: !!settings.skipAiSummary,
            exportToLark: !!settings.exportToLark,
            noteMode: settings.noteMode||'auto',
        });
        try {
            const resultData = await processVideoSSE(file, {
                taskId,
                sourceLastModifiedMs: file.lastModified||null,
                exportToLark: settings.exportToLark||false,
                larkExportRoute: larkExportRouteFromSettings(settings),
                larkViaCli: !!settings.larkViaCli,
                title: file.name.replace(/\.[^/.]+$/,""),
                ...buildAiOptions(settings),
                skipSummary: !!settings.skipAiSummary,
                sttProvider,
                sttModel,
                sttSpeed: settings.sttSpeed||'balanced',
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
                            cloudAudioSizeMb: ev.elevenlabs_audio_size_mb ?? prev.cloudAudioSizeMb,
                        } : null);
                if(ev.stage === 'transcript_ready' && ev.result) setLastResult(ev.result);
            });

            setLastResult(resultData);
            const larkUrl = resultData.lark_response?.url || null;
            addToHistory(resultToHistoryEntry(resultData, {taskId, name:file.name, requestedNoteMode: settings.noteMode||'auto'}));
            if(larkUrl) addLarkExport({url:larkUrl, title: resultData.lark_doc_title || fileNameStem(file.name), timestamp:Date.now()});
            setCurrentJob({fileName:file.name, stage:'done', progress:100});
            setTimeout(() => setCurrentJob(null), 3000);
            showToast(t('edit.retranscribeDone'));
        } catch(err) {
            setCurrentJob(null);
            showToast(retranscribeErrorMessage(err), false);
        } finally {
            setRetranscribing(false);
        }
    };

    const handleRetranscribe = () => {
        if (isReadOnlyResult) {
            showToast(resultNotice || (lang === 'zh' ? '此结果不可重新转录。' : 'This result cannot be retranscribed.'), false);
            return;
        }
        setRetranscribeConfirmOpen(true);
    };

    const confirmRetranscribe = async () => {
        setRetranscribeConfirmOpen(false);
        if (hosted?.blockRetranscribe?.({result, showToast, lang})) return;
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
        if(matchedLocalSourceFile) runRetranscribe(matchedLocalSourceFile);
        else if(result?.task_id && result?.source_file_available) {
            try {
                const sourceFile = await fetchJobSourceFile(result.task_id, result.filename || 'source', resultJobOptions);
                runRetranscribe(sourceFile);
            } catch (err) {
                if (result?.artifacts?.playback_audio) {
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
        } else if(result?.task_id && result?.artifacts?.playback_audio) {
            try {
                const audioFile = await fetchPlaybackAudioForRetranscribe();
                runRetranscribe(audioFile);
            } catch (err) {
                showToast(err.message || t('edit.retranscribeUnavailableTitle'), false);
                retranscribeInputRef.current?.click();
            }
        } else retranscribeInputRef.current?.click();
    };

    const handleMediaFileSelected = async (file) => {
        if (!file) return;
        setLastSourceFile(file);
        loadMediaFile(file);
        if (isReadOnlyResult) {
            showToast(resultNotice || (lang === 'zh' ? '此结果只会在当前浏览器播放。' : 'This result plays only in this browser.'));
            return;
        }
        if (!result?.task_id || !canPersistResult) return;
        try {
            const data = await uploadJobPlaybackAudio(result.task_id, file);
            if (data?.result) {
                setLastResult(data.result);
                addToHistory(resultToHistoryEntry(data.result, {
                    taskId: data.result.task_id || result.task_id,
                    name: data.result.filename || file.name,
                }));
            }
            showToast(lang === 'zh' ? '原音频已保存，下次打开不用重选。' : 'Source audio saved for next time.');
        } catch (err) {
            showToast(
                lang === 'zh'
                    ? `当前可播放，但保存失败，刷新后可能还要重选。${err?.message ? ` ${err.message}` : ''}`
                    : `Playback works for now, but saving failed. You may need to choose it again after refresh.${err?.message ? ` ${err.message}` : ''}`,
                false,
            );
        }
    };

    if(!result) return (
        <div className="ml-[var(--sidebar-offset)] min-h-dvh bg-[#f8f7fb] pb-8 text-[#111111] dark:bg-[#101010] dark:text-white/[0.92]">
            <main className="flex h-[calc(100vh-2rem)] items-center justify-center px-8">
                <div className="w-full max-w-xl rounded-[28px] border border-[#e4e0e0] bg-white px-8 py-10 text-center shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                    <SvgIcon name="edit_note" className="mb-4 text-6xl text-[#8a8a8a] dark:text-white/40"/>
                    <h2 className="mb-2 font-headline text-2xl font-extrabold text-[#111111] dark:text-white">{t('edit.noResult')}</h2>
                    <p className="mb-6 text-sm font-semibold leading-relaxed text-[#666] dark:text-white/60">{t('edit.noResultDesc')}</p>
                    <Link to="/agent" className="inline-flex items-center gap-2 rounded-[14px] bg-[#111111] px-6 py-3 text-sm font-bold text-white transition-colors hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85">
                        <SvgIcon name="monitoring" className="text-lg"/>{t('edit.chooseRecord')}
                    </Link>
                                            </div>
            </main>
                                                </div>
    );

    const recordDownload = (eventName, format) => {
        recordEvent({
            event_name: eventName,
            task_id: activeTaskId,
            source_type: result.source || null,
            source_filename: result.filename,
            source_duration_seconds: durSec,
            transcript_length: transcript.length,
            summary_length: summary.length,
            stage: "download",
            success: true,
            metadata: {format},
        });
    };
    const handleDownloadSourceVideo = async () => {
        if (!canDownloadSourceVideo || downloading) return;
        setDownloading('source_video');
        try {
            const file = matchedLocalSourceFile && isLikelyVideoFile(matchedLocalSourceFile)
                ? matchedLocalSourceFile
                : await fetchJobSourceFile(result.task_id, result.filename || `${resultDownloadName}.mp4`, resultJobOptions);
            downloadBrowserFile(file, result.filename || `${resultDownloadName}.mp4`);
            recordDownload('source_video_downloaded', 'video');
            showToast(t('dl.success'));
        } catch (err) {
            showToast(err.message || (lang === 'zh' ? '原视频不可用' : 'Source video unavailable'), false);
        } finally {
            setDownloading(null);
        }
    };
    const mediaProgress = playbackDuration > 0 ? Math.min(100, Math.max(0, mediaCurrentTime / playbackDuration * 100)) : 0;

    return (
    <div className="ml-[var(--sidebar-offset)] min-h-dvh bg-[#f8f7fb] pb-8 text-[#111111] dark:bg-[#101010] dark:text-white/[0.92]">
        {toast && (
            <div className={`fixed right-8 top-6 z-50 rounded-[16px] px-5 py-3 text-sm font-bold shadow-[0_18px_44px_-26px_rgba(17,17,17,.55)] ${toast.ok?'bg-[#111111] text-white dark:bg-white dark:text-[#111111]':'bg-red-600 text-white'}`}>
                {toast.msg}
                                            </div>
        )}
        {larkUrl && (
            <div className="fixed left-1/2 top-6 z-50 flex max-w-lg -translate-x-1/2 items-center gap-4 rounded-[20px] border border-[#d9eadf] bg-white px-6 py-4 shadow-[0_18px_44px_-26px_rgba(17,17,17,.55)] dark:border-emerald-400/20 dark:bg-[#151515]">
                <SvgIcon name="check_circle" className="text-2xl text-emerald-600 dark:text-emerald-300"/>
                <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-[#111111] dark:text-white">{t('edit.exportDone')}</p>
                    <a href={larkUrl} target="_blank" rel="noopener noreferrer" className="text-primary text-sm hover:underline truncate block">{larkUrl}</a>
                                                </div>
                <button onClick={()=>setLarkUrl(null)} className="flex-shrink-0 text-[#777] hover:text-[#111111] dark:text-white/50 dark:hover:text-white">
                    <SvgIcon name="close" className="text-sm"/>
                </button>
                                                </div>
        )}
        {feishuExportPromptOpen && (
            <FeishuExportPrompt
                onCancel={()=>setFeishuExportPromptOpen(false)}
                onConnect={connectFeishuForExport}
                connecting={feishuExportConnecting}
                reconnect={feishuReconnectRequired}
            />
        )}
        {regenerateConfirmOpen && (
            <RegenerateConfirmDialog
                transcriptTitle={rawEditorTitle}
                onCancel={()=>setRegenerateConfirmOpen(false)}
                onConfirm={handleRegenerate}
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
                <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4">
                    <div className="min-w-0 pr-2">
                        <h1
                            className="max-w-[18em] truncate whitespace-nowrap font-headline text-[clamp(1.55rem,1.8vw,2rem)] font-extrabold leading-tight text-[#111111] dark:text-white"
                            title={rawEditorTitle}
                            aria-label={rawEditorTitle}
                        >
                            {rawEditorTitle}
                        </h1>
                        {sttElapsedLabel && (
                            <p className="mt-1 truncate text-sm font-semibold leading-snug text-[#666] dark:text-white/60">
                                {sttElapsedLabel}
                            </p>
                        )}
                        {isReadOnlyResult && resultNotice && (
                            <div className="mt-2 flex max-w-2xl items-start gap-2 rounded-[12px] border border-[#d6dcff] bg-[#eef2ff] px-3 py-2 text-xs font-semibold leading-relaxed text-[#46536f] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white/72">
                                <SvgIcon name="info" className="mt-0.5 shrink-0 text-[15px] text-primary"/>
                                <p>
                                    {resultNotice}
                                </p>
                            </div>
                        )}
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                        <button
                            type="button"
                            onClick={()=>setPromptOpen(true)}
                            className="inline-flex h-10 items-center justify-center gap-1.5 rounded-[14px] border border-[#e4e0e0] bg-white px-3 text-xs font-bold text-[#111111] transition hover:bg-[#efeeee] active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.1]"
                        >
                            <SvgIcon name="tune" className="text-[17px]"/>
                            <span>{t('prompt.collapsed')}</span>
                        </button>
                        <button
                            type="button"
                            onClick={()=>setRegenerateConfirmOpen(true)}
                            disabled={isTransientResult||isReadOnlyResult||regenerating||!transcript}
                            className="inline-flex h-10 items-center justify-center gap-1.5 rounded-[14px] border border-[#e4e0e0] bg-white px-3 text-xs font-bold text-[#111111] transition hover:bg-[#efeeee] active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.1]"
                        >
                            <SvgIcon name={regenerating ? 'sync' : 'refresh'} className={`text-[17px] ${regenerating?'animate-spin':''}`}/>
                            <span>{regenerating ? t('edit.regenerating') : t('edit.regenerate')}</span>
                        </button>
                        <button
                            type="button"
                            onClick={handleRetranscribe}
                            disabled={isTransientResult||isReadOnlyResult||retranscribing||retranscribeBlockedByJob}
                            className="inline-flex h-10 items-center justify-center gap-1.5 rounded-[14px] border border-[#e4e0e0] bg-white px-3 text-xs font-bold text-[#666] transition hover:bg-[#efeeee] hover:text-[#111111] active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/65 dark:hover:bg-white/[0.1] dark:hover:text-white"
                        >
                            <SvgIcon name={retranscribing ? 'sync' : 'record_voice_over'} className={`text-[17px] ${retranscribing?'animate-spin':''}`}/>
                            <span>{retranscribing ? t('edit.retranscribing') : t('edit.retranscribe')}</span>
                        </button>
                        <button
                            type="button"
                            onClick={handleExportLark}
                            disabled={isTransientResult||exporting||!summary}
                            className="inline-flex h-10 items-center justify-center gap-1.5 rounded-[14px] bg-[#111111] px-4 text-xs font-extrabold text-white transition hover:bg-[#2a2a2a] active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/85"
                        >
                            <SvgIcon name={exporting ? 'sync' : 'cloud_upload'} className={`text-[17px] ${exporting?'animate-spin':''}`}/>
                            <span>{t('edit.export')}</span>
                        </button>
                    </div>
                </div>

                <PromptTemplateDialog
                    open={promptOpen}
                    onClose={()=>setPromptOpen(false)}
                    t={t}
                    lang={lang}
                    settings={loadSettings()}
                    promptKey={promptKey}
                    presetLabel={presetLabel}
                    handlePromptKeyChange={handlePromptKeyChange}
                    handleDeleteUserPreset={handleDeleteUserPreset}
                    resetBuiltinExtra={resetBuiltinExtra}
                    defaultPromptEdit={defaultPromptEdit}
                    handleDefaultPromptChange={handleDefaultPromptChange}
                    userPresetEdit={userPresetEdit}
                    handleUserPresetChange={handleUserPresetChange}
                    customText={customText}
                    handleCustomTextChange={handleCustomTextChange}
                    presetNameInput={presetNameInput}
                    setPresetNameInput={setPresetNameInput}
                    saveCustomAsPresetFromEditor={saveCustomAsPresetFromEditor}
                    autoTranscriptNotesEdit={autoTranscriptNotesEdit}
                    meetingEdit={meetingEdit}
                    researchEdit={researchEdit}
                    quickBulletsEdit={quickBulletsEdit}
                    handleBuiltinExtraChange={handleBuiltinExtraChange}
                />

                        <div className="flex min-h-0 flex-1 gap-4 overflow-hidden">
                            <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-[#e4e0e0] bg-white shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
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
                                                        onClick={()=>{ persistMediaPosition(); setTranscriptReviewMode('text'); }}
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
                                                        onClick={()=>{ if (canUseVideoReview) { persistMediaPosition(); setTranscriptReviewMode('video'); } }}
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
                                            <DropdownMenu
                                                trigger={
                                                    <button className="inline-flex h-8 items-center justify-center gap-1.5 rounded-[13px] bg-[#111111] px-3 text-xs font-bold text-white transition hover:bg-[#2a2a2a] active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/85">
                                                        <SvgIcon name="download" className="text-[17px]"/>
                                                        {lang === 'zh' ? '导出' : 'Export'}
                                                    </button>
                                                }
                                                items={[
                                                    {icon:'description', label:t('dl.txt'), badge:'TXT', onClick:()=>{dlTranscriptTxt(transcript,resultDownloadName); recordDownload('transcript_downloaded','txt'); showToast(t('dl.success'));}},
                                                    {icon:'subtitles', label:t('dl.srt'), badge:'SRT', disabled:segments.length===0, onClick:()=>{dlTranscriptSrt(segments,resultDownloadName); recordDownload('transcript_downloaded','srt'); showToast(t('dl.success'));}},
                                                    {icon:'closed_caption', label:t('dl.vtt'), badge:'VTT', disabled:segments.length===0, onClick:()=>{dlTranscriptVtt(segments,resultDownloadName); recordDownload('transcript_downloaded','vtt'); showToast(t('dl.success'));}},
                                                    {icon:'translate', label:t('dl.bilingualSrt'), badge:'双语 SRT', disabled:!hasBilingualTranscript, onClick:()=>{dlBilingualTranscriptSrt(bilingualTranscriptSegments,null,resultDownloadName); recordDownload('transcript_downloaded','bilingual_srt'); showToast(t('dl.success'));}},
                                                    {icon:'translate', label:t('dl.bilingualVtt'), badge:'双语 VTT', disabled:!hasBilingualTranscript, onClick:()=>{dlBilingualTranscriptVtt(bilingualTranscriptSegments,null,resultDownloadName); recordDownload('transcript_downloaded','bilingual_vtt'); showToast(t('dl.success'));}},
                                                    {icon:'video', label:t('dl.sourceVideo'), badge:'MP4', disabled:!canDownloadSourceVideo || downloading === 'source_video', onClick:handleDownloadSourceVideo},
                                                ]}
                                            />
                                        </div>
                                    </div>
                                </div>
                        {shouldShowVideoReview ? (
                            <div className="min-h-0 flex-1 overflow-hidden p-4">
                                <div className="flex h-full min-h-0 flex-col gap-3">
                                    <video
                                        ref={mediaRef}
                                        src={mediaUrl || undefined}
                                        controls
                                        className="max-h-[min(58vh,36rem)] w-full shrink-0 rounded-[18px] bg-black object-contain"
                                        onTimeUpdate={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration})}
                                        onLoadedMetadata={(e)=>restoreMediaPosition(e.currentTarget, e.currentTarget.duration || durSec || 0)}
                                        onSeeked={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true})}
                                        onPlay={()=>setMediaPlaying(true)}
                                            onPause={(e)=>{ updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true}); setMediaPlaying(false); }}
                                            onEnded={()=>setMediaPlaying(false)}
                                        />
                                    <div ref={transcriptScrollRef} className="hide-scrollbar min-h-[10rem] flex-1 overflow-y-auto rounded-[18px] border border-[#e4e0e0] bg-[#fbfbfb] px-4 py-2 dark:border-white/[0.12] dark:bg-white/[0.05]">
                                        <div className="divide-y divide-[#e4e0e0] dark:divide-white/[0.08]">
                                            {visibleTranscriptView === 'bilingual' && bilingualTranscriptSegments.length > 0 ? bilingualTranscriptSegments.map((seg,i) => (
                                                <div
                                                    key={`video-review-bilingual-${i}`}
                                                    ref={(node)=>{ if(node) segmentRefs.current[i]=node; }}
                                                    className={`grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 px-1 py-2 transition-colors ${i===activeSegmentIndex ? 'bg-[#eef2ff] dark:bg-white/[0.08]' : 'hover:bg-white/70 dark:hover:bg-white/[0.04]'}`}
                                                >
                                                    <button
                                                        type="button"
                                                        onClick={()=>seekToSegment(seg)}
                                                        className={`pt-[1px] text-left font-mono text-xs font-bold tabular-nums transition ${i===activeSegmentIndex ? 'text-primary dark:text-white' : 'text-[#8a8a8a] hover:text-primary dark:text-white/42 dark:hover:text-white'}`}
                                                    >
                                                        {fmtTime(seg.start)}
                                                    </button>
                                                    <div className="min-w-0">
                                                        <p className="whitespace-pre-wrap text-sm font-semibold leading-snug text-[#111111] dark:text-white">
                                                            {seg.text}
                                                        </p>
                                                        <p className="mt-1.5 border-l-2 border-primary/25 pl-3 text-sm font-semibold leading-snug text-[#666] dark:text-white/68">
                                                            {seg.text_zh}
                                                        </p>
                                                    </div>
                                                </div>
                                            )) : segments.map((seg,i) => (
                                                <div
                                                    key={`video-review-${i}`}
                                                    ref={(node)=>{ if(node) segmentRefs.current[i]=node; }}
                                                    className={`grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 px-1 py-2 transition-colors ${i===activeSegmentIndex ? 'bg-[#eef2ff] dark:bg-white/[0.08]' : 'hover:bg-white/70 dark:hover:bg-white/[0.04]'}`}
                                                >
                                                    <button
                                                        type="button"
                                                        onClick={()=>seekToSegment(seg)}
                                                        className={`pt-[1px] text-left font-mono text-xs font-bold tabular-nums transition ${i===activeSegmentIndex ? 'text-primary dark:text-white' : 'text-[#8a8a8a] hover:text-primary dark:text-white/42 dark:hover:text-white'}`}
                                                    >
                                                        {fmtTime(seg.start)}
                                                    </button>
                                                    <textarea
                                                        data-transcript-segment="true"
                                                        value={seg.text || ''}
                                                        ref={(node)=>{ if(node) autoSizeTextarea(node); }}
                                                        onChange={(e)=>{ autoSizeTextarea(e.target); handleSegmentTextChange(i, e.target.value); }}
                                                        readOnly={isReadOnlyResult}
                                                        onFocus={()=>setFollowPlayback(false)}
                                                        rows={1}
                                                        className="min-h-[1.45rem] w-full resize-none overflow-hidden border-none bg-transparent p-0 text-sm font-semibold leading-snug text-[#111111] focus:ring-0 dark:text-white"
                                                    />
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <>
                        <div ref={transcriptScrollRef} className="hide-scrollbar min-h-0 flex-1 space-y-1.5 overflow-y-auto p-3">
                            {visibleTranscriptView === 'bilingual' && bilingualTranscriptSegments.length > 0 ? bilingualTranscriptSegments.map((seg,i) => (
                                <div
                                    key={`bilingual-${i}`}
                                    ref={(node)=>{ if(node) segmentRefs.current[i]=node; }}
                                    className={`grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 rounded-[16px] px-3 py-2.5 transition-colors ${i===activeSegmentIndex && mediaUrl ? 'bg-[#eef2ff] dark:bg-white/[0.1]' : 'hover:bg-[#f8f7fb] dark:hover:bg-white/[0.06]'}`}
                                >
                                    <button
                                        type="button"
                                        onClick={()=>seekToSegment(seg)}
                                        className={`flex flex-col items-start justify-start pt-[1px] font-mono text-xs tabular-nums transition ${i===activeSegmentIndex && mediaUrl ? 'font-bold text-primary' : 'text-[#777] hover:text-primary dark:text-white/45'}`}
                                    >
                                        <span className="block">{fmtTime(seg.start)}</span>
                                        <span className="mt-0.5 block text-[10px] opacity-70">{fmtTime(seg.end)}</span>
                                    </button>
                                    <div className="min-w-0 flex-1">
                                        <p className="whitespace-pre-wrap text-sm font-medium leading-snug text-[#111111] dark:text-white">
                                            {seg.text}
                                        </p>
                                        <p className="mt-1.5 border-l-2 border-primary/25 pl-3 text-sm font-medium leading-snug text-[#666] dark:text-white/65">
                                            {seg.text_zh}
                                        </p>
                                    </div>
                                </div>
                            )) : segments.length > 0 ? segments.map((seg,i) => (
                                <div
                                    key={i}
                                    ref={(node)=>{ if(node) segmentRefs.current[i]=node; }}
                                    className={`group grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 rounded-[16px] px-3 py-2.5 transition-colors ${i===activeSegmentIndex && mediaUrl ? 'bg-[#eef2ff] dark:bg-white/[0.1]' : 'hover:bg-[#f8f7fb] dark:hover:bg-white/[0.06]'}`}
                                >
                                    <button
                                        type="button"
                                        onClick={()=>seekToSegment(seg)}
                                        className={`pt-[1px] text-left font-mono text-xs tabular-nums transition ${i===activeSegmentIndex && mediaUrl ? 'font-bold text-primary' : 'text-[#8a8a8a] hover:text-primary dark:text-white/40'}`}
                                    >
                                        {fmtTime(seg.start)}
                                    </button>
                                    <div className="min-w-0 flex-1">
                                        <textarea
                                            data-transcript-segment="true"
                                            value={seg.text || ''}
                                            ref={autoSizeTextarea}
                                                        onChange={(e)=>{ autoSizeTextarea(e.target); handleSegmentTextChange(i, e.target.value); }}
                                                        readOnly={isReadOnlyResult}
                                            onFocus={()=>setFollowPlayback(false)}
                                            rows={1}
                                            className="min-h-[1.75rem] w-full resize-none overflow-hidden border-none bg-transparent p-0 text-sm font-medium leading-snug text-[#111111] focus:ring-0 dark:text-white"
                                        />
                                    </div>
                                        </div>
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
                                                ? '当前结果没有时间戳分段。重新转录原音频后会恢复逐段校对。'
                                                : 'This result has no timestamped segments. Retranscribe the source audio to restore segment review.'}>
                                                {lang==='zh'
                                                    ? '当前结果没有时间戳分段。重新转录原音频后会恢复逐段校对。'
                                                    : 'No timestamped segments. Retranscribe the source audio to restore segment review.'}
                                            </p>
                                        </div>
                                    <textarea
                                        value={transcript}
                                        onChange={(e)=>handlePlainTranscriptChange(e.target.value)}
                                        readOnly={isReadOnlyResult}
                                        onFocus={()=>setFollowPlayback(false)}
                                        className="min-h-[320px] w-full flex-1 resize-none whitespace-pre-wrap border-none bg-transparent p-0 text-sm font-medium leading-relaxed text-[#111111] focus:ring-0 dark:text-white"
                                    />
                                    </div>
                                )}
                                </div>
                                <div className="border-t border-[#e4e0e0] bg-[#fbfbfb]/90 p-4 dark:border-white/[0.12] dark:bg-white/[0.04]">
                                        <video
                                            ref={mediaRef}
                                            src={mediaUrl || undefined}
                                            className="hidden"
                                            onTimeUpdate={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration})}
                                            onLoadedMetadata={(e)=>restoreMediaPosition(e.currentTarget, e.currentTarget.duration || durSec || 0)}
                                            onSeeked={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true})}
                                            onPlay={()=>setMediaPlaying(true)}
                                            onPause={(e)=>{ updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true}); setMediaPlaying(false); }}
                                            onEnded={()=>setMediaPlaying(false)}
                                        />
                                    {mediaUrl ? (
                                        <div className="space-y-3">
                                            <div className="flex items-center gap-3">
                                                <button type="button" onClick={togglePlayback} className="flex h-9 w-9 items-center justify-center rounded-[13px] bg-[#111111] text-white transition hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85">
                                                    <SvgIcon name={mediaPlaying ? 'pause' : 'play_arrow'} className="text-lg"/>
                                                </button>
                                                <button type="button" onClick={()=>setFollowPlayback(v=>!v)} className={`rounded-[12px] px-2.5 py-1.5 text-xs font-bold transition ${followPlayback?'bg-[#eef2ff] text-primary dark:bg-white/[0.1] dark:text-white':'bg-[#efeeee] text-[#666] dark:bg-white/[0.08] dark:text-white/60'}`}>
                                                    {t('edit.followPlayback')}
                                                </button>
                                                <span className="text-xs font-mono text-on-surface-variant ml-auto">{fmtTime(mediaCurrentTime)} / {fmtTime(playbackDuration || mediaCurrentTime)}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min="0"
                                                max={Math.max(1, playbackDuration)}
                                                step="0.1"
                                                    value={Math.min(mediaCurrentTime, Math.max(1, playbackDuration))}
                                                    onChange={(e)=>{
                                                        const next = Number(e.target.value) || 0;
                                                        seekMediaTo(next);
                                                    }}
                                                className="w-full accent-primary"
                                                style={{background:`linear-gradient(90deg, #3B82F6 ${mediaProgress}%, var(--c-surface-container-highest) ${mediaProgress}%)`}}
                                            />
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
                            </>
                        )}
                            </section>

                            <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-[#e4e0e0] bg-white shadow-[0_18px_44px_-34px_rgba(17,17,17,.55)] xl:w-[38rem] xl:flex-none 2xl:w-[42rem] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
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
                                        <DropdownMenu
                                            trigger={
                                                <button disabled={!summary || !!downloading} className="inline-flex h-8 items-center justify-center gap-1.5 rounded-[13px] border border-[#e4e0e0] bg-white px-3 text-xs font-bold text-[#111111] transition hover:bg-[#efeeee] disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.1]">
                                                    <SvgIcon name={downloading ? 'sync' : 'download'} className={`text-sm ${downloading?'animate-spin':''}`}/>
                                                    {downloading ? t('dl.generating') : t('dl.summary')}
                                            </button>
                                            }
                                            items={[
                                                {icon:'description', label:t('dl.txt'), badge:'TXT', disabled:!summary, onClick:()=>{dlSummaryTxt(summary,resultDownloadName); recordDownload('summary_downloaded','txt'); showToast(t('dl.success'));}},
                                                {icon:'markdown', label:t('dl.md'), badge:'MD', disabled:!summary, onClick:()=>{dlSummaryMd(summary,resultDownloadName); recordDownload('summary_downloaded','md'); showToast(t('dl.success'));}},
                                                {divider:true},
                                                {icon:'picture_as_pdf', label:t('dl.pdf'), badge:'PDF', disabled:!summary, onClick:async()=>{
                                                    setDownloading('pdf');
                                                    try{ await dlSummaryPdf(summary,resultDownloadName); recordDownload('summary_downloaded','pdf'); showToast(t('dl.pdfPrintOpened')); }catch(e){showToast(e.message,false);}
                                                    finally{setDownloading(null);}
                                                }},
                                                {icon:'article', label:t('dl.word'), badge:'DOCX', disabled:!summary, onClick:async()=>{
                                                    setDownloading('docx');
                                                    try{ await dlSummaryWord(summary,resultDownloadName); recordDownload('summary_downloaded','docx'); showToast(t('dl.success')); }catch(e){showToast(e.message,false);}
                                                    finally{setDownloading(null);}
                                                }},
                                            ]}
                                        />
                                    </div>
                                </div>
                                {hasEditableSummary && canEditSummary ? (
                                    <>
                                        <RichNoteEditor
                                            key={summaryResultKey}
                                            lang={lang}
                                            markdown={summaryMarkdownForEditor}
                                            onChange={handleSummaryChange}
                                        />
                                        <div
                                            ref={summaryRef}
                                            className="fixed left-[-10000px] top-0 w-[720px] bg-white p-6 text-black"
                                            dangerouslySetInnerHTML={{__html: renderedSummary}}
                                        />
                                    </>
                                ) : (
                                    <div className="hide-scrollbar min-h-0 flex-1 overflow-y-auto p-6 text-[#111111] dark:text-white">
                                    {hasEditableSummary ? (
                                        <div
                                            ref={summaryRef}
                                            className="max-w-none text-base font-semibold leading-8 text-[#111111] dark:text-white [&_a]:text-primary [&_blockquote]:border-l-4 [&_blockquote]:border-primary/30 [&_blockquote]:pl-4 [&_blockquote]:text-[#555] dark:[&_blockquote]:text-white/70 [&_h1]:mb-4 [&_h1]:mt-6 [&_h1]:font-headline [&_h1]:text-2xl [&_h1]:font-extrabold [&_h2]:mb-3 [&_h2]:mt-6 [&_h2]:font-headline [&_h2]:text-xl [&_h2]:font-extrabold [&_h3]:mb-2 [&_h3]:mt-5 [&_h3]:font-headline [&_h3]:text-lg [&_h3]:font-extrabold [&_li]:my-1.5 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:my-3 [&_strong]:font-extrabold [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-6"
                                            dangerouslySetInnerHTML={{__html: renderedSummary}}
                                        />
                                    ) : result.summary_skipped ? (
                                        <p className="text-sm italic text-[#666] dark:text-white/60">{t('edit.summarySkipped')}</p>
                                    ) : result.summary_status === 'failed' || result.summary_error ? (
                                        <div className="space-y-2 text-sm text-[#666] dark:text-white/60">
                                            <p className="italic">{t('edit.summaryFailed')}</p>
                                            {summaryFailureHint && (
                                                <p className="rounded-[14px] border border-error/20 bg-error-container px-3 py-2 text-xs font-semibold leading-relaxed text-on-error-container">
                                                    {summaryFailureHint}
                                                </p>
                                            )}
                                        </div>
                                    ) : (
                                        <p className="text-sm italic text-[#666] dark:text-white/60">{t('edit.summaryPending')}</p>
                                    )}
                                    </div>
                                )}
                                <div className="flex justify-end border-t border-[#e4e0e0] bg-[#fbfbfb] px-4 py-2 dark:border-white/[0.12] dark:bg-white/[0.04]">
                                    <Link to={agentWorkflowHref} className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-[12px] border border-[#dedada] bg-white px-3 text-[12px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]">
                                        <SvgIcon name="route" className="text-sm"/>
                                        {lang === 'zh' ? '处理记录' : 'Processing records'}
                                    </Link>
                                </div>
                            </section>
                        </div>
                    </div>
                </main>
            </div>
        );
};

/* ═══════════════ Admin ═══════════════ */

export default Editor;
