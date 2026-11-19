import {useCallback, useEffect, useRef, useState} from 'react';

// A folded sidebar opens over the page while the pointer rests on it and folds
// again shortly after the pointer leaves (2026-11-19). Both edges wait a little:
// a pointer sweeping past the left edge does not flash it open, and a pointer
// that slips out for a moment does not snap it shut.
//
//   enabled — peeking is allowed at all (folded by choice, wide window)
//   hold    — keep it open even with the pointer outside (a menu or dialog
//             that belongs to the sidebar is open); once hold ends with the
//             pointer outside, the usual close delay starts
//
// Returns {peek, onMouseEnter, onMouseLeave, closeNow}.
export const PEEK_OPEN_DELAY = 150;
export const PEEK_CLOSE_DELAY = 400;

export const useHoverPeek = (enabled, hold = false) => {
    const [peek, setPeek] = useState(false);
    const inside = useRef(false);
    const timer = useRef(null);

    const clear = useCallback(() => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = null;
    }, []);
    const later = useCallback((fn, ms) => {
        clear();
        timer.current = setTimeout(() => { timer.current = null; fn(); }, ms);
    }, [clear]);

    const onMouseEnter = useCallback(() => {
        inside.current = true;
        if (!enabled) return;
        if (peek) clear();
        else later(() => setPeek(true), PEEK_OPEN_DELAY);
    }, [enabled, peek, clear, later]);

    const onMouseLeave = useCallback(() => {
        inside.current = false;
        if (!peek) { clear(); return; }
        if (!hold) later(() => setPeek(false), PEEK_CLOSE_DELAY);
    }, [peek, hold, clear, later]);

    const closeNow = useCallback(() => { clear(); setPeek(false); }, [clear]);

    // Pinned open, or the window went narrow: no peeking left to undo.
    useEffect(() => {
        if (!enabled) { clear(); setPeek(false); }
    }, [enabled, clear]);

    // A menu opened during the close wait keeps it open; a menu closed while
    // the pointer was already elsewhere starts the wait.
    useEffect(() => {
        if (!peek) return;
        if (hold) clear();
        else if (!inside.current) later(() => setPeek(false), PEEK_CLOSE_DELAY);
    }, [hold, peek, clear, later]);

    useEffect(() => clear, [clear]);

    return {peek: enabled && peek, onMouseEnter, onMouseLeave, closeNow};
};
