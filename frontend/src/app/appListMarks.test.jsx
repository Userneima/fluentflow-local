// @vitest-environment jsdom

// Requirement (review of 5554f67, 2026-11-26): a pin clicked while the app's
// first read of the whole list is still on its way stays pinned when that
// read, made before the click, arrives.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, render, screen} from '@testing-library/react';

let answerJobs;
vi.mock('./apiConfig.js', async () => {
    const actual = await vi.importActual('./apiConfig.js');
    return {
        ...actual,
        apiFetch: async (url) => {
            if (String(url).endsWith('/jobs')) {
                return new Promise((resolve) => { answerJobs = (jobs) => resolve({ok: true, json: async () => ({jobs})}); });
            }
            return {ok: true, json: async () => ({})};
        },
    };
});

const {LocalAppProvider} = await import('./LocalAppProvider.jsx');
const {useApp} = await import('./AppContext.jsx');
const {isPinnedJob} = await import('../lib/jobMappers.js');

const UPDATED = '2026-11-20T00:00:00Z';
const row = (metadata = {}) => ({task_id: 't1', status: 'completed', created_at: UPDATED, updated_at: UPDATED,
    metadata: {display_title: '第四课', ...metadata}, result: {task_id: 't1'}});

let ingest;
const Probe = () => {
    const {tasks, ingestJobs} = useApp();
    ingest = ingestJobs;
    const job = tasks.find((item) => item.task_id === 't1');
    return <p data-testid="pin">{job ? String(isPinnedJob(job)) : 'none'}</p>;
};

describe('the first read of the whole list', () => {
    afterEach(cleanup);

    it('does not undo a pin clicked while it was on its way', async () => {
        localStorage.clear();
        render(<LocalAppProvider><Probe/></LocalAppProvider>);
        await act(async () => {
            ingest([row({pinned_at: '2026-11-26T08:00:00+00:00', marks_changed_at: '2026-11-26T08:00:00.000000+00:00'})]);
        });
        expect(screen.getByTestId('pin').textContent).toBe('true');
        await act(async () => { answerJobs([row()]); });
        expect(screen.getByTestId('pin').textContent).toBe('true');
    });
});
