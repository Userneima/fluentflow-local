// The page's working copy of the transcript, moved out of editor.jsx: reading
// the task's full result from the service when the page holds only a light
// copy (the note's full text included, which makes the note editable:
// noteReadFor), resetting the copy when another result opens, the edit
// handlers, and what the transcript pane shows from it (paragraphs, the
// bilingual view, the edit records). Saving the edits is editor-saves.js.
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {
    buildTranscriptEditRecords,
    composeTranscriptText,
    isLocalHistoryResult,
    pickDisplayTranscriptSegments,
    pickTranscriptBaselineSegments,
    pickTranscriptSegments,
} from '../app/shared.jsx';
import {markBackendJob} from '../lib/taskState.js';
import {filterVisibleEditRecords, groupTranscriptParagraphs, mergeNoteFields, resultIdentity} from './editor-helpers.js';

export const useResultTranscript = ({result, setLastResult, getJob, resultJobOptions, noteReadFor, setNoteReadFor, ingestJobs}) => {
    const hydratedTaskIdsRef = useRef(new Set());
    // Effects keyed on a derived string (resultKey) read the latest result
    // through this ref: listing `result` itself as a dependency would re-run
    // them on every edit that syncs into the provider.
    const resultRef = useRef(result);
    resultRef.current = result;
    const noteReadForRef = useRef(noteReadFor);
    noteReadForRef.current = noteReadFor;
    // Memoised: it copies every sentence, and the page renders on each keystroke.
    const resultSegmentCount = useMemo(() => pickTranscriptSegments(result).length, [result]);
    const resultTextLength = (result?.transcript_text || '').length;
    const resultKey = result
        ? `${result.task_id || result.filename || 'current_result'}:${result.transcript_edited ? 'edited' : `${resultSegmentCount}:${resultTextLength}`}`
        : 'empty_result';
    const editTargetRef = useRef('');
    const [editedSegments, setEditedSegments] = useState([]);
    const editedSegmentsRef = useRef(editedSegments);
    const transcriptComposingRef = useRef(false);
    editedSegmentsRef.current = editedSegments;
    // The segments the provider last received. Edit records are diffed from
    // these, not from every keystroke.
    const [syncedSegments, setSyncedSegments] = useState([]);
    const [editedTranscript, setEditedTranscript] = useState('');
    const [baselineSegments, setBaselineSegments] = useState([]);
    const [transcriptDirty, setTranscriptDirty] = useState(false);
    const [transcriptUnsaved, setTranscriptUnsaved] = useState(false);
    const [transcriptSaveStatus, setTranscriptSaveStatus] = useState('idle');
    const [hydratingResult, setHydratingResult] = useState(false);
    const [hydrationFailed, setHydrationFailed] = useState(false);
    const [transcriptView, setTranscriptView] = useState('bilingual');

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
                // Its 置顶 and 学完了 too, as when the address opens a task.
                if (job.task_id) ingestJobs?.([markBackendJob(job)]);
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
    }, [resultKey, transcriptUnsaved, resultJobOptions, getJob, setLastResult, setNoteReadFor, ingestJobs]);

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
        // After this page's own save the result holds the sentences just
        // saved; the list on the page is kept as it is, so nothing under the
        // caret is re-laid.
        const shown = editedSegmentsRef.current;
        const unchanged = shown.length === sourceSegments.length && sourceSegments.every((seg, i) => (
            seg.text === shown[i].text && seg.start === shown[i].start && seg.end === shown[i].end && seg.speaker === shown[i].speaker
        ));
        if (!unchanged) setEditedSegments(sourceSegments.map((seg) => ({...seg})));
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
    const displayTranscriptSegments = useMemo(() => pickDisplayTranscriptSegments(result, segments), [result, segments]);
    const transcriptParagraphs = useMemo(
        () => groupTranscriptParagraphs(segments),
        [segments],
    );
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

    return {
        segments,
        transcript,
        editedSegments,
        baselineSegments,
        setSyncedSegments,
        transcriptDirty,
        setTranscriptDirty,
        transcriptUnsaved,
        setTranscriptUnsaved,
        transcriptSaveStatus,
        setTranscriptSaveStatus,
        transcriptComposingRef,
        editTargetRef,
        hydrationFailed,
        setHydrationFailed,
        applyTranscriptEdit,
        handleSegmentTextChange,
        handlePlainTranscriptChange,
        visibleEditRecords,
        transcriptParagraphs,
        bilingualTranscriptSegments,
        hasBilingualTranscript,
        visibleTranscriptView,
        setTranscriptView,
        visibleTranscriptSegments,
        isTranscriptHydrationPending,
        isTranscriptHydrationFailed,
    };
};
