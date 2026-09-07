import {afterEach, beforeEach, describe, expect, it} from 'vitest';
import {
    HEADER_PINNED_STORAGE_KEY,
    MAX_SPLIT_RATIO,
    MIN_SPLIT_RATIO,
    SPLIT_RATIO_STORAGE_KEY,
    clampSplitRatio,
    loadHeaderPinned,
    loadSplitRatio,
    nudgeSplitRatio,
    parseSplitRatio,
    saveHeaderPinned,
    saveSplitRatio,
    splitPaneStyles,
    splitRatioFromPointer,
} from './editorLayoutPrefs.js';

const memoryStorage = () => {
    const map = new Map();
    return {
        getItem: (key) => (map.has(key) ? map.get(key) : null),
        setItem: (key, value) => map.set(key, String(value)),
        removeItem: (key) => map.delete(key),
    };
};

describe('split ratio math', () => {
    it('clamps to a range where both panes stay usable', () => {
        expect(clampSplitRatio(0.5)).toBe(0.5);
        expect(clampSplitRatio(0.01)).toBe(MIN_SPLIT_RATIO);
        expect(clampSplitRatio(9)).toBe(MAX_SPLIT_RATIO);
    });

    it('treats unusable values as "no preference" rather than 0', () => {
        expect(clampSplitRatio('nope')).toBeNull();
        expect(parseSplitRatio(null)).toBeNull();
        expect(parseSplitRatio('')).toBeNull();
        expect(parseSplitRatio('0.42')).toBeCloseTo(0.42);
    });

    it('converts a pointer position inside the container to a ratio', () => {
        const rect = {left: 100, width: 400};
        expect(splitRatioFromPointer(300, rect)).toBeCloseTo(0.5);
        // Dragging past the edge stops at the limit instead of collapsing a pane.
        expect(splitRatioFromPointer(80, rect)).toBe(MIN_SPLIT_RATIO);
        expect(splitRatioFromPointer(900, rect)).toBe(MAX_SPLIT_RATIO);
    });

    it('ignores a container that has not been measured yet', () => {
        expect(splitRatioFromPointer(300, null)).toBeNull();
        expect(splitRatioFromPointer(300, {left: 0, width: 0})).toBeNull();
    });

    it('steps from the current ratio, or from the fallback before the first drag', () => {
        expect(nudgeSplitRatio(0.5, 1)).toBeCloseTo(0.52);
        expect(nudgeSplitRatio(0.5, -1)).toBeCloseTo(0.48);
        expect(nudgeSplitRatio(null, 1, 0.6)).toBeCloseTo(0.62);
        expect(nudgeSplitRatio(MAX_SPLIT_RATIO, 1)).toBe(MAX_SPLIT_RATIO);
    });
});

describe('pane styles', () => {
    it('leaves the responsive default layout alone until a ratio is stored', () => {
        expect(splitPaneStyles(null)).toEqual({left: undefined, right: undefined});
    });

    it('overrides the note pane fixed width so the ratio actually applies', () => {
        const {left, right} = splitPaneStyles(0.4);
        expect(left).toEqual({flex: '0 0 40.00%'});
        expect(right).toEqual({width: 'auto', flex: '1 1 0%'});
    });
});

describe('persistence', () => {
    let original;

    beforeEach(() => {
        original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
        Object.defineProperty(globalThis, 'localStorage', {value: memoryStorage(), configurable: true});
    });

    afterEach(() => {
        if (original) Object.defineProperty(globalThis, 'localStorage', original);
        else delete globalThis.localStorage;
    });

    it('round-trips the split ratio and clears it on reset', () => {
        saveSplitRatio(0.62);
        expect(globalThis.localStorage.getItem(SPLIT_RATIO_STORAGE_KEY)).toBe('0.62');
        expect(loadSplitRatio()).toBeCloseTo(0.62);

        saveSplitRatio(null);
        expect(loadSplitRatio()).toBeNull();
    });

    it('defaults the header to auto-collapse and remembers pinning', () => {
        expect(loadHeaderPinned()).toBe(false);

        saveHeaderPinned(true);
        expect(globalThis.localStorage.getItem(HEADER_PINNED_STORAGE_KEY)).toBe('1');
        expect(loadHeaderPinned()).toBe(true);

        saveHeaderPinned(false);
        expect(loadHeaderPinned()).toBe(false);
    });

    it('survives storage being unavailable', () => {
        Object.defineProperty(globalThis, 'localStorage', {
            get() { throw new Error('blocked'); },
            configurable: true,
        });

        expect(() => saveSplitRatio(0.5)).not.toThrow();
        expect(() => saveHeaderPinned(true)).not.toThrow();
        expect(loadSplitRatio()).toBeNull();
        expect(loadHeaderPinned()).toBe(false);
    });
});
