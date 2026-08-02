// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { activeTranscriptSegmentIndex, mediaSourcePlan, shouldKeepVideoReviewMounted } from './editor-helpers.js';

describe('shouldKeepVideoReviewMounted', () => {
    it('keeps the player mounted while playback is between subtitle segments', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'video', activeSegmentIndex: -1})).toBe(true);
    });

    it('does not render the video-review layout after switching back to text review', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'text', activeSegmentIndex: 3})).toBe(false);
    });
});

describe('mediaSourcePlan', () => {
    const storedVideo = {
        task_id: 'task-1',
        filename: 'kickoff.mp4',
        source_file_available: true,
        artifacts: {playback_audio: {filename: 'kickoff_audio.mp3'}},
    };

    it('streams a retained video after a restart instead of asking for the file again', () => {
        // No in-memory File survives a restart, so the reselect prompt must not
        // be the first thing a record with a retained source falls back to.
        const plan = mediaSourcePlan(storedVideo);
        expect(plan[0]).toEqual({kind: 'stream', mediaKind: 'video'});
        expect(plan.map((step) => step.kind)).toEqual(['stream', 'artifact', 'download']);
    });

    it('prefers the file the user just picked over any server copy', () => {
        const file = {name: 'kickoff.mp4', size: 10};
        expect(mediaSourcePlan(storedVideo, {localFile: file})).toEqual([{kind: 'local-file', file}]);
    });

    it('falls back to the saved playback audio when the source has expired', () => {
        const expired = {...storedVideo, source_file_available: false};
        expect(mediaSourcePlan(expired).map((step) => step.kind)).toEqual(['artifact']);
    });

    it('marks a retained audio-only source as audio playback', () => {
        const storedAudio = {task_id: 'task-2', filename: 'talk.m4a', source_file_available: true};
        expect(mediaSourcePlan(storedAudio)).toEqual([
            {kind: 'stream', mediaKind: 'audio'},
            {kind: 'download', filename: 'talk.m4a'},
        ]);
    });

    it('skips the full-source download for results that cannot be persisted', () => {
        expect(mediaSourcePlan(storedVideo, {canPersistResult: false}).map((step) => step.kind))
            .toEqual(['stream', 'artifact']);
    });

    it('has nothing to attach without a task or a result', () => {
        expect(mediaSourcePlan(null)).toEqual([]);
        expect(mediaSourcePlan({filename: 'orphan.mp3'})).toEqual([]);
    });
});

describe('activeTranscriptSegmentIndex', () => {
    const segments = [
        {start: 0, end: 3},
        {start: 3, end: 6},
        {start: 6, end: 9},
    ];

    it('finds the active segment without scanning the full transcript', () => {
        expect(activeTranscriptSegmentIndex(segments, 6.5)).toBe(2);
        expect(activeTranscriptSegmentIndex(segments, 9)).toBe(-1);
    });
});
