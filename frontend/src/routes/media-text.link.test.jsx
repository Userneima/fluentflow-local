// @vitest-environment jsdom

// The link box on the start page, mounted. Written from what a person pasting a
// link should be told: which links work (podcasts do not), and, when they
// pasted several, that only the first one was used.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';

const createVideoSourceJob = vi.fn();

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        history: [],
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
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
        useApi: () => ({
            createVideoSourceJob,
            getCredentialsStatus: async () => ({}),
        }),
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
        </Routes>
    </MemoryRouter>,
);

describe('the link box', () => {
    beforeEach(() => {
        localStorage.setItem('fluentflow_settings', JSON.stringify({defaultSourceMode: 'link'}));
        createVideoSourceJob.mockReset();
        seenState = null;
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
});
