// The note pane's working copy of the note, moved out of editor.jsx: the
// draft and its save label, whether the page has read the full note and may
// edit it (noteReady), a table cell open in the editor holding typing back,
// and the note as shown (screenshots on or off). Saving is editor-saves.js.
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {isLocalHistoryResult, simpleMd} from '../app/shared.jsx';
import {resultIdentity} from './editor-helpers.js';

export const useNoteDraft = ({result, setLastResult, noteReadFor, lang}) => {
    const resultRef = useRef(result);
    resultRef.current = result;
    const [visualEvidenceVisible, setVisualEvidenceVisible] = useState(true);
    const summaryDraftResultKeyRef = useRef('');
    const summaryResultKey = resultIdentity(result);
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

    return {
        summary,
        summaryDraft,
        summaryUnsaved,
        setSummaryUnsaved,
        summarySaveStatus,
        setSummarySaveStatus,
        summarySaveLabel,
        summaryResultKey,
        summaryMarkdownForEditor,
        noteCellOpenRef,
        deferredNoteReloadRef,
        handleNoteEditingChange,
        handleSummaryChange,
        hasEditableSummary,
        noteReady,
        inlineVisualEvidenceCount,
        hasInlineVisualEvidence,
        visualEvidenceVisible,
        setVisualEvidenceVisible,
        renderedSummary,
    };
};
