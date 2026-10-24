import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {matchRows, normalizeRows, noteSections, rowHolds, sectionAt} from './noteFollow.js';

// The editor page's side of the note following playback (decision 6.37,
// mock-up behind `?follow=on`). Reads the section times every POLL_MS while
// the note is open, and asks the service to match whatever has no time (an
// older note, a heading renamed here, an earlier try that failed): the
// service waits for typing to stop, backs off after a failure and ignores a
// repeated ask, so asking on every look is safe. Works out the section
// playing on each tick of the player, stored only when it changes, like the
// transcript's sentence. A section whose time was clicked stays the one
// highlighted while the time is inside its spans, even where another
// section's span starts at the same second.

export const POLL_MS = 20000;
const ASK = new Set(['missing', 'partial', 'failed']);

export const useNoteFollow = ({enabled, taskId, noteReady, markdown, playingCut, hasMedia, clock, apiFetch, apiBase, seek}) => {
    const [table, setTable] = useState(null);
    const [following, setFollowing] = useState(true);
    const [activeKey, setActiveKey] = useState(null);
    const [revealToken, setRevealToken] = useState(0);
    const pinnedRef = useRef(null);

    useEffect(() => {
        setTable(null);
        setActiveKey(null);
        setFollowing(true);
        pinnedRef.current = null;
    }, [taskId]);

    useEffect(() => {
        if (!enabled || !taskId || !noteReady) return undefined;
        let stopped = false;
        let timer = null;
        const url = `${apiBase}/jobs/${encodeURIComponent(taskId)}/note/section-times`;
        const read = async () => {
            let status = null;
            try {
                const response = await apiFetch(url);
                if (response.ok) status = await response.json();
            } catch (_) { /* tried again on the next look */ }
            if (!stopped && status && ASK.has(status.state)) {
                // Refused in the read-only preview, which shows what is stored.
                try {
                    const response = await apiFetch(url, {method: 'POST'});
                    if (response.ok) status = await response.json();
                } catch (_) { /* the editor works without the times */ }
            }
            if (stopped) return;
            if (status) setTable(status);
            timer = window.setTimeout(read, POLL_MS);
        };
        read();
        return () => { stopped = true; if (timer) window.clearTimeout(timer); };
    }, [enabled, taskId, noteReady, apiBase, apiFetch]);

    // The times are on one file; shown only while that file is playing.
    const onPlayingFile = table?.timeline === 'debreath_media' ? playingCut : table?.timeline ? !playingCut : false;
    const rows = useMemo(
        () => (enabled && hasMedia && onPlayingFile ? normalizeRows(table?.sections) : []),
        [enabled, hasMedia, onPlayingFile, table],
    );
    // The rows whose section is in the note as it is now (a renamed heading's
    // row is not, until the service matches it again).
    const sections = useMemo(() => (rows.length ? noteSections(markdown) : []), [rows, markdown]);
    const foundIds = useMemo(() => [...matchRows(rows, sections).values()].map((row) => row.id).sort().join(' '), [rows, sections]);
    const foundRows = useMemo(() => {
        const ids = new Set(foundIds.split(' '));
        return rows.filter((row) => ids.has(row.id));
    }, [rows, foundIds]);

    useEffect(() => {
        if (!foundRows.length) { setActiveKey(null); return undefined; }
        const update = (time) => {
            const pinned = foundRows.find((row) => row.id === pinnedRef.current);
            let key = null;
            if (pinned && rowHolds(pinned, time)) key = pinned.id;
            else {
                pinnedRef.current = null;
                key = sectionAt(time, foundRows);
            }
            if (key) setActiveKey((previous) => (previous === key ? previous : key));
        };
        update(clock.get());
        return clock.subscribe(update);
    }, [clock, foundRows]);

    const pause = useCallback(() => setFollowing((value) => (value ? false : value)), []);
    const resume = useCallback(() => {
        setFollowing(true);
        setRevealToken((value) => value + 1);
    }, []);
    const seekSection = useCallback((seconds, rowId) => {
        if (rowId) {
            pinnedRef.current = rowId;
            setActiveKey(rowId);
        }
        seek(seconds);
        resume();
    }, [seek, resume]);

    const available = foundRows.length > 0;
    return {
        available,
        aligning: enabled && !available && table?.state === 'aligning',
        // Why there are no times, when a try failed.
        failedReason: enabled && !available && table?.state === 'failed' ? (table.error || '') : '',
        following,
        activeKey,
        toggle: () => (following ? setFollowing(false) : resume()),
        resume,
        editorProps: enabled ? {rows, activeKey, following, revealToken, onSeek: seekSection, onUserScroll: pause} : null,
    };
};
