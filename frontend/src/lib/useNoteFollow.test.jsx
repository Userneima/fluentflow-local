// @vitest-environment jsdom

// The editor page's side of the note following playback (decision 6.37).
// Written from the requirements:
//   - Times are shown only while the file they are on is the one playing.
//   - The section highlighted is the one playing; it stays until another
//     section's main span starts. A section drawing on the moment only through
//     another span (the recap at the end) is listed to mark lightly and is
//     never the one followed (owner, 2026-10-25).
//   - Scrolling in the note pauses following; resuming follows again and
//     brings the section playing back into view.
//   - Clicking a section's time highlights that section, even where another
//     section's span starts at the same second.
//   - The page keeps looking while the note is open and asks the service to
//     match whatever has no time, so a heading renamed here gets its time
//     back without a reload.
//   - When matching failed, the page can say so.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, renderHook} from '@testing-library/react';
import {createPlaybackClock} from './playbackClock.js';
import {noteSections} from './noteFollow.js';
import {POLL_MS, useNoteFollow} from './useNoteFollow.js';

const NOTE = ['## 八、讨论', '', '讨论的话。', '', '## 九、决定', '', '决定的话。', '', '## 十、待办', '', '待办的话。'].join('\n');
const hashOf = (markdown, heading) => noteSections(markdown).find((s) => s.heading === heading).bodyHash;
const row = (id, heading, primary, others = []) => ({id, heading, level: 2, body_hash: hashOf(NOTE, heading), primary, others});
const READY = {
    state: 'ready',
    timeline: 'debreath_media',
    sections: [
        row('r8', '八、讨论', [100, 600]),
        row('r9', '九、决定', [1939, 1972], [[619, 652]]),
        row('r10', '十、待办', [1939, 1972]),
    ],
};

const response = (body) => ({ok: true, json: async () => body});

const setup = ({statuses = [READY], playingCut = true, markdown = NOTE} = {}) => {
    const clock = createPlaybackClock();
    const seek = vi.fn();
    const calls = [];
    let next = 0;
    const apiFetch = vi.fn(async (url, init = {}) => {
        calls.push(init.method || 'GET');
        const body = statuses[Math.min(next, statuses.length - 1)];
        if (!init.method) next += 1;
        return response(body);
    });
    const hook = renderHook((props) => useNoteFollow({
        enabled: true, taskId: 't1', noteReady: true, hasMedia: true, apiBase: '', clock, apiFetch, seek, playingCut, markdown, ...props,
    }), {initialProps: {}});
    return {...hook, clock, seek, calls};
};

const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('useNoteFollow', () => {
    it('shows times only while the file they are on is playing', async () => {
        const original = setup({playingCut: false});
        await settle();
        expect(original.result.current.available).toBe(false);
        expect(original.result.current.editorProps.rows).toEqual([]);

        const cut = setup({playingCut: true});
        await settle();
        expect(cut.result.current.available).toBe(true);
        expect(cut.result.current.editorProps.rows.map((r) => r.id)).toEqual(['r8', 'r9', 'r10']);
    });

    it('highlights the section playing and keeps it through time no section claims', async () => {
        const {result, clock} = setup();
        await settle();
        act(() => clock.set(200));
        expect(result.current.activeKey).toBe('r8');
        expect(result.current.alsoKeys).toEqual([]);
        act(() => clock.set(630));
        expect(result.current.activeKey).toBe('r8'); // 九 draws on it only through another span
        expect(result.current.alsoKeys).toEqual(['r9']);
        act(() => clock.set(5000));
        expect(result.current.activeKey).toBe('r8');
        expect(result.current.alsoKeys).toEqual([]);
        act(() => clock.set(1950));
        expect(result.current.activeKey).toBe('r9'); // 九 and 十 share one main span: the first in the note
        expect(result.current.editorProps.alsoKeys).toEqual(['r10']);
    });

    it('pauses when the reader scrolls the note and comes back to the section playing on resume', async () => {
        const {result} = setup();
        await settle();
        expect(result.current.following).toBe(true);
        act(() => result.current.editorProps.onUserScroll());
        expect(result.current.following).toBe(false);
        const token = result.current.editorProps.revealToken;
        act(() => result.current.resume());
        expect(result.current.following).toBe(true);
        expect(result.current.editorProps.revealToken).toBe(token + 1);
    });

    it('highlights the section whose time was clicked, even when another starts at the same second', async () => {
        const {result, clock, seek} = setup();
        await settle();
        act(() => result.current.editorProps.onUserScroll());
        act(() => result.current.editorProps.onSeek(1939, 'r10'));
        act(() => clock.set(1939));
        expect(seek).toHaveBeenCalledWith(1939);
        expect(result.current.activeKey).toBe('r10');
        expect(result.current.following).toBe(true);
        act(() => clock.set(1950));
        expect(result.current.activeKey).toBe('r10');
        // Once playback leaves that section, the ordinary rule again.
        act(() => clock.set(200));
        expect(result.current.activeKey).toBe('r8');
        act(() => clock.set(1945));
        expect(result.current.activeKey).toBe('r9');
    });

    it('keeps looking while the note is open and asks for a renamed heading\'s time', async () => {
        const renamed = NOTE.replace('## 十、待办', '## 十、接下来的待办');
        const {result, rerender, calls} = setup({statuses: [READY, {...READY, state: 'partial'}]});
        await settle();
        expect(calls).toEqual(['GET']);

        rerender({markdown: renamed});
        // The renamed heading has no time until the service matches it.
        expect(result.current.editorProps.rows.length).toBe(3);
        await act(async () => { vi.advanceTimersByTime(POLL_MS); });
        await settle();
        expect(calls).toEqual(['GET', 'GET', 'POST']);
        await act(async () => { vi.advanceTimersByTime(POLL_MS); });
        await settle();
        expect(calls.filter((call) => call === 'GET')).toHaveLength(3);
    });

    it('says why there are no times when matching failed, and asks again (the service decides when)', async () => {
        const {result, calls} = setup({statuses: [{state: 'failed', sections: [], timeline: null, error: '这台电脑上用不了 Claude，章节时间没有对上。'}]});
        await settle();
        expect(calls).toEqual(['GET', 'POST']);
        expect(result.current.available).toBe(false);
        expect(result.current.failedReason).toContain('用不了 Claude');
    });
});
