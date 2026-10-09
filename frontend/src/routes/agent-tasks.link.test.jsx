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

    // Requirement: when the fix is a setting, the failed card takes the person
    // to it, and still offers "submit again" for afterwards.
    it('links a Douyin link refused for want of the fallback to that setting', async () => {
        tasks = [failedLink({error_reason: '抖音直接下载没有成功。可以把这个链接交给第三方解析服务再试（只发送链接本身）：在 设置 → 抖音备用解析 里允许后重试；AI 工具提交时传 allow_miuistore=true。'})];
        mount();
        const link = await screen.findByRole('link', {name: '去设置修改'});
        expect(link.getAttribute('href')).toBe('/settings#douyin-fallback');
        expect(screen.getByRole('button', {name: /重新提交/})).toBeTruthy();
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

describe('arriving from a link that was already being processed', () => {
    afterEach(() => cleanup());

    it('says nothing new was submitted and marks the task that has the link', async () => {
        const running = failedLink({task_id: 'run-3', status: 'running', stage: 'download', error_reason: ''});
        tasks = [failedLink(), running];
        mount({job: running, duplicateOfActive: true, highlightTaskId: 'run-3'});
        expect((await screen.findByTestId('duplicate-link-notice')).textContent).toMatch(/这个链接已经在处理中，没有重复提交/);
        const marked = document.querySelectorAll('[data-highlighted="true"]');
        expect(marked.length).toBe(1);
    });

    it('says nothing when arriving normally', async () => {
        tasks = [failedLink()];
        mount();
        await screen.findAllByRole('article').catch(() => null);
        expect(screen.queryByTestId('duplicate-link-notice')).toBeNull();
        expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(0);
    });
});

// Requirement: while a link downloads, its card shows the download's own line
// (the service's progress_message), and the note cell never reads that line as
// the note's state.
describe('a link task while it downloads', () => {
    afterEach(cleanup);

    const downloading = (overrides = {}) => ({
        task_id: 'dl-2',
        client_id: 'local-single-user',
        status: 'running',
        stage: 'resolving',
        progress: 5,
        source_type: 'video_link',
        created_at: '2026-10-08T10:00:00Z',
        updated_at: '2026-10-08T10:00:00Z',
        summary_status: null,
        metadata: {display_title: '抖音分享'},
        ...overrides,
    });

    it('shows the download line the service sends', async () => {
        tasks = [downloading({progress_message: '等待前面的链接下载完成'})];
        mount();
        expect(await screen.findByText('等待前面的链接下载完成')).toBeTruthy();
    });

    it('also reads the line from metadata', async () => {
        tasks = [downloading({metadata: {display_title: '抖音分享', progress_message: '正在解析链接'}})];
        mount();
        expect(await screen.findByText('正在解析链接')).toBeTruthy();
    });

    it('does not take a progress text left in summary_status for the note state', async () => {
        tasks = [downloading({summary_status: '正在保存视频信息', result: {summary_status: '正在保存视频信息'}})];
        mount();
        await screen.findByText('抖音分享');
        expect(screen.queryByText('正在保存视频信息')).toBeNull();
    });
});

// Requirement: a note the text model wrote because the frame note could not
// run says so on the card's note cell, with the reason when there is one.
describe('a task whose note the text model wrote instead', () => {
    afterEach(cleanup);

    it('says so in the note cell', async () => {
        tasks = [{
            task_id: 'fb-1',
            client_id: 'local-single-user',
            status: 'completed',
            stage: 'done',
            source_type: 'video',
            created_at: '2026-10-08T10:00:00Z',
            updated_at: '2026-10-08T10:00:00Z',
            metadata: {display_title: '讲座'},
            result: {
                task_id: 'fb-1',
                summary_status: 'completed',
                summary_chars: 1200,
                summary_markdown: '# 笔记',
                summary_written_from: 'text_fallback',
                note_fallback_reason: '本机 Claude 的登录已过期。',
            },
        }];
        mount();
        expect(await screen.findByText('这次改由文本模型按文字写的笔记（没有截图）。原因：本机 Claude 的登录已过期。')).toBeTruthy();
    });
});
