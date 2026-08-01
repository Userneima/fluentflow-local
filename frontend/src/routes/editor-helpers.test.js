// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { activeTranscriptSegmentIndex, shouldKeepVideoReviewMounted } from './editor-helpers.js';

describe('shouldKeepVideoReviewMounted', () => {
    it('keeps the player mounted while playback is between subtitle segments', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'video', activeSegmentIndex: -1})).toBe(true);
    });

    it('does not render the video-review layout after switching back to text review', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'text', activeSegmentIndex: 3})).toBe(false);
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
