// @vitest-environment jsdom

// The task list refreshing itself. Written from what the person sees on the
// records page: a running task's progress moves without pressing refresh, an
// idle page does not hammer the service, a service that has gone away is named
// as the cause, and leaving the page stops the asking.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, renderHook} from '@testing-library/react';

// What the service answers, and every question it was asked.
let answer = async () => [];
let getJobsCalls = [];
let ingested = [];
let reported = [];
let lang = 'zh';

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang}),
        useApi: () => ({
            getJobs: async (options) => {
                getJobsCalls.push(options);
                return answer();
            },
        }),
    };
});

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        tasks: [],
        ingestJobs: (jobs) => ingested.push(...jobs),
        reportBackendError: (err) => reported.push(err),
    }),
}));

const {useJobPolling} = await import('./useJobPolling.js');
const {BACKEND_DOWN_MESSAGE} = await import('./backendHealth.js');

const wording = {refreshFailedZh: '刷新记录失败', refreshFailedEn: 'Could not refresh records'};
const mount = (hasLiveJobs) => renderHook(
    ({live}) => useJobPolling({hasLiveJobs: live, ...wording}),
    {initialProps: {live: hasLiveJobs}},
);
const tick = (ms) => act(() => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
    vi.useFakeTimers();
    answer = async () => [];
    getJobsCalls = [];
    ingested = [];
    reported = [];
    lang = 'zh';
});

afterEach(() => {
    cleanup();
    vi.useRealTimers();
});

describe('while a task is running', () => {
    it('asks the service again every 5 seconds without the person doing anything', async () => {
        mount(true);
        await tick(0);
        expect(getJobsCalls).toHaveLength(1);
        await tick(5000);
        expect(getJobsCalls).toHaveLength(2);
        await tick(5000);
        expect(getJobsCalls).toHaveLength(3);
    });

    it('pushes every fetched task into the shared list, marked as a backend row', async () => {
        answer = async () => [{task_id: 't1', status: 'running', updated_at: '2026-10-06T01:00:00Z'}];
        mount(true);
        await tick(0);
        expect(ingested.map((job) => job.task_id)).toEqual(['t1']);
        expect(ingested[0].task_state).toBeTruthy();
    });

    it('reads everything once, then asks only for rows newer than the latest it has seen', async () => {
        answer = async () => [
            {task_id: 'old', status: 'done', updated_at: '2026-10-06T01:00:00Z'},
            {task_id: 'new', status: 'running', updated_at: '2026-10-06T01:05:00Z'},
        ];
        mount(true);
        await tick(0);
        expect(getJobsCalls[0].updatedSince).toBe('');
        answer = async () => [];
        await tick(5000);
        expect(getJobsCalls[1].updatedSince).toBe('2026-10-06T01:05:00Z');
    });
});

describe('with no task running', () => {
    it('does not poll at the running cadence; one slow refresh still happens', async () => {
        mount(false);
        await tick(0);
        expect(getJobsCalls).toHaveLength(1);
        await tick(5000);
        await tick(5000);
        expect(getJobsCalls).toHaveLength(1);
        await tick(20000);
        expect(getJobsCalls).toHaveLength(2);
    });

    it('switches to the fast cadence as soon as a task becomes live', async () => {
        const hook = mount(false);
        await tick(0);
        expect(getJobsCalls).toHaveLength(1);
        hook.rerender({live: true});
        await tick(0);
        expect(getJobsCalls).toHaveLength(2);
        await tick(5000);
        expect(getJobsCalls).toHaveLength(3);
    });
});

describe('when the refresh fails', () => {
    it('names the service as gone when nothing answered, not a generic refresh failure', async () => {
        answer = async () => { throw new TypeError('Failed to fetch'); };
        const hook = mount(true);
        await tick(0);
        expect(hook.result.current.error).toBe(BACKEND_DOWN_MESSAGE.zh);
        expect(hook.result.current.error).not.toBe(wording.refreshFailedZh);
        expect(reported).toHaveLength(1);
        expect(reported[0]).toBeInstanceOf(TypeError);
    });

    it('uses the page\'s own wording when the service answered with an error', async () => {
        answer = async () => { const err = new Error('HTTP 500'); err.status = 500; throw err; };
        const hook = mount(true);
        await tick(0);
        expect(hook.result.current.error).toBe(wording.refreshFailedZh);
    });

    it('clears the message once a later refresh succeeds', async () => {
        answer = async () => { throw new TypeError('Failed to fetch'); };
        const hook = mount(true);
        await tick(0);
        expect(hook.result.current.error).toBeTruthy();
        answer = async () => [];
        await tick(5000);
        expect(hook.result.current.error).toBeNull();
        expect(hook.result.current.loading).toBe(false);
    });
});

describe('after leaving the page', () => {
    it('stops asking the service', async () => {
        const hook = mount(true);
        await tick(0);
        await tick(5000);
        expect(getJobsCalls).toHaveLength(2);
        hook.unmount();
        await tick(30000);
        expect(getJobsCalls).toHaveLength(2);
    });
});
