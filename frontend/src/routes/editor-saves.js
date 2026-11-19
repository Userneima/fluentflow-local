// Saving the editor's edits, moved out of editor.jsx: the transcript and the
// note are each saved 800ms after typing pauses, one save of a kind at a
// time, each naming the version it was made on; a save refused as made on an
// older copy loads what the task holds now. The working copies are
// editor-transcript-state.js and editor-note-draft.js.
import {useCallback, useEffect, useRef} from 'react';
import {buildTranscriptEditRecords} from '../app/shared.jsx';
import {filterVisibleEditRecords, resultIdentity} from './editor-helpers.js';

export const useContentSaves = ({
    result,
    setLastResult,
    getJob,
    saveTranscriptEdit,
    saveSummaryEdit,
    resultJobOptions,
    showToast,
    transcriptDraft,
    noteDraft,
}) => {
    const {
        segments,
        transcript,
        baselineSegments,
        transcriptUnsaved,
        setTranscriptUnsaved,
        setSyncedSegments,
        setTranscriptDirty,
        setTranscriptSaveStatus,
        transcriptComposingRef,
        editTargetRef,
    } = transcriptDraft;
    const {
        summaryDraft,
        summaryUnsaved,
        setSummaryUnsaved,
        setSummarySaveStatus,
        noteCellOpenRef,
        deferredNoteReloadRef,
    } = noteDraft;
    const resultRef = useRef(result);
    resultRef.current = result;
    const resultJobOptionsRef = useRef(resultJobOptions);
    resultJobOptionsRef.current = resultJobOptions;
    const transcriptSaveSeqRef = useRef(0);
    const summarySaveSeqRef = useRef(0);
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
        let timer = null;
        const sync = () => {
            // Half-typed pinyin is not the user's text yet: wait until the
            // input method hands over the characters.
            if (transcriptComposingRef.current) { timer = setTimeout(sync, 300); return; }
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
        };
        timer = setTimeout(sync, 800);
        return () => clearTimeout(timer);
    }, [transcriptUnsaved, transcript, segments, baselineSegments, resultJobOptions, saveTranscriptEdit, setLastResult, setSyncedSegments, setTranscriptDirty, setTranscriptSaveStatus, setTranscriptUnsaved, transcriptComposingRef, editTargetRef]);

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
    }, [result?.task_id, summaryUnsaved, summaryDraft, resultJobOptions, saveSummaryEdit, setLastResult, setSummarySaveStatus, setSummaryUnsaved, noteCellOpenRef, deferredNoteReloadRef]);
};
