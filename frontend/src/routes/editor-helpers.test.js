// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { shouldKeepVideoReviewMounted } from './editor-helpers.js';

describe('shouldKeepVideoReviewMounted', () => {
    it('keeps the player mounted while playback is between subtitle segments', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'video', activeSegmentIndex: -1})).toBe(true);
    });

    it('does not render the video-review layout after switching back to text review', () => {
        expect(shouldKeepVideoReviewMounted({activeReviewMode: 'text', activeSegmentIndex: 3})).toBe(false);
    });
});
