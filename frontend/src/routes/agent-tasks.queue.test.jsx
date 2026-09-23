// @vitest-environment jsdom

// The records page, mounted, on a queue that is not moving.
//
// Two things were unreadable here, and both only showed up by opening the page.
// A queued card said the same sentence whether the queue was working or wedged,
// so a row of them on an idle machine looked exactly like a row of them on a
// busy one. And when "submit again" was refused — the recording had been moved
// out of its folder — the reason went into the page banner, which every
// successful poll clears; the button spun and nothing appeared.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

let tasks = [];
let retryOutcome = null;

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        tasks,
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
        addToHistory: () => {},
        removeFromHistory: () => {},
        restoreTask: () => {},
        ingestJobs: () => {},
        markCancelled: () => {},
        revertCancelled: () => {},
        abortPendingUpload: () => {},
        runtimeConfig: {jobRetryFromStoredSource: true},
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useApi: () => ({
            getJob: async () => ({}),
            getJobs: async () => [],
            cancelJob: async () => ({}),
            deleteJob: async () => ({}),
            createVideoSourceJob: async () => ({}),
            fetchJobSourceFile: async () => { throw new Error('no stored source'); },
            fetchJobArtifactFile: async () => { throw new Error('no artifact'); },
            enqueueProcessFiles: async () => ({}),
            retryJob: async () => retryOutcome(),
        }),
    };
});

const {default: AgentTasks} = await import('./agent-tasks.jsx');

const ahead = {
    task_id: 'ahead-1111',
    client_id: 'local-single-user',
    status: 'running',
    stage: 'stt',
    progress: 40,
    source_filename: '0811-morning.mp4',
    created_at: '2026-08-28T10:00:00Z',
    updated_at: '2026-08-28T10:00:00Z',
    metadata: {stt_provider: 'local', display_title: '0811 上午 · 训练营第一讲'},
};

const waiting = {
    task_id: 'waiting-2222',
    client_id: 'local-single-user',
    status: 'queued',
    stage: 'queued',
    progress: 0,
    source_filename: '0811-afternoon.mp4',
    created_at: '2026-08-28T10:01:00Z',
    updated_at: '2026-08-28T10:01:00Z',
    metadata: {
        stt_provider: 'local',
        display_title: '0811 下午 · 学员项目讲评',
        queue_wait: {waiting_for: 'ahead-1111', since: '2026-08-28T10:01:00Z'},
        // The snapshot describes the step this task will run, and used to win.
        // For a task that has not started it is the least useful true thing here.
    },
    task_snapshot: {
        current_step: 'source_fetch',
        steps: [{id: 'source_fetch', detail: '等待准备可转写音频。'}],
    },
};

const movedFolderTask = {
    task_id: 'moved-3333',
    client_id: 'local-single-user',
    status: 'failed',
    stage: 'failed',
    progress: 100,
    source_filename: '0813-guest.mp4',
    error_reason: 'ffmpeg exited with code 1',
    created_at: '2026-08-28T09:00:00Z',
    updated_at: '2026-08-28T09:00:00Z',
    metadata: {
        stt_provider: 'local',
        display_title: '0813 · 客座分享',
        folder_intake: {chosen_with: 'folder_path', original_path: '/Users/y/Movies/0813-guest.mp4'},
    },
};

const mount = () => render(<MemoryRouter><AgentTasks/></MemoryRouter>);

describe('a queue that is not moving', () => {
    beforeEach(() => {
        tasks = [];
        retryOutcome = async () => ({ok: true});
        vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-08-28T10:53:13Z'));
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
    });

    it('names the recording it is waiting for, not a task id', async () => {
        tasks = [ahead, waiting];
        mount();

        const line = await screen.findByText(/在等「0811 上午 · 训练营第一讲」/);
        expect(line.textContent).toMatch(/已经等了 52:13/);
        expect(line.textContent).toMatch(/一次只处理一个/);
        // The task id identifies the right row to a developer and nothing at all
        // to the person who queued five lectures.
        expect(line.textContent).not.toMatch(/ahead-1111/);
    });

    it("wins over the snapshot own line for a task that has not started", async () => {
        tasks = [ahead, waiting];
        mount();

        await screen.findByText(/在等「0811 上午/);
        expect(screen.queryByText('等待准备可转写音频。')).toBeNull();
    });

    it('falls back to the id when the task ahead is not on screen', async () => {
        tasks = [waiting];
        mount();

        expect(await screen.findByText(/在等「ahead-1111」/)).toBeTruthy();
    });
});

describe('a recording that has been moved out of its folder', () => {
    beforeEach(() => {
        tasks = [movedFolderTask];
        retryOutcome = async () => {
            const err = new Error('原文件已不在这个位置，无法重新处理：/Users/y/Movies/0813-guest.mp4');
            err.status = 404;
            throw err;
        };
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
    });

    it('keeps the refusal on the card, where polling cannot wipe it', async () => {
        mount();

        (await screen.findByRole('button', {name: /重新提交/})).click();

        const shown = await screen.findByTestId('retry-error');
        expect(shown.textContent).toMatch(/原文件已不在这个位置/);
        // The path is the one thing the product cannot work out for the reader.
        expect(shown.textContent).toMatch(/0813-guest\.mp4/);
    });

    it('keeps showing it after the list refreshes', async () => {
        mount();

        (await screen.findByRole('button', {name: /重新提交/})).click();
        await screen.findByTestId('retry-error');

        // A successful poll clears the page-level banner. The card must not care.
        await new Promise((resolve) => setTimeout(resolve, 60));
        await waitFor(() => expect(screen.getByTestId('retry-error')).toBeTruthy());
    });
});
