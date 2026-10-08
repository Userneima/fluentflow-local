// @vitest-environment jsdom

// The link box on the start page, mounted. Written from what a person pasting a
// link should be told: which links work (podcasts do not), and, when they
// pasted several, that only the first one was used.
//
// Pasting a link that was already submitted must not process it twice without
// asking: still running → taken to that task, told nothing new was started;
// already finished → asked whether to open the earlier one or process again.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';

const createVideoSourceJob = vi.fn();
const getJob = vi.fn();
let lastResultSet = null;
const stableApi = {createVideoSourceJob, getJob, getCredentialsStatus: async () => ({})};

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        history: [],
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: (value) => { lastResultSet = value; },
        setLastSourceFile: () => {},
        addLarkExport: () => {},
        runtimeConfig: {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}},
        setPendingUploadAbort: () => {},
        abortPendingUpload: () => {},
        backendDown: false,
        reportBackendError: () => {},
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        // One object for the life of the test, like the real useApi: a fresh
        // getCredentialsStatus each render re-runs the page's effect forever.
        useApi: () => stableApi,
    };
});

const {default: MediaText} = await import('./media-text.jsx');

let seenState = null;
const AgentProbe = () => {
    seenState = useLocation().state;
    return <p>agent page</p>;
};

const mount = () => render(
    <MemoryRouter initialEntries={['/media-text?mode=media']}>
        <Routes>
            <Route path="/media-text" element={<MediaText/>}/>
            <Route path="/agent" element={<AgentProbe/>}/>
            <Route path="/editor" element={<p>editor page</p>}/>
        </Routes>
    </MemoryRouter>,
);

describe('the link box', () => {
    beforeEach(() => {
        localStorage.setItem('fluentflow_settings', JSON.stringify({defaultSourceMode: 'link'}));
        createVideoSourceJob.mockReset();
        getJob.mockReset();
        seenState = null;
        lastResultSet = null;
    });
    afterEach(() => {
        cleanup();
        localStorage.clear();
    });

    it('asks for a video link and names the platforms, without promising podcasts', () => {
        mount();
        expect(screen.getByText('视频链接')).toBeTruthy();
        expect(screen.queryByText(/播客/)).toBeNull();
        const box = screen.getByRole('textbox');
        expect(box.getAttribute('placeholder')).toMatch(/抖音/);
        expect(box.getAttribute('placeholder')).toMatch(/Bilibili/);
        expect(box.getAttribute('placeholder')).toMatch(/YouTube/);
    });

    it('carries the "only the first link was used" answer to the records page', async () => {
        createVideoSourceJob.mockResolvedValue({ok: true, extra_urls_ignored: true, job: {task_id: 't-1', status: 'queued'}});
        mount();
        fireEvent.change(screen.getByRole('textbox'), {target: {value: 'https://b23.tv/a https://b23.tv/b'}});
        fireEvent.click(screen.getByRole('button', {name: /开始|处理|提交/}));
        await waitFor(() => expect(screen.getByText('agent page')).toBeTruthy());
        expect(seenState.extraUrlsIgnored).toBe(true);
    });

    const paste = (link) => {
        fireEvent.change(screen.getByRole('textbox'), {target: {value: link}});
        fireEvent.click(screen.getByRole('button', {name: /开始|处理|提交/}));
    };
    const finishedDuplicate = () => Object.assign(new Error('这个链接已经处理过了'), {
        status: 409,
        payload: {detail: {code: 'duplicate_link', existing_task_id: 'old-7', existing_status: 'completed', message: '这个链接已经处理过了'}},
    });

    it('takes the person to the task already processing the link, and says nothing new was started', async () => {
        createVideoSourceJob.mockResolvedValue({ok: true, duplicate_of_active: true, job: {task_id: 'run-3', status: 'running'}});
        mount();
        paste('https://b23.tv/a');
        await waitFor(() => expect(screen.getByText('agent page')).toBeTruthy());
        expect(seenState.duplicateOfActive).toBe(true);
        expect(seenState.highlightTaskId).toBe('run-3');
        expect(createVideoSourceJob).toHaveBeenCalledTimes(1);
    });

    it('asks before processing a finished link again, and "再处理一次" resubmits allowing the duplicate', async () => {
        createVideoSourceJob
            .mockRejectedValueOnce(finishedDuplicate())
            .mockResolvedValueOnce({ok: true, job: {task_id: 'new-9', status: 'queued'}});
        mount();
        paste('https://b23.tv/a');
        expect(await screen.findByText('这个链接已经处理过了（任务 old-7）。要再处理一次吗？')).toBeTruthy();
        expect(screen.getByText('打开已有的')).toBeTruthy();
        // Nothing has been started yet.
        expect(createVideoSourceJob).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByText('再处理一次'));
        await waitFor(() => expect(screen.getByText('agent page')).toBeTruthy());
        expect(createVideoSourceJob).toHaveBeenCalledTimes(2);
        const [input, options] = createVideoSourceJob.mock.calls[1];
        expect(input).toBe('https://b23.tv/a');
        expect(options.allowDuplicate).toBe(true);
        expect(createVideoSourceJob.mock.calls[0][1].allowDuplicate).toBeUndefined();
    });

    it('"打开已有的" opens the earlier result instead of processing again', async () => {
        createVideoSourceJob.mockRejectedValueOnce(finishedDuplicate());
        getJob.mockResolvedValue({task_id: 'old-7', status: 'completed', result: {task_id: 'old-7', summary_markdown: '# 旧笔记'}});
        mount();
        paste('https://b23.tv/a');
        await screen.findByText('打开已有的');
        fireEvent.click(screen.getByText('打开已有的'));
        await waitFor(() => expect(screen.getByText('editor page')).toBeTruthy());
        expect(getJob.mock.calls[0][0]).toBe('old-7');
        expect(lastResultSet?.task_id).toBe('old-7');
        expect(createVideoSourceJob).toHaveBeenCalledTimes(1);
    });
});
