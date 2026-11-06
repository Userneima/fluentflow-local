import {createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {FEATURE_HINTS, anyHintSeen, markHintSeen, pickHint, readSeenHints, resetFeatureHints} from '../lib/featureHints.js';
import {linkButton} from '../lib/ui.js';

// The page's set of first-use hints (lib/featureHints.js): which controls are
// on screen, which hint is showing, and whether one was already dismissed on
// this visit. Mounting the page starts a new visit.
const HintScope = createContext(null);

export const HINT_SETTLE_MS = 1200;

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
    // The bubble opens towards the side with room inside the pane: in a
    // narrow window a control at the right of its row wraps to the left.
    const anchorRef = useRef(null);
    const bubbleRef = useRef(null);
    const [side, setSide] = useState(align);
    useLayoutEffect(() => {
        if (!open) return;
        const anchor = anchorRef.current?.getBoundingClientRect();
        const width = bubbleRef.current?.getBoundingClientRect().width || 0;
        const box = (anchorRef.current?.closest('section') || document.body).getBoundingClientRect();
        if (!anchor || !width || !box.width) return;
        const fitsLeft = anchor.left + width <= box.right - 8;
        const fitsRight = anchor.right - width >= box.left + 8;
        setSide(align === 'right' ? (fitsRight || !fitsLeft ? 'right' : 'left') : (fitsLeft || !fitsRight ? 'left' : 'right'));
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
                    className={`absolute top-full z-30 mt-2.5 block w-max max-w-[min(17rem,calc(100vw-2.5rem))] rounded-[12px] bg-[#111111] px-3.5 py-3 text-left text-[12px] font-medium leading-relaxed text-white shadow-[0_16px_40px_-16px_rgba(17,17,17,0.55)] dark:bg-[#f2f2f3] dark:text-[#111111] ${side === 'right' ? 'right-0' : 'left-0'}`}
                >
                    <span aria-hidden="true" className={`absolute -top-1 h-2.5 w-2.5 rotate-45 bg-[#111111] dark:bg-[#f2f2f3] ${side === 'right' ? 'right-4' : 'left-4'}`}/>
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
