// Playback on the editor page and the transcript following it, moved out of
// editor.jsx: the player's clock, speed and remembered position, seeking,
// the sentence playing and scrolling to it, following switched off by
// scrolling or by typing in a sentence and back on when the sentence is
// left, keeping the reader's place across text and video review, and the
// note following playback (useNoteFollow). Which file plays is
// editor-media.js. The effects run in the order they had in editor.jsx.
import {useCallback, useEffect, useRef, useState} from 'react';
import {API_BASE, apiFetch} from '../app/shared.jsx';
import {createPlaybackClock} from '../lib/playbackClock.js';
import {useNoteFollow} from '../lib/useNoteFollow.js';
import {findSegmentAt, shouldKeepVideoReviewMounted, sizeTextareas} from './editor-helpers.js';
import {SIZES_ITSELF} from './editor-transcript-rows.jsx';

export const usePlaybackFollow = ({
    result,
    activeTaskId,
    durSec,
    mediaUrl,
    setMediaError,
    mediaObjectUrlRef,
    canUseVideoReview,
    segments,
    visibleTranscriptSegments,
    visibleTranscriptView,
    handleSegmentTextChange,
    noteReady,
    summaryMarkdownForEditor,
    summaryResultKey,
    playingCutVersion,
}) => {
    const playbackSaveRef = useRef(0);
    const playbackClockRef = useRef(null);
    if (!playbackClockRef.current) playbackClockRef.current = createPlaybackClock();
    const playbackClock = playbackClockRef.current;
    const [mediaDuration, setMediaDuration] = useState(0);
    const [mediaPlaying, setMediaPlaying] = useState(false);
    const [followPlayback, setFollowPlayback] = useState(true);
    const followPlaybackRef = useRef(followPlayback);
    followPlaybackRef.current = followPlayback;
    // Set while following is off only because the caret went into a sentence:
    // leaving the sentence then follows again (evaluation 2026-11-19).
    const editPausedRef = useRef(false);
    const [playbackRate, setPlaybackRate] = useState(1);
    const [transcriptReviewMode, setTranscriptReviewMode] = useState('text');
    const mediaRef = useRef(null);
    const transcriptScrollRef = useRef(null);
    const segmentRefs = useRef({});
    const playbackMemoryKey = result ? `fluentflow_playback_position_${activeTaskId}` : '';
    const playbackDuration = mediaDuration || durSec || 0;
    const activeReviewMode = canUseVideoReview ? transcriptReviewMode : 'text';
    const shouldShowVideoReview = shouldKeepVideoReviewMounted({activeReviewMode});
    // The sentence playing now, worked out on each tick of the player but
    // stored only when it changes, so the page re-renders once per sentence.
    const [activeSegmentIndex, setActiveSegmentIndex] = useState(-1);
    useEffect(() => {
        const follow = (time) => {
            const index = findSegmentAt(visibleTranscriptSegments, time);
            setActiveSegmentIndex((previous) => (previous === index ? previous : index));
        };
        follow(playbackClock.get());
        return playbackClock.subscribe(follow);
    }, [playbackClock, visibleTranscriptSegments]);
    const updateMediaCurrentTime = useCallback((time, options={}) => {
        const next = Math.max(0, Number(time) || 0);
        playbackClock.set(next);
        if (options.persist === false || !playbackMemoryKey) return;
        const now = Date.now();
        if (now - playbackSaveRef.current < 900 && !options.force) return;
        playbackSaveRef.current = now;
        try {
            localStorage.setItem(playbackMemoryKey, JSON.stringify({
                time: next,
                duration: Number(options.duration ?? playbackDuration) || 0,
                updatedAt: now,
            }));
        } catch(_) {}
    }, [playbackClock, playbackDuration, playbackMemoryKey]);
    const restoreMediaPosition = useCallback((media, duration) => {
        const nextDuration = Number(duration) || 0;
        setMediaDuration(nextDuration || durSec || 0);
        if (!media || !playbackMemoryKey) {
            updateMediaCurrentTime(media?.currentTime || 0, {persist: false});
            return;
        }
        try {
            const saved = JSON.parse(localStorage.getItem(playbackMemoryKey) || '{}');
            const savedTime = Number(saved?.time);
            const safeDuration = nextDuration || Number(saved?.duration) || durSec || 0;
            if (Number.isFinite(savedTime) && savedTime > 1 && (!safeDuration || savedTime < safeDuration - 1)) {
                media.currentTime = savedTime;
                updateMediaCurrentTime(savedTime, {persist: false});
                return;
            }
        } catch(_) {}
        updateMediaCurrentTime(media.currentTime || 0, {persist: false});
    }, [durSec, playbackMemoryKey, updateMediaCurrentTime]);
    const seekMediaTo = useCallback((time, {follow=true}={}) => {
        const media = mediaRef.current;
        const duration = playbackDuration || media?.duration || 0;
        const next = Math.max(0, Math.min(Number(time) || 0, duration || Number(time) || 0));
        if (media) media.currentTime = next;
        updateMediaCurrentTime(next, {duration, force: true});
        if (follow) setFollowPlayback(true);
    }, [playbackDuration, updateMediaCurrentTime]);
    const noteFollow = useNoteFollow({
        enabled: true,
        taskId: result?.task_id || '',
        noteReady,
        markdown: summaryMarkdownForEditor,
        playingCut: playingCutVersion,
        hasMedia: !!mediaUrl,
        clock: playbackClock,
        apiFetch,
        apiBase: API_BASE,
        seek: seekMediaTo,
    });

    const persistMediaPosition = useCallback(() => {
        const media = mediaRef.current;
        if (!media || !playbackMemoryKey) return;
        const next = Math.max(0, Number(media.currentTime) || 0);
        try {
            localStorage.setItem(playbackMemoryKey, JSON.stringify({
                time: next,
                duration: Number(media.duration) || playbackDuration || 0,
                updatedAt: Date.now(),
            }));
        } catch(_) {}
    }, [playbackDuration, playbackMemoryKey]);

    // The transcript follows the playback like the note does (useNoteFollow):
    // scrolling it by hand stops the following, so another part can be read
    // while listening (2026-11-03: it was pulled back on every sentence);
    // turning the switch back on brings the sentence playing into view at
    // once, also while paused. Clicking a sentence's time follows again.
    const revealPendingRef = useRef(false);
    const pauseFollowing = useCallback(() => {
        editPausedRef.current = false;
        setFollowPlayback((value) => (value ? false : value));
    }, []);
    const resumeFollowing = useCallback(() => {
        editPausedRef.current = false;
        revealPendingRef.current = true;
        setFollowPlayback(true);
    }, []);
    useEffect(() => {
        const followIndex = activeSegmentIndex;
        const reveal = revealPendingRef.current;
        revealPendingRef.current = false;
        if (!followPlayback || followIndex < 0 || (!mediaPlaying && !reveal)) return;
        const node = segmentRefs.current[followIndex];
        if (node) node.scrollIntoView({block:'center', behavior:'smooth'});
    }, [activeSegmentIndex, followPlayback, mediaPlaying]);
    // The speed is the page's, not the player's menu (PlaybackSpeedMenu), and
    // is set again after a new file loads, which resets it.
    useEffect(() => {
        const media = mediaRef.current;
        if (!media) return;
        media.defaultPlaybackRate = playbackRate;
        media.playbackRate = playbackRate;
    }, [playbackRate, mediaUrl]);

    // Switching between text review and video review keeps the reader's
    // place: while playing, the sentence playing is brought into view;
    // otherwise the sentence at the top of the list stays at the top.
    const reviewAnchorRef = useRef({index: -1, block: 'start'});
    const firstVisibleLine = () => {
        const box = transcriptScrollRef.current;
        if (!box) return -1;
        const top = box.getBoundingClientRect().top;
        let lo = 0;
        let hi = visibleTranscriptSegments.length - 1;
        let found = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const node = segmentRefs.current[mid];
            if (!node) return -1;
            if (node.getBoundingClientRect().bottom > top) { found = mid; hi = mid - 1; } else { lo = mid + 1; }
        }
        return found;
    };
    const switchReviewMode = (mode) => {
        persistMediaPosition();
        reviewAnchorRef.current = mediaPlaying
            ? {index: activeSegmentIndex, block: 'center'}
            : {index: firstVisibleLine(), block: 'start'};
        setTranscriptReviewMode(mode);
    };
    const reviewModeSeenRef = useRef(activeReviewMode);
    useEffect(() => {
        if (reviewModeSeenRef.current === activeReviewMode) return;
        reviewModeSeenRef.current = activeReviewMode;
        const {index, block} = reviewAnchorRef.current;
        const node = segmentRefs.current[index];
        if (!node) return undefined;
        node.scrollIntoView({block});
        // Rows off screen are laid out at a placeholder height; once the rows
        // near the target are drawn at their real height the target moves.
        // Measure again over the next frames and scroll it back into place.
        const box = transcriptScrollRef.current;
        let frame = 0;
        let handle = 0;
        const settle = () => {
            if (!box || !node.isConnected) return;
            const outer = box.getBoundingClientRect();
            const rect = node.getBoundingClientRect();
            const offset = block === 'center'
                ? (rect.top + rect.height / 2) - (outer.top + outer.height / 2)
                : rect.top - outer.top;
            if (Math.abs(offset) > 1) box.scrollTop += offset;
            frame += 1;
            if (frame < 10) handle = requestAnimationFrame(settle);
        };
        handle = requestAnimationFrame(settle);
        return () => cancelAnimationFrame(handle);
    }, [activeReviewMode]);

    // Only for a browser that cannot size the boxes with CSS: all of them in
    // one pass when a result, a view or the number of sentences changes. Not
    // on every saved edit: the page re-measured 7104 boxes after the first
    // autosave and froze for eleven seconds.
    useEffect(() => {
        if (SIZES_ITSELF) return;
        const root = transcriptScrollRef.current;
        if (!root) return;
        sizeTextareas(root.querySelectorAll('textarea[data-transcript-segment="true"]'));
    }, [summaryResultKey, visibleTranscriptView, activeReviewMode, segments.length]);

    const seekToSegment = (seg) => {
        if (seg?.start == null) return;
        seekMediaTo(Number(seg.start) || 0);
    };

    // Stable handlers for the rows, reading the latest page state through refs.
    const rowHandlersRef = useRef({});
    rowHandlersRef.current = {seekToSegment: (seg) => seekToSegment(seg), handleSegmentTextChange: (i, text) => handleSegmentTextChange(i, text)};
    const seekRow = useCallback((seg) => rowHandlersRef.current.seekToSegment(seg), []);
    const editRow = useCallback((index, text) => rowHandlersRef.current.handleSegmentTextChange(index, text), []);
    // The caret in a sentence keeps the transcript still while it is typed in;
    // leaving the sentence follows again if it was following (leaveSentence).
    const pauseForEdit = useCallback(() => {
        if (followPlaybackRef.current) editPausedRef.current = true;
        setFollowPlayback(false);
    }, []);
    const stopFollowing = pauseForEdit;
    // Putting the caret in a sentence of a paragraph while nothing plays moves
    // the player there, so it can be heard right after.
    rowHandlersRef.current.focusSentence = (index) => {
        pauseForEdit();
        const seg = segments[index];
        if (!mediaPlaying && seg?.start != null) seekMediaTo(Number(seg.start) || 0, {follow: false});
    };
    const focusSentence = useCallback((index) => rowHandlersRef.current.focusSentence(index), []);
    const registerRow = useCallback((index, node) => { if (node) segmentRefs.current[index] = node; }, []);

    useEffect(() => {
        const persistBeforeBackground = () => {
            if (document.visibilityState === 'hidden') persistMediaPosition();
        };
        window.addEventListener('pagehide', persistMediaPosition);
        document.addEventListener('visibilitychange', persistBeforeBackground);
        return () => {
            persistMediaPosition();
            window.removeEventListener('pagehide', persistMediaPosition);
            document.removeEventListener('visibilitychange', persistBeforeBackground);
        };
    }, [persistMediaPosition]);

    useEffect(() => () => {
        const activeUrl = mediaObjectUrlRef.current;
        if (activeUrl) URL.revokeObjectURL(activeUrl);
        mediaObjectUrlRef.current = '';
    }, [mediaObjectUrlRef]);

    const togglePlayback = () => {
        const media = mediaRef.current;
        if (!media) return;
        if (media.paused) media.play().catch((err) => setMediaError(err.message || 'Playback failed'));
        else media.pause();
    };

    // Fixing words while watching (evaluation 2026-11-19): leaving a sentence
    // for anything but another sentence follows the playback again, unless
    // following was off before or the reader scrolled away meanwhile.
    const inSentence = (node) => !!node?.closest?.('[data-transcript-segment="true"]');
    const leaveSentence = (e) => {
        if (!inSentence(e.target) || inSentence(e.relatedTarget) || !editPausedRef.current) return;
        // Switching to another app (to look a name up) is not leaving: the
        // caret comes back to the same sentence.
        if (!document.hasFocus()) return;
        editPausedRef.current = false;
        setFollowPlayback(true);
    };
    // With the caret in a sentence: Esc plays or pauses, Shift+Esc goes back
    // 3 seconds and plays. Esc that ends an input method's composition is left
    // to it.
    const REWIND_SECONDS = 3;
    const sentenceKeys = (e) => {
        if (e.key !== 'Escape' || e.nativeEvent?.isComposing || !inSentence(e.target)) return;
        e.preventDefault();
        e.stopPropagation();
        const media = mediaRef.current;
        if (!media) return;
        if (!e.shiftKey) {
            togglePlayback();
            return;
        }
        seekMediaTo((Number(media.currentTime) || 0) - REWIND_SECONDS, {follow: false});
        media.play()?.catch?.((err) => setMediaError(err.message || 'Playback failed'));
    };

    return {
        mediaRef,
        playbackClock,
        playbackDuration,
        mediaPlaying,
        setMediaPlaying,
        followPlayback,
        setFollowPlayback,
        playbackRate,
        setPlaybackRate,
        activeReviewMode,
        shouldShowVideoReview,
        activeSegmentIndex,
        transcriptScrollRef,
        updateMediaCurrentTime,
        restoreMediaPosition,
        seekMediaTo,
        seekToSegment,
        noteFollow,
        pauseFollowing,
        resumeFollowing,
        switchReviewMode,
        seekRow,
        editRow,
        stopFollowing,
        focusSentence,
        registerRow,
        togglePlayback,
        leaveSentence,
        sentenceKeys,
    };
};
