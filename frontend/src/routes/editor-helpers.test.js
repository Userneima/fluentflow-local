// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { claudeRewriteSettled, cutFlowSummary, isAutoCutFlow, cutItemsToJudge, findSegmentAt, groupTranscriptParagraphs, sizeTextareas, mergeNoteFields, noteCameFromClaude, playbackMediaChoice, shouldKeepVideoReviewMounted } from './editor-helpers.js';

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

describe('regenerating while a rewrite is already running', () => {
    it('waits instead of starting a second rewrite with either writer', async () => {
        const {regenerateDialogCopy} = await import('./editor-helpers.js');
        for (const writerKind of ['claude', 'text_model']) {
            const copy = regenerateDialogCopy({
                writerKind, writerLabel: 'X', frameNote: {available: false, reason: '这个任务的笔记正在重写中', running: true},
                textWriterLabel: 'DeepSeek', textWriterReady: true,
            }, 'zh');
            expect(copy.confirmDisabled).toBe(true);
            expect(copy.alternative).toBe(null);
            expect(copy.desc).toContain('正在写');
        }
    });
});

describe('a Claude note written from the original recording still counts as Claude\'s', () => {
    it('reads the service answer first, then either stamp', async () => {
        const {noteCameFromClaude} = await import('./editor-helpers.js');
        expect(noteCameFromClaude({note_from_frames: true, summary_written_from: 'source_media_note'})).toBe(true);
        expect(noteCameFromClaude({summary_written_from: 'source_media_note'})).toBe(true);
        expect(noteCameFromClaude({summary_written_from: 'debreath_media_note'})).toBe(true);
        expect(noteCameFromClaude({summary_written_from: 'text_regeneration'})).toBe(false);
        expect(noteCameFromClaude({note_from_frames: false, summary_written_from: 'debreath_media_note'})).toBe(false);
    });
});

// The editor says how many cut places wait for the user, the same number the
// records page shows: judgement items without an answer.
describe('cutItemsToJudge', () => {
    const survey = (verdicts) => ({debreath: {survey: {
        report: {sections: [
            {judge: true, items: [{key: 'a'}, {key: 'b'}]},
            {items: [{key: 'c'}]},
        ]},
        verdicts,
    }}});

    it('counts the judgement items still unanswered, not the rest of the report', () => {
        expect(cutItemsToJudge(survey({}))).toBe(2);
        expect(cutItemsToJudge(survey({a: 'agree'}))).toBe(1);
        expect(cutItemsToJudge(survey({a: 'agree', b: 'agree'}))).toBe(0);
    });

    it('uses the count a records-list copy carries when the report is not there', () => {
        expect(cutItemsToJudge({debreath: {survey: {has_report: true, to_judge: 3}}})).toBe(3);
    });

    it('is zero for a task without a cut report', () => {
        expect(cutItemsToJudge({})).toBe(0);
        expect(cutItemsToJudge(null)).toBe(0);
    });
});

// The highlighted sentence while playing: the one being said at that moment,
// none between sentences.
describe('findSegmentAt', () => {
    const segs = [
        {start: 0, end: 2, text: 'a'},
        {start: 2, end: 5, text: 'b'},
        {start: 8, end: 9, text: 'c'},
        {start: 9, text: 'd'},
        {start: 20, text: 'e'},
    ];

    it('finds the sentence being said', () => {
        expect(findSegmentAt(segs, 0)).toBe(0);
        expect(findSegmentAt(segs, 1.9)).toBe(0);
        expect(findSegmentAt(segs, 2)).toBe(1);
        expect(findSegmentAt(segs, 8.5)).toBe(2);
    });

    it('finds none in a gap between sentences or before the first', () => {
        expect(findSegmentAt(segs, 6)).toBe(-1);
        expect(findSegmentAt([{start: 3, end: 4}], 1)).toBe(-1);
        expect(findSegmentAt([], 1)).toBe(-1);
    });

    it('lets a sentence without an end last until the next starts, or six seconds for the last', () => {
        expect(findSegmentAt(segs, 15)).toBe(3);
        expect(findSegmentAt(segs, 25)).toBe(4);
        expect(findSegmentAt(segs, 26.5)).toBe(-1);
    });

    it('agrees with a plain scan on a long list', () => {
        const long = Array.from({length: 3000}, (_, i) => ({start: i * 2, end: i * 2 + (i % 3 === 0 ? 1.5 : 2)}));
        const scan = (t) => long.findIndex((s) => t >= s.start && t < s.end);
        for (const t of [0, 1.7, 2.1, 777.6, 3001.5, 5999, 6000.2]) expect(findSegmentAt(long, t)).toBe(scan(t));
    });
});

describe('sizeTextareas', () => {
    it('gives every box the height of its own text', () => {
        const boxes = [10, 40, 25].map((h) => {
            const node = document.createElement('textarea');
            Object.defineProperty(node, 'scrollHeight', {get: () => h});
            return node;
        });
        sizeTextareas(boxes);
        expect(boxes.map((b) => b.style.height)).toEqual(['10px', '40px', '25px']);
    });
});

// Whether the page is about the cut file: the header says 「剪后文件读不到了」
// only for such a task (decision 6.20, 6.33). A task whose automatic cut was
// declined is about the original and must never be told its cut file is gone.
const autoResult = (extra = {}) => ({
    task_id: 't1',
    transcript_media: 'debreath_media',
    debreath: {
        status: 'completed',
        ran_before_transcription: true,
        used_for_transcription: true,
        rendered: true,
        render_verified: true,
        plan: {cut_count: 4, removed_seconds: 6.4, removed_percent: 46, source_duration_seconds: 13.9, kept_seconds: 7.5},
    },
    artifacts: {debreath_media: {filename: 'debreath/lecture_debreath.mp4'}},
    ...extra,
});

describe('isAutoCutFlow', () => {
    it('recognises a task that cut itself before transcribing', () => {
        expect(isAutoCutFlow(autoResult())).toBe(true);
    });

    it('does not claim the flow for a task cut after it was transcribed', () => {
        expect(isAutoCutFlow({
            task_id: 't1',
            transcript_media: 'source',
            debreath: {status: 'completed', rendered: true, plan: {cut_count: 4}},
        })).toBe(false);
    });

    it('does not claim it when the automatic cut declined and the original was used', () => {
        // The manual entries must stay visible here: the page is about the recording.
        expect(isAutoCutFlow(autoResult({
            transcript_media: 'source',
            debreath: {ran_before_transcription: true, used_for_transcription: false},
        }))).toBe(false);
    });

    it('leaves old results alone', () => {
        expect(isAutoCutFlow({task_id: 't1'})).toBe(false);
        expect(isAutoCutFlow(null)).toBe(false);
    });
});

describe('cutFlowSummary', () => {
    it('carries the numbers of the cut and the cut file', () => {
        expect(cutFlowSummary(autoResult())).toEqual({
            cutCount: 4,
            removedSeconds: 6.4,
            removedPercent: 46,
            sourceSeconds: 13.9,
            keptSeconds: 7.5,
            mediaArtifact: {filename: 'debreath/lecture_debreath.mp4'},
            renderVerified: true,
        });
    });

    it('is null for anything that did not go through the automatic flow', () => {
        expect(cutFlowSummary({task_id: 't1', transcript_media: 'source'})).toBeNull();
    });

    it('still summarises when the cut file record is gone, so the header can say so', () => {
        const summary = cutFlowSummary(autoResult({artifacts: {}}));
        expect(summary.mediaArtifact).toBeNull();
        expect(summary.cutCount).toBe(4);
    });
});


// The transcript reads as paragraphs, not a column of fragments (界面优化清单
// 线二第 1、2 条): a paragraph per speaker turn, broken further only when it
// gets long; the speaker named where a new one takes over, as in the note.
describe('groupTranscriptParagraphs', () => {
    const piece = (start, text, speaker) => ({start, end: start + 2, text, speaker});

    it('keeps one speaker\'s consecutive pieces in one paragraph and starts a new one when the speaker changes', () => {
        const paragraphs = groupTranscriptParagraphs([
            piece(0, '是这么样', 'S0'), piece(2, '就是想想', 'S0'), piece(4, '然后一些', 'S0'),
            piece(6, '要不咱们', 'S1'), piece(8, '花个五分钟', 'S1'),
            piece(10, '大家可以', 'S0'),
        ]);
        expect(paragraphs.map((p) => p.indices)).toEqual([[0, 1, 2], [3, 4], [5]]);
        expect(paragraphs.map((p) => p.label)).toEqual(['说话人 A', '说话人 B', '说话人 A']);
        expect(paragraphs.map((p) => p.start)).toEqual([0, 6, 10]);
    });

    it('does not name anyone when the material has a single speaker or none', () => {
        expect(groupTranscriptParagraphs([piece(0, 'a', 'S0'), piece(2, 'b', 'S0')]).map((p) => p.label)).toEqual(['']);
        expect(groupTranscriptParagraphs([piece(0, 'a'), piece(2, 'b')]).map((p) => p.label)).toEqual(['']);
    });

    it('names the speaker once per turn, not again on a paragraph that only continues it', () => {
        const long = '这是一段比较长的话没有标点'.repeat(3);
        const pieces = [];
        for (let i = 0; i < 6; i += 1) pieces.push(piece(i * 2, long, 'S0'));
        pieces.push(piece(12, '好的', 'S1'));
        const paragraphs = groupTranscriptParagraphs(pieces);
        expect(paragraphs.length).toBeGreaterThan(2);
        expect(paragraphs[0].label).toBe('说话人 A');
        expect(paragraphs.slice(1, -1).every((p) => p.label === '')).toBe(true);
        expect(paragraphs.at(-1).label).toBe('说话人 B');
    });

    it('breaks a long run at a sentence end rather than mid-sentence', () => {
        const sentence = '这是一句完整的话，后面还有一些内容要讲完。';
        const pieces = [];
        for (let i = 0; i < 12; i += 1) pieces.push(piece(i * 2, i % 2 ? sentence : '然后我们继续说，', null));
        const paragraphs = groupTranscriptParagraphs(pieces);
        expect(paragraphs.length).toBeGreaterThan(1);
        for (const p of paragraphs.slice(0, -1)) expect(pieces[p.indices.at(-1)].text).toMatch(/。$/);
    });

    it('starts a new paragraph after a long pause', () => {
        const paragraphs = groupTranscriptParagraphs([piece(0, 'a'), piece(2, 'b'), {start: 10, end: 12, text: 'c'}]);
        expect(paragraphs.map((p) => p.indices)).toEqual([[0, 1], [2]]);
    });

    it('keeps every piece exactly once and in order, so each stays editable and saved on its own', () => {
        const pieces = Array.from({length: 50}, (_, i) => piece(i, `第${i}段内容`, i % 7 < 3 ? 'S0' : 'S1'));
        const indices = groupTranscriptParagraphs(pieces).flatMap((p) => p.indices);
        expect(indices).toEqual(pieces.map((_, i) => i));
    });
});
