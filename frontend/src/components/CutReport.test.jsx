// @vitest-environment jsdom

// Requirements (owner, 2026-10-15): a task whose cut has a processing report
// offers "看看这条视频处理了什么". Opened, it shows the before/after length, the
// summary and the groups, folded groups closed until clicked, each item with
// what was done, in the original's time. Agreeing ("剪得对" / "确实该保留", or a
// whole group) is recorded at once and can be taken back; changing the video
// ("这段不该剪" / "这段应该剪掉") asks first. Every change is in the bin, undoable.

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
            {ref: 'r1', key: 'k1', kind: 'removed_talk', action: 'removed', start: 832.4, end: 2316, seconds: 1483.6, text: '说完拜拜之后多录的', said: '好拜拜'},
        ]},
        {title: '短停顿', folded: true, items: [
            {ref: 'short', key: 'short', kind: 'short_gaps', action: 'removed', seconds: 95, text: '120 处短停顿全部剪掉'},
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
        // A count over many places has nothing to listen to or judge one by one.
        expect(screen.getAllByText('剪得对').length).toBe(1);
        expect(screen.getAllByText('这一组都没问题').length).toBe(1);
    });

    it('agrees with an item at once, and takes it back', async () => {
        apiFetch.mockImplementationOnce(() => ok({result: {debreath: {survey: {report: REPORT}}}}));
        apiFetch.mockImplementationOnce(() => ok({verdicts: {k1: {verdict: 'agree'}}}));
        apiFetch.mockImplementationOnce(() => ok({verdicts: {}}));
        render(<CutReport job={listJob}/>);
        fireEvent.click(screen.getByText('看看这条视频处理了什么'));
        await waitFor(() => screen.getByText('剪得对'));
        fireEvent.click(screen.getByText('剪得对'));
        await waitFor(() => expect(screen.getByText('已确认剪得对')).toBeTruthy());
        expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({keys: ['k1'], verdict: 'agree'});
        expect(screen.getByText('已确认 1 / 1 条')).toBeTruthy();
        fireEvent.click(screen.getAllByText('撤回')[0]);
        await waitFor(() => expect(screen.getByText('剪得对')).toBeTruthy());
    });

    it('agrees with a whole group in one click', async () => {
        apiFetch.mockImplementationOnce(() => ok({result: {debreath: {survey: {report: REPORT}}}}));
        apiFetch.mockImplementationOnce(() => ok({verdicts: {k1: {verdict: 'agree'}}}));
        render(<CutReport job={listJob}/>);
        fireEvent.click(screen.getByText('看看这条视频处理了什么'));
        await waitFor(() => screen.getAllByText('这一组都没问题'));
        fireEvent.click(screen.getAllByText('这一组都没问题')[0]);
        await waitFor(() => expect(screen.getByText('这一组已确认')).toBeTruthy());
        expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({keys: ['k1'], verdict: 'agree'});
    });

    it('asks before changing the video, then renders again', async () => {
        apiFetch.mockImplementationOnce(() => ok({result: {debreath: {survey: {report: REPORT}}}}));
        apiFetch.mockImplementation(() => ok({ok: true}));
        render(<CutReport job={listJob}/>);
        fireEvent.click(screen.getByText('看看这条视频处理了什么'));
        await waitFor(() => screen.getByText('这段不该剪'));
        fireEvent.click(screen.getByText('这段不该剪'));
        expect(apiFetch.mock.calls.length).toBe(1);
        fireEvent.click(screen.getByText('确定放回去'));
        await waitFor(() => expect(screen.getByText(/按你的改动重新导出剪后视频/)).toBeTruthy());
        expect(apiFetch.mock.calls[1][0]).toBe('/api/jobs/t1/cut-report/r1/flip');
    });

    it('lists every change in the bin, each undoable after asking', async () => {
        const survey = {report: REPORT, user_changes: [
            {start: 60, end: 64, model_said: 'removed', user_said: 'kept', model_reason: '确认声音', at: 'a'},
            {start: 70, end: 75, model_said: 'kept', user_said: 'removed', at: 'b', undone_at: 'c'},
        ]};
        apiFetch.mockImplementationOnce(() => ok({result: {debreath: {survey}}}));
        apiFetch.mockImplementation(() => ok({ok: true}));
        render(<CutReport job={listJob}/>);
        fireEvent.click(screen.getByText('看看这条视频处理了什么'));
        await waitFor(() => screen.getByText('你改过的地方'));
        expect(screen.getByText('原片 01:00 – 01:04 · 原来剪掉，你改成保留')).toBeTruthy();
        expect(screen.getByText('已撤销')).toBeTruthy();
        fireEvent.click(screen.getByText('撤销这次改动'));
        fireEvent.click(screen.getByText('确定撤销'));
        await waitFor(() => expect(apiFetch.mock.calls[1][0]).toBe('/api/jobs/t1/cut-report/changes/0/undo'));
    });

    it('offers to cut what was kept', async () => {
        const kept = {...REPORT, sections: [{title: '保留的画面', folded: false, items: [
            {ref: 'g2', key: 'k3', kind: 'long_gap', action: 'kept', start: 24, end: 55, seconds: 31, text: '在演示操作'},
        ]}]};
        apiFetch.mockImplementationOnce(() => ok({result: {debreath: {survey: {report: kept}}}}));
        render(<CutReport job={listJob}/>);
        fireEvent.click(screen.getByText('看看这条视频处理了什么'));
        await waitFor(() => expect(screen.getByText('这段应该剪掉')).toBeTruthy());
        expect(screen.getByText('确实该保留')).toBeTruthy();
    });
});
