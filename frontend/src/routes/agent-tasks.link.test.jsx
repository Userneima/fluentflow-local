// @vitest-environment jsdom

// The records page for link tasks, mounted. Written from what the person
// pressing "submit again" on a failed link expects, and from what a running
// download should look like when the platform did not say how big it is.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

let tasks = [];
const createVideoSourceJob = vi.fn(async () => ({ok: true, job: {task_id: 'new-1', status: 'queued'}}));

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
            createVideoSourceJob,
            fetchJobSourceFile: async () => { throw new Error('no stored source'); },
            fetchJobArtifactFile: async () => { throw new Error('no artifact'); },
            enqueueProcessFiles: async () => ({}),
            retryJob: async () => ({}),
        }),
    };
});

const {default: AgentTasks} = await import('./agent-tasks.jsx');

const failedLink = (overrides = {}) => ({
    task_id: 'link-1',
    client_id: 'local-single-user',
    status: 'failed',
    stage: 'failed',
    source_type: 'video_link',
    source_filename: '抖音视频链接',
    error_reason: '平台拒绝下载',
    created_at: '2026-10-08T10:00:00Z',
    updated_at: '2026-10-08T10:00:00Z',
    metadata: {
        display_title: '一条很长的分享',
        video_source_url: 'https://v.douyin.com/abcDEF12/',
        video_source_input_preview: '这期讲得特别好，'.repeat(25),
        queue_options: {generate_visuals: 'true', voice_enhance: 'true', title: '我起的名字', cookies_from_browser: 'safari'},
    },
    ...overrides,
});

const mount = (state) => render(
    <MemoryRouter initialEntries={[{pathname: '/agent', state}]}><AgentTasks/></MemoryRouter>,
);

describe('submitting a failed link again', () => {
    beforeEach(() => {
        createVideoSourceJob.mockClear();
        localStorage.setItem('fluentflow_settings', JSON.stringify({videoCookiesBrowser: 'chrome'}));
    });
    afterEach(() => {
        cleanup();
        localStorage.clear();
    });

    it('sends the full link with the stored options and the browser login chosen now', async () => {
        tasks = [failedLink()];
        mount();
        (await screen.findByRole('button', {name: /重新提交/})).click();
        await waitFor(() => expect(createVideoSourceJob).toHaveBeenCalledTimes(1));
        const [input, options] = createVideoSourceJob.mock.calls[0];
        expect(input).toBe('https://v.douyin.com/abcDEF12/');
        expect(options).toMatchObject({generateVisuals: true, voiceEnhance: true, title: '我起的名字', cookiesFromBrowser: 'chrome'});
    });

    it('offers no retry when retrying cannot change the outcome', async () => {
        tasks = [failedLink({error_reason: '这个抖音链接是图文作品，没有视频可以下载。'})];
        mount();
        expect(await screen.findAllByText('这个抖音链接是图文作品，没有视频可以下载。')).toBeTruthy();
        expect(screen.queryByRole('button', {name: /重新提交/})).toBeNull();
    });
});

describe('a download of unknown size', () => {
    afterEach(cleanup);

    it('shows how much has arrived instead of a fixed 10%', async () => {
        tasks = [{
            task_id: 'dl-1',
            client_id: 'local-single-user',
            status: 'running',
            stage: 'downloading',
            progress: 10,
            source_type: 'video_link',
            created_at: '2026-10-08T10:00:00Z',
            updated_at: '2026-10-08T10:00:00Z',
            metadata: {display_title: 'B 站讲座', video_source_progress: {message: '正在下载视频', loaded_bytes: 52428800}},
        }];
        mount();
        expect(await screen.findAllByText(/已下载 50(\.0)? MB/)).toBeTruthy();
        expect(screen.queryByText('10%')).toBeNull();
    });
});

describe('a pasted text with several links', () => {
    afterEach(cleanup);

    it('says that only the first link was used', async () => {
        tasks = [];
        mount({job: {task_id: 'new-1', status: 'queued'}, extraUrlsIgnored: true});
        const notice = await screen.findByTestId('extra-urls-ignored');
        expect(notice.textContent).toMatch(/只处理了第一个/);
    });
});
