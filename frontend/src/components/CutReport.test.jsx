// @vitest-environment jsdom

// Requirements (owner, 2026-10-15):
// - The task card offers the processing report; while items wait for the
//   user it says how many ("有 N 处需要你判断").
// - Only the first group asks: "剪得对" / "确实该保留" is recorded at once and the
//   item leaves the group for a folded "你已确认的", where it can be taken back;
//   "这段不该剪" / "这段应该剪掉" change the video and ask first.
// - Every other item is Claude's account: text, listen, and a quiet
//   "改成保留" / "改成剪掉" that also asks first. A count over many places has
//   no buttons at all.
// - Every change is in the bin at the bottom, undoable after asking.

import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {CutReport} from './CutReport.jsx';

vi.mock('../app/shared.jsx', () => ({API_BASE: '/api', apiFetch: vi.fn()}));
import {apiFetch} from '../app/shared.jsx';

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

const JUDGE = {title: '需要你判断', folded: false, judge: true, items: [
    {ref: 'r1', key: 'k1', kind: 'removed_talk', action: 'removed', start: 832.4, end: 2316, seconds: 1483.6,
        text: '说完拜拜之后多录的', said: '好拜拜', doubt: '去掉的是有人说话的内容，请你确认。'},
]};
const READ = {title: '保留的画面', folded: false, items: [
    {ref: 'g2', key: 'k2', kind: 'long_gap', action: 'kept', start: 24, end: 55, seconds: 31, text: '在演示操作'},
]};
const SHORT = {title: '短停顿', folded: true, items: [
    {ref: 'short', key: 'short', kind: 'short_gaps', action: 'removed', seconds: 95, count: 120, text: '3 秒以下的停顿，全部剪掉。'},
]};
const REPORT = {before_seconds: 2316, after_seconds: 252, summary: '去掉了会后多录的部分。', sections: [JUDGE, READ, SHORT]};
const ok = (body) => Promise.resolve({ok: true, json: () => Promise.resolve(body)});
const listJob = (toJudge = 1) => ({task_id: 't1', result: {debreath: {survey: {has_report: true, to_judge: toJudge}}}});
const opened = (survey) => {
    apiFetch.mockImplementationOnce(() => ok({result: {debreath: {survey}}}));
    render(<CutReport job={listJob()}/>);
    fireEvent.click(screen.getByText('有 1 处需要你判断'));
};

describe('CutReport', () => {
    it('offers nothing when the task has no report', () => {
        const {container} = render(<CutReport job={{task_id: 't1', result: {debreath: {}}}}/>);
        expect(container.innerHTML).toBe('');
    });

    it('says plainly when nothing waits for the user', () => {
        render(<CutReport job={listJob(0)}/>);
        expect(screen.getByText('看看这条视频处理了什么')).toBeTruthy();
    });

    it('asks only in the first group, one line each until opened, with why', async () => {
        opened({report: REPORT});
        await waitFor(() => screen.getByText('原片 38:36，剪后 04:12'));
        expect(screen.queryByText('剪得对')).toBeNull();
        expect(screen.getByText('说完拜拜之后多录的')).toBeTruthy();
        fireEvent.click(screen.getByText('原片 13:52 – 38:36'));
        expect(screen.getByText('请你判断：去掉的是有人说话的内容，请你确认。')).toBeTruthy();
        expect(screen.getAllByText('剪得对').length).toBe(1);
        expect(screen.queryByText('确实该保留')).toBeNull();
        expect(screen.getByText('改成剪掉')).toBeTruthy();
        fireEvent.click(screen.getByText('短停顿'));
        expect(screen.getByText('3 秒以下的停顿，全部剪掉。')).toBeTruthy();
        expect(screen.getAllByText('在原片里听').length).toBe(2);
    });

    it('moves an agreed item out of the group, where it can be taken back', async () => {
        opened({report: REPORT});
        await waitFor(() => screen.getByText('原片 13:52 – 38:36'));
        fireEvent.click(screen.getByText('原片 13:52 – 38:36'));
        apiFetch.mockImplementationOnce(() => ok({verdicts: {k1: {verdict: 'agree'}}}));
        fireEvent.click(screen.getByText('剪得对'));
        await waitFor(() => expect(screen.getByText('需要你判断的都处理完了')).toBeTruthy());
        expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({keys: ['k1'], verdict: 'agree'});
        expect(screen.queryByText('说完拜拜之后多录的')).toBeNull();
        fireEvent.click(screen.getByText('你已确认的'));
        apiFetch.mockImplementationOnce(() => ok({verdicts: {}}));
        fireEvent.click(screen.getByText('撤回'));
        await waitFor(() => expect(screen.queryByText('需要你判断的都处理完了')).toBeNull());
        expect(screen.getByText('说完拜拜之后多录的')).toBeTruthy();
    });

    it('asks before changing the video, then renders again', async () => {
        opened({report: REPORT});
        await waitFor(() => screen.getByText('原片 13:52 – 38:36'));
        fireEvent.click(screen.getByText('原片 13:52 – 38:36'));
        apiFetch.mockImplementation(() => ok({ok: true}));
        fireEvent.click(screen.getByText('这段不该剪'));
        expect(apiFetch.mock.calls.length).toBe(1);
        fireEvent.click(screen.getByText('确定放回去'));
        await waitFor(() => expect(screen.getByText(/按你的改动重新导出剪后视频/)).toBeTruthy());
        expect(apiFetch.mock.calls[1][0]).toBe('/api/jobs/t1/cut-report/r1/flip');
    });

    it('lets Claude\'s account be changed quietly, after asking', async () => {
        opened({report: REPORT});
        await waitFor(() => screen.getByText('改成剪掉'));
        apiFetch.mockImplementation(() => ok({ok: true}));
        fireEvent.click(screen.getByText('改成剪掉'));
        fireEvent.click(screen.getByText('确定剪掉'));
        await waitFor(() => expect(apiFetch.mock.calls[1][0]).toBe('/api/jobs/t1/cut-report/g2/flip'));
    });

    it('lists every change in the bin, each undoable after asking', async () => {
        opened({report: REPORT, user_changes: [
            {start: 60, end: 64, model_said: 'removed', user_said: 'kept', model_reason: '确认声音', at: 'a'},
            {start: 70, end: 75, model_said: 'kept', user_said: 'removed', at: 'b', undone_at: 'c'},
        ]});
        await waitFor(() => screen.getByText('你改过的地方'));
        expect(screen.getByText('原片 01:00 – 01:04 · 原来剪掉，你改成保留')).toBeTruthy();
        expect(screen.getByText('已撤销')).toBeTruthy();
        apiFetch.mockImplementation(() => ok({ok: true}));
        fireEvent.click(screen.getByText('撤销这次改动'));
        fireEvent.click(screen.getByText('确定撤销'));
        await waitFor(() => expect(apiFetch.mock.calls[1][0]).toBe('/api/jobs/t1/cut-report/changes/0/undo'));
    });
});
