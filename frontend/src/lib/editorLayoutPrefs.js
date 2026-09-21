// Editor workspace layout preferences: the transcript/note split ratio and
// whether the page header stays pinned open during playback. Both are per-browser
// reading comfort settings, not result data, so they live in localStorage and
// never travel with the job.

export const SPLIT_RATIO_STORAGE_KEY = 'fluentflow_editor_split_ratio';
export const HEADER_PINNED_STORAGE_KEY = 'fluentflow_editor_header_pinned';

// Keeps both panes usable: neither side can be dragged down to a sliver where
// its toolbar wraps into an unreadable column.
export const MIN_SPLIT_RATIO = 0.25;
export const MAX_SPLIT_RATIO = 0.75;
export const SPLIT_RATIO_KEYBOARD_STEP = 0.02;

const storage = () => {
    try {
        return globalThis.localStorage || null;
    } catch {
        // Private mode and blocked third-party storage both throw on access.
        return null;
    }
};

/** `null` means "no usable ratio": the responsive default layout applies. */
export const clampSplitRatio = (value) => {
    // Number(null) and Number('') are both 0, which would silently clamp a
    // missing preference to the minimum and shrink the transcript pane.
    if (value === null || value === undefined || value === '') return null;
    const ratio = Number(value);
    if (!Number.isFinite(ratio)) return null;
    return Math.min(MAX_SPLIT_RATIO, Math.max(MIN_SPLIT_RATIO, ratio));
};

export const parseSplitRatio = (raw) => clampSplitRatio(raw);

export const splitRatioFromPointer = (clientX, rect) => {
    if (!rect || !(rect.width > 0)) return null;
    return clampSplitRatio((clientX - rect.left) / rect.width);
};

export const nudgeSplitRatio = (ratio, direction, fallback) => {
    const base = clampSplitRatio(ratio) ?? clampSplitRatio(fallback);
    if (base === null) return null;
    return clampSplitRatio(base + direction * SPLIT_RATIO_KEYBOARD_STEP);
};

export const loadSplitRatio = () => parseSplitRatio(storage()?.getItem(SPLIT_RATIO_STORAGE_KEY));

export const saveSplitRatio = (ratio) => {
    const store = storage();
    if (!store) return;
    try {
        const clamped = clampSplitRatio(ratio);
        if (clamped === null) store.removeItem(SPLIT_RATIO_STORAGE_KEY);
        else store.setItem(SPLIT_RATIO_STORAGE_KEY, String(clamped));
    } catch {
        // A full or blocked quota must not break the drag interaction.
    }
};

export const loadHeaderPinned = () => storage()?.getItem(HEADER_PINNED_STORAGE_KEY) === '1';

export const saveHeaderPinned = (pinned) => {
    const store = storage();
    if (!store) return;
    try {
        store.setItem(HEADER_PINNED_STORAGE_KEY, pinned ? '1' : '0');
    } catch {
        // Same reasoning as saveSplitRatio.
    }
};

/**
 * Inline styles that turn the stored ratio into pane widths. Returns empty
 * styles while no preference exists so the responsive Tailwind classes stay in
 * charge of the default layout.
 */
export const splitPaneStyles = (ratio) => {
    const clamped = clampSplitRatio(ratio);
    if (clamped === null) return {left: undefined, right: undefined};
    return {
        left: {flex: `0 0 ${(clamped * 100).toFixed(2)}%`},
        // `width: auto` is required: the note pane pins itself to a fixed width
        // at xl and above, and a class width would win over flex-basis alone.
        right: {width: 'auto', flex: '1 1 0%'},
    };
};
