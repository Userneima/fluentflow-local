// The editor following a task until its note is in, moved out of editor.jsx:
// a task carried on from the header (接着做 / 重做这一步, useTaskResume) until
// its steps have all ended; a note still being written, or a rewrite this page
// started (editor-note-regenerate.js), fetched again every few seconds until it
// settles; and after it an automatic Feishu export until its link or its
// failure is in.
import {useEffect, useRef, useState} from 'react';
import {useTaskResume} from '../lib/useTaskResume.js';
import {
    NOTE_POLL_INTERVAL_MS,
    noteIsBeingWritten,
    noteWritingSettled,
    claudeRewriteSettled,
    mergeNoteFields,
    mergeLarkFields,
    AUTO_EXPORT_WAIT_MS,
    autoExportPending,
    larkExportSettled,
} from './editor-helpers.js';

export const useResumeWatch = ({taskId, getJobActivity, resumeJob, jobOptions, lang, showToast}) => {
    // A task carried on from the header (接着做 / 重做这一步): {taskId} until
    // its steps have all ended, when its note is taken like any other.
    const [resumeWatch, setResumeWatch] = useState(null);
    // Carrying a stopped task on, or redoing a step that failed (header button).
    const taskResume = useTaskResume({
        taskId,
        getJobActivity,
        resumeJob,
        jobOptions,
        lang,
        onResumed: () => {
            // The page follows the task until it is done and shows its note.
            if (taskId) setResumeWatch({taskId});
            showToast(lang === 'zh' ? '已排进队列，从这一步接着做' : 'Queued: carrying on from this step');
        },
        onError: (err) => showToast(err?.message || String(err), false),
    });

    return {resumeWatch, setResumeWatch, taskResume};
};

// A note still being written — the automatic one after transcription, or a
// Claude rewrite started from this page — is fetched again every few
// seconds until it settles, then shown. `noteWatch` is the rewrite this page
// started: {taskId, requestedAtMs, sawRunning}.
export const useNotePolling = ({
    result,
    getJob,
    setLastResult,
    showToast,
    resultJobOptions,
    summaryUnsaved,
    noteCellOpenRef,
    noteWatch,
    setNoteWatch,
    setRegenerating,
    resumeWatch,
    setResumeWatch,
    lang,
    t,
}) => {
    const langRef = useRef(lang);
    langRef.current = lang;
    const tRef = useRef(t);
    tRef.current = t;
    const resumeWatchRef = useRef(resumeWatch);
    resumeWatchRef.current = resumeWatch;
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
        || resumeWatch?.taskId === pollTaskId
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
        if (resumeWatchRef.current && resumeWatchRef.current.taskId !== pollTaskId) {
            setResumeWatch(null);
        }
    }, [pollTaskId, setNoteWatch, setRegenerating, setResumeWatch]);
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
            // Carried on: wait until no step of the task is working any more,
            // then take its note (written again, or left as it was).
            const resumeWait = resumeWatchRef.current?.taskId === taskId ? resumeWatchRef.current : null;
            if (resumeWait) {
                if (job?.activity?.busy !== false) return;
                if (summaryUnsavedRef.current || noteCellOpenRef.current) return;
                stop();
                setResumeWatch(null);
                setLastResult((prev) => (prev?.task_id === taskId ? mergeNoteFields(prev, fresh) : prev));
                return;
            }
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
    }, [notePolling, pollTaskId, getJob, setLastResult, showToast, noteCellOpenRef, setNoteWatch, setRegenerating, setResumeWatch]);
};
