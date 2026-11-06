// @vitest-environment jsdom

// Requirements for the editor's first-use hints (decision 6.59), from the
// problem they solve (controls he uses but would not find on his own):
//   1. A hint shows beside its control once the page has settled; when several
//      controls are there, the first in FEATURE_HINTS order, and only one.
//   2. A control that appears a moment later (the note's follow switch, after
//      its sections are matched) still gets its turn ahead of later hints.
//   3. 知道了 hides it for good; no other hint follows on the same visit.
//   4. The next visit shows the next unseen hint; a seen one never returns.
//   5. A hint whose control is not on the page does not show.
//   6. Settings shows "重新显示" only after one was seen; it brings them back.
//   7. Storage that throws (private mode) does not break the page.

import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {FeatureHint, FeatureHintScope, FeatureHintsLine, HINT_MAX_WIDTH, HINT_SETTLE_MS, placeHint} from './FeatureHint.jsx';
import {FEATURE_HINTS, FEATURE_HINTS_SEEN, readSeenHints} from '../lib/featureHints.js';

const ORDER = FEATURE_HINTS.map((hint) => hint.id);

const Page = ({ids = ORDER, laterIds = [], showLater = false}) => (
    <FeatureHintScope>
        {ids.map((id) => <FeatureHint key={id} id={id}><button type="button">{id}</button></FeatureHint>)}
        {laterIds.map((id) => <FeatureHint key={id} id={id} when={showLater}><button type="button">{id}</button></FeatureHint>)}
    </FeatureHintScope>
);

const settle = () => act(() => { vi.advanceTimersByTime(HINT_SETTLE_MS + 50); });
const shownHints = () => screen.queryAllByTestId(/^feature-hint-/).map((node) => node.dataset.testid.replace('feature-hint-', ''));

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
    cleanup();
    vi.useRealTimers();
    localStorage.clear();
});

describe('which hint shows', () => {
    it('shows nothing until the page settles, then the first in order, alone', () => {
        render(<Page ids={['review-note', 'transcript-edit', 'note-versions']}/>);
        expect(shownHints()).toEqual([]);
        settle();
        expect(shownHints()).toEqual(['transcript-edit']);
        expect(screen.getByTestId('feature-hint-transcript-edit').textContent).toContain('转录稿可以直接点进去改字');
    });

    it('lets a control that appears a moment later take its turn first', () => {
        const {rerender} = render(<Page ids={['transcript-edit']} laterIds={['note-follow']}/>);
        act(() => { vi.advanceTimersByTime(HINT_SETTLE_MS / 2); });
        rerender(<Page ids={['transcript-edit']} laterIds={['note-follow']} showLater/>);
        settle();
        expect(shownHints()).toEqual(['note-follow']);
    });

    it('keeps the hint on screen when a more important control turns up later', () => {
        const {rerender} = render(<Page ids={['transcript-edit']} laterIds={['note-follow']}/>);
        settle();
        rerender(<Page ids={['transcript-edit']} laterIds={['note-follow']} showLater/>);
        settle();
        expect(shownHints()).toEqual(['transcript-edit']);
    });

    it('does not show a hint whose control is not on the page', () => {
        render(<Page ids={[]} laterIds={['note-follow']}/>);
        settle();
        expect(shownHints()).toEqual([]);
    });
});

describe('dismissing', () => {
    it('hides it for good and shows no other on the same visit', () => {
        render(<Page/>);
        settle();
        fireEvent.click(screen.getByRole('button', {name: '知道了'}));
        settle();
        expect(shownHints()).toEqual([]);
        expect([...readSeenHints()]).toEqual(['note-follow']);
    });

    it('Escape dismisses it too', () => {
        render(<Page/>);
        settle();
        fireEvent.keyDown(window, {key: 'Escape'});
        expect(shownHints()).toEqual([]);
        expect(readSeenHints().has('note-follow')).toBe(true);
    });

    it('shows the next unseen one on the next visit, never a seen one', () => {
        const first = render(<Page/>);
        settle();
        fireEvent.click(screen.getByRole('button', {name: '知道了'}));
        first.unmount();
        render(<Page/>);
        settle();
        expect(shownHints()).toEqual(['transcript-follow']);
    });

    it('shows nothing once every hint was seen', () => {
        localStorage.setItem(FEATURE_HINTS_SEEN, JSON.stringify(ORDER));
        render(<Page/>);
        settle();
        expect(shownHints()).toEqual([]);
    });

    it('still works when storage throws', () => {
        const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('private'); });
        render(<Page/>);
        settle();
        fireEvent.click(screen.getByRole('button', {name: '知道了'}));
        expect(shownHints()).toEqual([]);
        spy.mockRestore();
    });
});

describe('the settings line', () => {
    it('is not there before any hint was seen', () => {
        render(<FeatureHintsLine lang="zh"/>);
        expect(screen.queryByTestId('feature-hints-line')).toBeNull();
    });

    it('brings every hint back', () => {
        localStorage.setItem(FEATURE_HINTS_SEEN, JSON.stringify(['note-follow', 'review-note']));
        render(<FeatureHintsLine lang="zh"/>);
        fireEvent.click(screen.getByRole('button', {name: '重新显示'}));
        expect(screen.getByTestId('feature-hints-line').textContent).toContain('下次进编辑页会再出现');
        expect(readSeenHints().size).toBe(0);
        cleanup();
        render(<Page/>);
        settle();
        expect(shownHints()).toEqual(['note-follow']);
    });
});

// The bubble stays inside the pane it is in (the editor's panes can be dragged
// down to a quarter of the width and clip what crosses their edge).
describe('where the bubble goes', () => {
    const rect = (left, width) => ({left, right: left + width, width});
    const inside = (anchor, pane, place) => {
        const left = anchor.left + place.left;
        const width = Math.min(HINT_MAX_WIDTH, place.maxWidth);
        return left >= pane.left && left + width <= pane.right;
    };

    it('opens under the control when there is room', () => {
        const anchor = rect(120, 80);
        const place = placeHint({anchor, pane: rect(100, 600), natural: HINT_MAX_WIDTH});
        expect(place.left).toBe(0);
        expect(place.maxWidth).toBe(HINT_MAX_WIDTH);
    });

    it('lines up with the right edge of a control at the right of its row', () => {
        const anchor = rect(600, 80);
        const place = placeHint({anchor, pane: rect(100, 600), natural: HINT_MAX_WIDTH, align: 'right'});
        expect(anchor.left + place.left + HINT_MAX_WIDTH).toBe(anchor.right);
    });

    it('slides over when a right-aligned control has wrapped to the left', () => {
        const anchor = rect(110, 80);
        const pane = rect(100, 300);
        const place = placeHint({anchor, pane, natural: HINT_MAX_WIDTH, align: 'right'});
        expect(inside(anchor, pane, place)).toBe(true);
    });

    it('narrows to a pane dragged narrower than the bubble', () => {
        const anchor = rect(110, 60);
        const pane = rect(100, 200);
        const place = placeHint({anchor, pane, natural: HINT_MAX_WIDTH});
        expect(place.maxWidth).toBe(184);
        expect(inside(anchor, pane, place)).toBe(true);
    });
});
