// @vitest-environment jsdom

// Requirements (owner, 2026-10-15): after a task, the cuts that carry the most
// risk are listed in the original recording's time with the model's reason.
// Each can be answered "剪得对" (it folds to one line that can be taken back) or
// "这段不该剪" (the stretch is put back and the cut rendered again). Once every
// one is answered the panel stops asking. A task with nothing to confirm shows
// nothing.

import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {CutReview} from './CutReview.jsx';

vi.mock('../app/shared.jsx', () => ({
    API_BASE: '/api',
    apiFetch: vi.fn(),
}));
import {apiFetch} from '../app/shared.jsx';

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

const job = (survey) => ({task_id: 't1', result: {debreath: {survey}}});
const REVIEW = [
    {kind: 'removed_talk', start: 832.4, end: 2316.0, seconds: 1483.6, reason: '会议在「好，拜拜」之后结束。'},
    {kind: 'long_gap', start: 0, end: 41, seconds: 41, reason: '这一段没人说话，整段剪掉了。'},
];
const ok = (body) => Promise.resolve({ok: true, json: () => Promise.resolve(body)});

describe('CutReview', () => {
    it('lists each cut in the original time with its reason', () => {
        render(<CutReview job={job({review: REVIEW, answers: {}})}/>);
        expect(screen.getByText('有 2 处剪得比较多，请确认')).toBeTruthy();
        expect(screen.getByText('原片 13:52 – 38:36')).toBeTruthy();
        expect(screen.getByText('剪掉 24 分 44 秒')).toBeTruthy();
        expect(screen.getByText('会议在「好，拜拜」之后结束。')).toBeTruthy();
    });

    it('shows nothing when there is nothing to confirm', () => {
        const {container} = render(<CutReview job={job({review: [], answers: {}})}/>);
        expect(container.innerHTML).toBe('');
    });

    it('records 剪得对, folds the item, and stops asking once all are answered', async () => {
        apiFetch.mockImplementation(() => ok({survey: {review: REVIEW, answers: {0: 'right', 1: 'right'}}}));
        render(<CutReview job={job({review: REVIEW, answers: {1: 'right'}})}/>);
        fireEvent.click(screen.getByText('剪得对'));
        await waitFor(() => expect(screen.getByText('2 处剪辑都已确认')).toBeTruthy());
        expect(apiFetch.mock.calls[0][0]).toBe('/api/jobs/t1/cut-review/0');
        expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({answer: 'right'});
        expect(screen.getAllByText('撤回').length).toBe(2);
    });

    it('asks for the stretch back on 这段不该剪 and says it is rendering', async () => {
        apiFetch.mockImplementation(() => ok({ok: true}));
        render(<CutReview job={job({review: REVIEW.slice(0, 1), answers: {}})}/>);
        fireEvent.click(screen.getByText('这段不该剪'));
        await waitFor(() => expect(screen.getByText('正在把这一段放回去，重新导出剪后视频…')).toBeTruthy());
        expect(apiFetch.mock.calls[0][0]).toBe('/api/jobs/t1/cut-review/0/restore');
    });
});
