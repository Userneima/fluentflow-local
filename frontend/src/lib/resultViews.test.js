import { describe, expect, it } from 'vitest';
import {
    hasNote,
    isPartialResult,
    normalizedBodyFields,
    noteForDisplay,
    noteForEditing,
    noteLength,
    toPreviewResult,
    transcriptForDisplay,
    transcriptForEditing,
    transcriptLength,
} from './resultViews.js';

// The rule this module exists to enforce: an editor can only obtain text
// through `*ForEditing`, and those return null for anything that is not the
// record. A 240-character preview therefore cannot become something autosave
// writes back over a 13k-character note.

const RECORD = {
    task_id: 't1',
    summary_markdown: '# 完整笔记\n\n正文很长。',
    transcript_text: '完整转录。'.repeat(100),
};

const LIST_ROW = {
    task_id: 't1',
    result_partial: true,
    summary_preview: '# 完整笔记\n\n正文',
    summary_markdown_chars: 13021,
    transcript_text_preview: '完整转录。'.repeat(5),
    transcript_text_chars: 49046,
};

describe('the wall between a record and a preview', () => {
    it('hands the editor the note only when it holds the record', () => {
        expect(noteForEditing(RECORD)).toBe(RECORD.summary_markdown);
        expect(noteForEditing(LIST_ROW)).toBeNull();
    });

    it('hands the editor the transcript only when it holds the record', () => {
        expect(transcriptForEditing(RECORD)).toBe(RECORD.transcript_text);
        expect(transcriptForEditing(LIST_ROW)).toBeNull();
    });

    it('has nothing to edit without a result', () => {
        expect(noteForEditing(null)).toBeNull();
        expect(transcriptForEditing(undefined)).toBeNull();
    });

    it('recognises which shape it was handed', () => {
        expect(isPartialResult(LIST_ROW)).toBe(true);
        expect(isPartialResult(RECORD)).toBe(false);
        expect(isPartialResult(null)).toBe(false);
    });
});

describe('read-only display', () => {
    it('shows the body when there is one and the preview otherwise', () => {
        expect(noteForDisplay(RECORD)).toBe(RECORD.summary_markdown);
        expect(noteForDisplay(LIST_ROW)).toBe(LIST_ROW.summary_preview);
        expect(transcriptForDisplay(LIST_ROW)).toBe(LIST_ROW.transcript_text_preview);
    });

    it('reports the real size even when only a preview is present', () => {
        expect(noteLength(LIST_ROW)).toBe(13021);
        expect(transcriptLength(LIST_ROW)).toBe(49046);
        expect(noteLength(RECORD)).toBe(RECORD.summary_markdown.length);
    });

    it('counts a note as present from a preview alone, and absent when empty', () => {
        expect(hasNote(LIST_ROW)).toBe(true);
        expect(hasNote(RECORD)).toBe(true);
        expect(hasNote({task_id: 't', summary_markdown: ''})).toBe(false);
        expect(hasNote({})).toBe(false);
    });
});

describe('normalizedBodyFields', () => {
    it('never lets a preview stand in for the body', () => {
        // The exact laundering that put a 240-char stub under the name that
        // means "the note".
        const fields = normalizedBodyFields(LIST_ROW);
        expect(fields.summary_markdown).toBe('');
        expect(fields.transcript_text).toBe('');
    });

    it('passes the body straight through', () => {
        expect(normalizedBodyFields(RECORD)).toEqual({
            summary_markdown: RECORD.summary_markdown,
            transcript_text: RECORD.transcript_text,
        });
    });
});

describe('toPreviewResult', () => {
    it('drops the body fields entirely rather than blanking them', () => {
        const preview = toPreviewResult(RECORD, {maxChars: 8});
        expect('summary_markdown' in preview).toBe(false);
        expect('transcript_text' in preview).toBe(false);
        expect(preview.result_partial).toBe(true);
    });

    it('keeps a readable preview and the real sizes', () => {
        const preview = toPreviewResult(RECORD, {maxChars: 8});
        expect(preview.summary_preview).toBe(RECORD.summary_markdown.slice(0, 8));
        expect(preview.summary_markdown_chars).toBe(RECORD.summary_markdown.length);
        expect(preview.transcript_text_chars).toBe(RECORD.transcript_text.length);
    });

    it('round-trips a row that is already a preview without losing its sizes', () => {
        const preview = toPreviewResult(LIST_ROW);
        expect(preview.summary_preview).toBe(LIST_ROW.summary_preview);
        expect(preview.summary_markdown_chars).toBe(13021);
        expect(noteForEditing(preview)).toBeNull();
    });

    it('leaves a non-object alone', () => {
        expect(toPreviewResult(null)).toBeNull();
    });
});
