import {useSyncExternalStore} from 'react';

// The player's current time, kept outside React state. It changes several times
// a second while something plays; held as state on the editor page, every tick
// re-rendered the whole page, transcript and note included (measured
// 2026-10-22: 55–93 ms per tick on a 7104-sentence task). Only the parts that
// show the time subscribe: the clock and the progress bar.
export const createPlaybackClock = () => {
    let time = 0;
    const listeners = new Set();
    return {
        get: () => time,
        set: (next) => {
            if (next === time) return;
            time = next;
            listeners.forEach((listener) => listener(time));
        },
        subscribe: (listener) => {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
};

export const usePlaybackTime = (clock) => useSyncExternalStore(clock.subscribe, clock.get, clock.get);
