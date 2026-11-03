// The page's side of the pointers to moved content (backend/core/note_pointers.py).
// Written from the requirements:
//   - A pointer the service adds, or the user deletes, does not change which
//     time a section has: the section's body hash leaves it out, as the
//     service's does.
//   - `[[#第三章#（一）…]]` lands on the sub-section inside that chapter,
//     even when another chapter has a sub-section with the same heading.

import {describe, expect, it} from 'vitest';
import {Text} from '@codemirror/state';
import {noteSections} from './noteFollow.js';
import {findHeadingLine} from './noteLivePreview.js';

describe('pointers to moved content', () => {
    it('leaves the section hash as it was', () => {
        const plain = ['## 二、话题', '', '话题的正文。', '', '## 三、下一章', '', '下一章。'].join('\n');
        const pointed = plain.replace('话题的正文。', '话题的正文。\n\n> [!tip] 视频 3:20 起讲的「挪走的」在[[#五、挪走的|第五章]]。');

        expect(noteSections(pointed).map((s) => s.bodyHash)).toEqual(noteSections(plain).map((s) => s.bodyHash));
        // A hand-written callout is the user's text and counts.
        const own = plain.replace('话题的正文。', '话题的正文。\n\n> [!tip] 这里视频讲的在第五章。');
        expect(noteSections(own)[0].bodyHash).not.toEqual(noteSections(plain)[0].bodyHash);
    });

    it('follows a heading path into the right chapter', () => {
        const doc = Text.of(['## 三、一章', '### （一）主线', '一。', '## 四、另一章', '### （一）主线', '二。']);

        expect(findHeadingLine(doc, '#四、另一章#（一）主线').number).toBe(5);
        expect(findHeadingLine(doc, '#（一）主线').number).toBe(2);
        // A chapter renamed since: the sub-section is still found.
        expect(findHeadingLine(doc, '#四、改过名#（一）主线').number).toBe(2);
    });
});
