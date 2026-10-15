// @vitest-environment jsdom

// Requirements (owner, 2026-10-15): a task whose cut has a processing report
// offers "看看这条视频处理了什么". Opened, it shows the before/after length, the
// summary and the groups, folded groups closed until clicked, each item with
// what was done, in the original's time. An opinion can be left on one item or
// on the whole, and is sent with the item it is about.

import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {CutReport} from './CutReport.jsx';

vi.mock('../app/shared.jsx', () => ({API_BASE: '/api', apiFetch: vi.fn()}));
import {apiFetch} from '../app/shared.jsx';

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

const REPORT = {
    before_seconds: 2316, after_seconds: 252, summary: '去掉了会后多录的部分。',
    sections: [
        {title: '去掉的说话', folded: false, items: [
            {ref: 'r1', kind: 'removed_talk', action: 'removed', start: 832.4, end: 2316, seconds: 1483.6, text: '说完拜拜之后多录的', said: '好拜拜'},
        ]},
        {title: '短停顿', folded: true, items: [
            {ref: 'short', kind: 'short_gaps', action: 'removed', seconds: 95, text: '120 处短停顿全部剪掉'},
        ]},
    ],
};
const ok = (body) => Promise.resolve({ok: true, json: () => Promise.resolve(body)});
const listJob = {task_id: 't1', result: {debreath: {survey: {has_report: true}}}};

describe('CutReport', () => {
    it('offers nothing when the task has no report', () => {
        const {container} = render(<CutReport job={{task_id: 't1', result: {debreath: {}}}}/>);
        expect(container.innerHTML).toBe('');
    });

    it('opens the report with folded groups closed', async () => {
        apiFetch.mockImplementation(() => ok({result: {debreath: {survey: {report: REPORT}}}}));
        render(<CutReport job={listJob}/>);
        fireEvent.click(screen.getByText('看看这条视频处理了什么'));
        await waitFor(() => expect(screen.getByText('原片 38:36，剪后 04:12')).toBeTruthy());
        expect(screen.getByText('去掉了会后多录的部分。')).toBeTruthy();
        expect(screen.getByText('原片 13:52 – 38:36')).toBeTruthy();
        expect(screen.queryByText('120 处短停顿全部剪掉')).toBeNull();
        fireEvent.click(screen.getByText('短停顿'));
        expect(screen.getByText('120 处短停顿全部剪掉')).toBeTruthy();
    });

    it('sends an opinion with the item it is about', async () => {
        apiFetch.mockImplementationOnce(() => ok({result: {debreath: {survey: {report: REPORT}}}}));
        apiFetch.mockImplementationOnce(() => ok({feedback: [{ref: 'r1', text: '不该剪'}]}));
        render(<CutReport job={listJob}/>);
        fireEvent.click(screen.getByText('看看这条视频处理了什么'));
        await waitFor(() => screen.getByText('有意见'));
        fireEvent.click(screen.getByText('有意见'));
        fireEvent.change(screen.getByPlaceholderText(/这一处哪里不对/), {target: {value: '不该剪'}});
        fireEvent.click(screen.getAllByText('发送')[0]);
        await waitFor(() => expect(screen.getAllByText('已记下你的意见').length).toBe(1));
        expect(apiFetch.mock.calls[1][0]).toBe('/api/jobs/t1/cut-report/feedback');
        expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({text: '不该剪', ref: 'r1'});
    });
});
