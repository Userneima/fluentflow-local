// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { playbackMediaChoice, shouldKeepVideoReviewMounted } from './editor-helpers.js';

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
