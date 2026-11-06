import {createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {FEATURE_HINTS, anyHintSeen, markHintSeen, pickHint, readSeenHints, resetFeatureHints} from '../lib/featureHints.js';
import {linkButton} from '../lib/ui.js';

// The page's set of first-use hints (lib/featureHints.js): which controls are
// on screen, which hint is showing, and whether one was already dismissed on
// this visit. Mounting the page starts a new visit.
const HintScope = createContext(null);

export const HINT_SETTLE_MS = 1200;
export const HINT_MAX_WIDTH = 272;
const HINT_GAP = 8;

// Where the bubble goes, in pixels from the control's left edge: as wide as
// its text up to HINT_MAX_WIDTH and never wider than the pane, its left edge
// under the control's left (or its right edge under the control's right), then
// moved just enough to stay inside the pane. The arrow points at the control.
export const placeHint = ({anchor, pane, natural, align = 'left'}) => {
    const maxWidth = Math.max(0, Math.min(HINT_MAX_WIDTH, pane.width - 2 * HINT_GAP));
    const width = Math.min(natural || maxWidth, maxWidth);
    const wanted = align === 'right' ? anchor.right - width : anchor.left;
    const lowest = pane.left + HINT_GAP;
    const highest = pane.right - HINT_GAP - width;
    const leftEdge = Math.max(lowest, Math.min(wanted, highest));
    const arrowAt = anchor.left + Math.min(anchor.width / 2, 20) - leftEdge - 5;
    return {
        left: Math.round(leftEdge - anchor.left),
        maxWidth: Math.round(maxWidth),
        arrow: Math.round(Math.max(10, Math.min(arrowAt, width - 20))),
    };
};

export const FeatureHintScope = ({children}) => {
    const [present, setPresent] = useState(() => new Set());
    const [seen, setSeen] = useState(readSeenHints);
    const [dismissedThisVisit, setDismissed] = useState(false);
    // The first hint is picked once the page has stopped adding controls for
    // a moment: the note's follow switch appears only after its sections are
    // matched to the recording, and picking at once would always choose the
    // transcript's hint ahead of it.
    const [settled, setSettled] = useState(false);
    const currentRef = useRef(null);
    useEffect(() => {
        if (currentRef.current) return undefined;
        setSettled(false);
        const timer = setTimeout(() => setSettled(true), HINT_SETTLE_MS);
        return () => clearTimeout(timer);
    }, [present]);
    const current = settled || currentRef.current
        ? pickHint({present, seen, current: currentRef.current, dismissedThisVisit})
        : null;
    currentRef.current = current;

    const setShown = useCallback((id, shown) => {
        setPresent((prev) => {
            if (prev.has(id) === shown) return prev;
            const next = new Set(prev);
            if (shown) next.add(id); else next.delete(id);
            return next;
        });
    }, []);
    const dismiss = useCallback((id) => {
        markHintSeen(id);
        setSeen(readSeenHints());
        setDismissed(true);
    }, []);
    const value = useMemo(() => ({current, setShown, dismiss}), [current, setShown, dismiss]);
    return <HintScope.Provider value={value}>{children}</HintScope.Provider>;
};

// Wraps the control the hint is about. `when` is whether that control is on
// the page right now; `align` puts the bubble's edge under the control's left
// or right edge, so a control near the pane's right side keeps it in view.
export const FeatureHint = ({id, when = true, align = 'left', lang = 'zh', children}) => {
    const scope = useContext(HintScope);
    const setShown = scope?.setShown;
    useEffect(() => {
        if (!setShown) return undefined;
        setShown(id, !!when);
        return () => setShown(id, false);
    }, [setShown, id, when]);
    const open = !!scope && !!when && scope.current === id;
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => { if (event.key === 'Escape') scope.dismiss(id); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, scope, id]);
    // The bubble stays inside its pane: it opens from the control's side
    // (`align`), slides over when that would cross the pane's edge (a control
    // wraps to the other side in a narrow window), and narrows to the pane
    // when the pane itself is dragged narrower than the bubble. Re-placed when
    // the window or the pane changes size.
    const anchorRef = useRef(null);
    const bubbleRef = useRef(null);
    const [place, setPlace] = useState(null);
    // The bubble's own width, taken once at its first, unplaced render (its
    // text up to HINT_MAX_WIDTH), so a later narrow pane does not shrink it
    // for good.
    const naturalRef = useRef(0);
    useLayoutEffect(() => {
        if (!open) return undefined;
        const measure = () => {
            const node = anchorRef.current;
            const bubble = bubbleRef.current;
            if (!node || !bubble) return;
            const pane = node.closest('section') || document.body;
            setPlace(placeHint({
                anchor: node.getBoundingClientRect(),
                pane: pane.getBoundingClientRect(),
                natural: naturalRef.current || (naturalRef.current = bubble.getBoundingClientRect().width),
                align,
            }));
        };
        measure();
        window.addEventListener('resize', measure);
        const pane = anchorRef.current?.closest('section');
        const observer = pane && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
        observer?.observe(pane);
        return () => {
            window.removeEventListener('resize', measure);
            observer?.disconnect();
        };
    }, [open, align]);
    const hint = FEATURE_HINTS.find((item) => item.id === id);
    return (
        <span ref={anchorRef} className="relative inline-flex">
            {children}
            {open && hint && (
                <span
                    ref={bubbleRef}
                    role="status"
                    data-testid={`feature-hint-${id}`}
                    style={place ? {left: place.left, maxWidth: place.maxWidth} : {left: 0, maxWidth: HINT_MAX_WIDTH}}
                    className="absolute top-full z-30 mt-2.5 block w-max rounded-[12px] bg-[#111111] px-3.5 py-3 text-left text-[12px] font-medium leading-relaxed text-white shadow-[0_16px_40px_-16px_rgba(17,17,17,0.55)] dark:bg-[#f2f2f3] dark:text-[#111111]"
                >
                    <span aria-hidden="true" style={{left: place ? place.arrow : 16}} className="absolute -top-1 h-2.5 w-2.5 rotate-45 bg-[#111111] dark:bg-[#f2f2f3]"/>
                    <span className="block whitespace-normal">{lang === 'zh' ? hint.zh : hint.en}</span>
                    <span className="mt-2 flex justify-end">
                        <button type="button" onClick={() => scope.dismiss(id)} className={linkButton('inherit', 'sm')}>
                            {lang === 'zh' ? '知道了' : 'Got it'}
                        </button>
                    </span>
                </span>
            )}
        </span>
    );
};

// The editor's first-use hints (lib/featureHints.js) disappear once seen; this
// brings them back. Shown only after at least one was seen.
export const FeatureHintsLine = ({lang}) => {
    const zh = lang === 'zh';
    const [seen, setSeen] = useState(anyHintSeen);
    const [reset, setReset] = useState(false);
    if (!seen && !reset) return null;
    return (
        <p className="px-1 text-xs text-on-surface-variant" data-testid="feature-hints-line">
            {reset
                ? (zh ? '已重新打开，下次进编辑页会再出现。' : 'Turned back on. They show again next time you open the editor.')
                : (
                    <>
                        {zh ? '编辑页的功能提示看过就不再出现。' : 'Editor hints stop showing once seen. '}
                        <button type="button" className={linkButton('blue')} onClick={() => { resetFeatureHints(); setSeen(false); setReset(true); }}>
                            {zh ? '重新显示' : 'Show them again'}
                        </button>
                    </>
                )}
        </p>
    );
};

export default FeatureHint;
