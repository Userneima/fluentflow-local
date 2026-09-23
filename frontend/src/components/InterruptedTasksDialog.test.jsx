// @vitest-environment jsdom

// The notice after a service restart cut tasks off. It must list what was
// interrupted and how far each got, re-run only what can be re-run, and record
// that the user has seen it so no other window shows it again.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';

let interrupted = [];
let calls = [];
let retryFailures = {};
const ingested = [];

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({ingestJobs: (jobs) => ingested.push(...jobs)}),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useApi: () => ({
            getInterruptedJobs: async () => interrupted,
            acknowledgeInterruptedJobs: async (ids) => {
                calls.push(['ack', ids]);
                return {ok: true, acknowledged: ids};
            },
            retryJob: async (taskId) => {
                calls.push(['retry', taskId]);
                if (retryFailures[taskId]) throw new Error(retryFailures[taskId]);
                return {ok: true, job: {task_id: `${taskId}-retry`, status: 'running'}};
            },
        }),
    };
});

const {default: InterruptedTasksDialog} = await import('./InterruptedTasksDialog.jsx');

const halfway = {task_id: 'a', title: '第一讲', started: true, retryable: true, original_path: null};
const waiting = {task_id: 'b', title: '第二讲', started: false, retryable: true, original_path: null};
const moved = {
    task_id: 'c',
    title: '第三讲.mp4',
    started: false,
    retryable: false,
    original_path: '/Users/me/录音/第三讲.mp4',
};

beforeEach(() => {
    calls = [];
    retryFailures = {};
    ingested.length = 0;
    interrupted = [halfway, waiting, moved];
});

afterEach(() => cleanup());

describe('InterruptedTasksDialog', () => {
    it('lists each interrupted task, how far it got, and where a missing file was', async () => {
        render(<InterruptedTasksDialog/>);

        expect(await screen.findByText('FluentFlow 服务重启过，3 个任务被中断了')).toBeTruthy();
        expect(screen.getByText('第一讲')).toBeTruthy();
        expect(screen.getByText('处理到一半')).toBeTruthy();
        expect(screen.getAllByText('还在排队')).toHaveLength(2);
        expect(screen.getByText('原文件不在了，需要重新提交')).toBeTruthy();
        expect(screen.getByText(/\/Users\/me\/录音\/第三讲\.mp4/)).toBeTruthy();
        expect(screen.getByRole('button', {name: '全部重新处理（2）'})).toBeTruthy();
    });

    it('re-runs every retryable task, acknowledges all of them, and shows failures in the dialog', async () => {
        retryFailures = {b: 'Source file not found'};
        render(<InterruptedTasksDialog/>);

        fireEvent.click(await screen.findByRole('button', {name: '全部重新处理（2）'}));

        await waitFor(() => expect(screen.getByRole('button', {name: '知道了'})).toBeTruthy());
        expect(calls).toEqual([
            ['ack', ['a', 'b', 'c']],
            ['retry', 'a'],
            ['retry', 'b'],
        ]);
        expect(ingested.map((job) => job.task_id)).toEqual(['a-retry']);
        expect(screen.getByText('已重新开始处理')).toBeTruthy();
        expect(screen.getByTestId('interrupted-retry-error')).toBeTruthy();

        fireEvent.click(screen.getByRole('button', {name: '知道了'}));
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('acknowledges without retrying when the user chooses later', async () => {
        render(<InterruptedTasksDialog/>);

        fireEvent.click(await screen.findByRole('button', {name: '稍后再说'}));

        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
        expect(calls).toEqual([['ack', ['a', 'b', 'c']]]);
    });

    it('stays hidden when nothing was interrupted', async () => {
        interrupted = [];
        render(<InterruptedTasksDialog/>);

        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(screen.queryByRole('dialog')).toBeNull();
    });
});
