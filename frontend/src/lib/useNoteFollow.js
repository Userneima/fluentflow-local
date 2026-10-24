import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {sectionAt, timesByKey} from './noteFollow.js';

// The editor page's side of the note following playback (decision 6.37,
// mock-up behind `?follow=on`): reads the section times, asks the service to
// match an older note once, works out the section playing on each tick of
// the player (stored only when it changes, like the transcript's sentence),
// and keeps the following switch.

const POLL_MS = 20000;
const WAITING = new Set(['aligning', 'missing', 'partial']);

export const useNoteFollow = ({enabled, taskId, noteReady, playingCut, hasMedia, clock, apiFetch, apiBase, seek}) => {
    const [table, setTable] = useState(null);
    const [following, setFollowing] = useState(true);
    const [activeKey, setActiveKey] = useState(null);
    const [revealToken, setRevealToken] = useState(0);
    const askedRef = useRef('');

    useEffect(() => {
        setTable(null);
        setActiveKey(null);
        setFollowing(true);
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
            } catch (_) { /* tried again on the next tick */ }
            if (stopped) return;
            if (status) setTable(status);
            // An older note with no times: ask once per opening. Refused in
            // the read-only preview, which only shows what is stored.
            if (status && ['missing', 'partial'].includes(status.state) && askedRef.current !== taskId) {
                askedRef.current = taskId;
                try {
                    const response = await apiFetch(url, {method: 'POST'});
                    if (response.ok && !stopped) setTable(await response.json());
                } catch (_) { /* the editor works without the times */ }
            }
            if (!stopped && (!status || WAITING.has(status.state))) timer = window.setTimeout(read, POLL_MS);
        };
        read();
        return () => { stopped = true; if (timer) window.clearTimeout(timer); };
    }, [enabled, taskId, noteReady, apiBase, apiFetch]);

    // The times are on one file; shown only while that file is playing.
    const onPlayingFile = table?.timeline === 'debreath_media' ? playingCut : table?.timeline ? !playingCut : false;
    const times = useMemo(
        () => (enabled && hasMedia && onPlayingFile ? timesByKey(table?.sections) : new Map()),
        [enabled, hasMedia, onPlayingFile, table],
    );

    useEffect(() => {
        if (!times.size) { setActiveKey(null); return undefined; }
        const update = (time) => {
            const key = sectionAt(time, times);
            if (key) setActiveKey((previous) => (previous === key ? previous : key));
        };
        update(clock.get());
        return clock.subscribe(update);
    }, [clock, times]);

    const pause = useCallback(() => setFollowing((value) => (value ? false : value)), []);
    const resume = useCallback(() => {
        setFollowing(true);
        setRevealToken((value) => value + 1);
    }, []);
    const seekSection = useCallback((seconds) => {
        seek(seconds);
        resume();
    }, [seek, resume]);

    return {
        available: times.size > 0,
        aligning: enabled && !times.size && table?.state === 'aligning',
        following,
        activeKey,
        toggle: () => (following ? setFollowing(false) : resume()),
        resume,
        editorProps: enabled ? {times, activeKey, following, revealToken, onSeek: seekSection, onUserScroll: pause} : null,
    };
};
