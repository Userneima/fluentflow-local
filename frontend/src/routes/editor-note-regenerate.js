// Writing or rewriting the note from the editor, moved out of editor.jsx:
// who writes it (lib/noteWriter.js), whether Claude can rewrite this task
// from its frames, the dialog's copy, and the rewrite itself: Claude in the
// background (followed by editor-note-polling.js through `noteWatch`), or the
// text model at once.
import {useEffect, useMemo, useRef, useState} from 'react';
import {API_BASE, apiFetch} from '../app/shared.jsx';
import {noteWriterLabel, resolveNoteWriter} from '../lib/noteWriter.js';
import {mergeNoteFields, regenerateDialogCopy, visualNoteStartErrorMessage} from './editor-helpers.js';

export const useNoteRegeneration = ({
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
}) => {
    const [regenerating, setRegenerating] = useState(false);
    const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false);
    // The rewrite this page started, followed by useNotePolling.
    const [noteWatch, setNoteWatch] = useState(null);
    const resultJobOptionsRef = useRef(resultJobOptions);
    resultJobOptionsRef.current = resultJobOptions;

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

    return {
        regenerating,
        setRegenerating,
        regenerateConfirmOpen,
        setRegenerateConfirmOpen,
        noteWatch,
        setNoteWatch,
        noteWriter,
        regenerateCopy,
        handleRegenerate,
    };
};
