// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { claudeRewriteSettled, mergeNoteFields, noteCameFromClaude, playbackMediaChoice, shouldKeepVideoReviewMounted } from './editor-helpers.js';

describe('shouldKeepVideoReviewMounted', () => {
    it('keeps the player mounted while playback is between subtitle segments', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'video', activeSegmentIndex: -1})).toBe(true);
    });

    it('does not render the video-review layout after switching back to text review', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'text', activeSegmentIndex: 3})).toBe(false);
    });
});

// The player must load the file the transcript belongs to. Playing the other one
// is the failure nobody would report as a bug: every seek and every highlighted
// line drifts further out the longer it plays, and the screen says nothing.
describe('playbackMediaChoice', () => {
    const cut = {kind: 'debreath_media', filename: 'debreath/lecture_debreath.mp4'};

    it('loads the cut file when the transcript was made from it', () => {
        expect(playbackMediaChoice({
            task_id: 't1',
            source_file_available: true,
            transcript_media: 'debreath_media',
            artifacts: {debreath_media: cut},
        })).toEqual({kind: 'debreath_media', filename: cut.filename, isCut: true});
    });

    it('loads the recording when the transcript belongs to it, cut file or not', () => {
        expect(playbackMediaChoice({
            task_id: 't1',
            filename: 'lecture.mp4',
            source_file_available: true,
            transcript_media: 'source',
            artifacts: {debreath_media: cut},
        })).toEqual({kind: 'source', filename: 'lecture.mp4', isCut: false});
    });

    it('does not reach for a cut file that is not there any more', () => {
        expect(playbackMediaChoice({
            task_id: 't1',
            filename: 'lecture.mp4',
            source_file_available: true,
            transcript_media: 'debreath_media',
            artifacts: {},
        })).toEqual({kind: 'source', filename: 'lecture.mp4', isCut: false});
    });

    it('treats an old result with neither field as the recording', () => {
        expect(playbackMediaChoice({task_id: 't1', filename: 'a.mp4', source_file_available: true}))
            .toEqual({kind: 'source', filename: 'a.mp4', isCut: false});
    });

    it('has nothing to load when the media is gone', () => {
        expect(playbackMediaChoice({task_id: 't1'})).toBeNull();
        expect(playbackMediaChoice(null)).toBeNull();
    });
});

describe('the screenshot warning before a text-only rewrite', () => {
    it('warns for a note Claude wrote from the frames', () => {
        expect(noteCameFromClaude({summary_written_from: 'debreath_media_note'})).toBe(true);
        expect(noteCameFromClaude({visual_note: {status: 'completed'}})).toBe(true);
    });

    it('does not warn once an older note has been put back over it', () => {
        expect(noteCameFromClaude({summary_written_from: null, visual_note: {status: 'completed'}})).toBe(false);
    });

    it('does not warn when the Claude note was kept beside the task note', () => {
        expect(noteCameFromClaude({visual_note: {status: 'completed', promoted: false}})).toBe(false);
    });
});

describe('telling a new Claude rewrite from the previous one', () => {
    const requestedAtMs = Date.parse('2026-10-07T10:00:00.400Z');

    it('does not take the previous run\'s result for the new one', () => {
        expect(claudeRewriteSettled({visual_note: {status: 'completed', started_at: '2026-10-07T09:00:00+00:00'}}, {requestedAtMs})).toBe(false);
    });

    it('takes a run that started after the request, even within the same second', () => {
        expect(claudeRewriteSettled({visual_note: {status: 'completed', started_at: '2026-10-07T18:00:00+08:00'}}, {requestedAtMs})).toBe(true);
    });

    it('takes any finish once the new run was seen running', () => {
        expect(claudeRewriteSettled({visual_note: {status: 'failed'}}, {requestedAtMs, sawRunning: true})).toBe(true);
    });
});

describe('applying a finished note', () => {
    it('takes the note and leaves the transcript the page holds alone', () => {
        const merged = mergeNoteFields(
            {task_id: 't1', transcript_text: 'edited here', summary_markdown: 'old'},
            {task_id: 't1', transcript_text: 'older copy', summary_markdown: 'new', summary_status: 'completed', visual_note: {status: 'completed'}},
        );
        expect(merged.transcript_text).toBe('edited here');
        expect(merged.summary_markdown).toBe('new');
        expect(merged.visual_note.status).toBe('completed');
    });
});
