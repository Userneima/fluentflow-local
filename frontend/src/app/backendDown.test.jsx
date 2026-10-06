// @vitest-environment jsdom

// The local service is not answering. Written from what the person should see:
// one line at the top saying to reopen FluentFlow Local and that nothing is
// lost, and no way to submit a job into the void.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

// Whether anything is listening on the loopback port. Every request fails the
// same way when nothing is, which is exactly what fetch does.
let serviceUp = false;

vi.mock('./apiConfig.js', async () => {
    const actual = await vi.importActual('./apiConfig.js');
    return {
        ...actual,
        apiFetch: async () => {
            if (!serviceUp) throw new TypeError('Failed to fetch');
            return {ok: true, json: async () => ({})};
        },
    };
});

const api = {
    enqueueProcessFiles: async () => ({}),
    createVideoSourceJob: async () => ({}),
    summarizeTranscriptFile: async () => ({}),
    cancelJob: async () => ({}),
    chooseLocalMedia: async () => ({}),
    chooseLocalFolder: async () => ({}),
    processLocalFolder: async () => ({}),
    locateDroppedFile: async () => null,
    processLocalPaths: async () => ({}),
    getCredentialsStatus: async () => ({}),
};

vi.mock('./shared.jsx', async () => {
    const actual = await vi.importActual('./shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useApi: () => api,
    };
});

const {LocalAppProvider} = await import('./LocalAppProvider.jsx');
const {default: BackendStatusBanner} = await import('../components/BackendStatusBanner.jsx');
const {default: MediaText} = await import('../routes/media-text.jsx');

const mount = () => render(
    <MemoryRouter initialEntries={['/media-text']}>
        <LocalAppProvider>
            <BackendStatusBanner/>
            <MediaText/>
        </LocalAppProvider>
    </MemoryRouter>
);

describe('when the local service does not answer', () => {
    beforeEach(() => {
        localStorage.clear();
        serviceUp = false;
    });
    afterEach(cleanup);

    it('shows the reopen banner and disables submitting', async () => {
        mount();
        const banner = await screen.findByTestId('backend-down-banner');
        expect(banner.textContent).toContain('本机服务没有响应，请重新打开 FluentFlow Local；任务和记录都还在。');
        expect(screen.getByTestId('submit-video-link').disabled).toBe(true);
    });

    it('shows nothing and leaves submitting enabled while the service answers', async () => {
        serviceUp = true;
        mount();
        await waitFor(() => expect(screen.getByTestId('submit-video-link').disabled).toBe(false));
        expect(screen.queryByTestId('backend-down-banner')).toBeNull();
    });
});
