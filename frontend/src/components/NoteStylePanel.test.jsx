// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';

const api = {read: vi.fn(), restore: vi.fn()};
vi.mock('../app/shared.jsx', () => ({useApi: () => ({noteStyle: api})}));
const {NoteStylePanel} = await import('./NoteStylePanel.jsx');
const SKILL = {text: '当前写法全文', version: 'v1', is_default: true};
const state = (extra = {}) => ({skill: SKILL, history: [], rules_log: [], ...extra});
afterEach(() => { cleanup(); Object.values(api).forEach((fn) => fn.mockReset()); });
const setup = (first = state()) => { api.read.mockResolvedValue(first); return render(<NoteStylePanel/>); };

describe('personal note preferences', () => {
    it('only offers viewing and undo, even if an old response carries candidates', async () => {
        setup(state({unread_edits: 3, candidates: [{rule: 'old'}], proposal: {status: 'ready'}}));
        expect(await screen.findByText('FluentFlow 默认写法')).toBeTruthy();
        expect(screen.queryByText(/找规则|满意的笔记起步|用新写法|写一份对比/)).toBeNull();
        fireEvent.click(screen.getByText('查看写法全文'));
        expect(screen.getByText('当前写法全文')).toBeTruthy();
        expect(api.read).toHaveBeenCalledTimes(1);
    });
    it('refreshes the current rules when returning from the agent without hiding cached content', async () => {
        setup();
        await screen.findByText('FluentFlow 默认写法');
        let finish;
        api.read.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
        fireEvent.focus(window);
        expect(screen.getByText('FluentFlow 默认写法')).toBeTruthy();
        expect(screen.queryByText('正在读取笔记写法…')).toBeNull();
        finish(state({skill: {...SKILL, is_default: false, version: 'v2'}}));
        await screen.findByText('你自己的写法');
    });
    it('keeps historical changes labelled as history after a restore', async () => {
        setup(state({rules_log: [{id: 'r1', rule: '段落为主', evidence: '检查了一段样例', feedback: [{id: 'f1', problem: '不想要太多表格'}]}]}));
        expect(await screen.findByText('修改记录和来源')).toBeTruthy();
        fireEvent.click(screen.getByText('修改记录和来源'));
        expect(screen.getByText(/也保留已撤回/)).toBeTruthy();
        expect(screen.getByText('来自你的反馈：不想要太多表格')).toBeTruthy();
    });
    it('confirms an undo and sends the viewed version without choosing a recording', async () => {
        setup(state({history: [{name: 'old.md', saved_at: '2026-10-26 14:00'}]}));
        await screen.findByText('FluentFlow 默认写法');
        fireEvent.change(screen.getByLabelText('撤回到'), {target: {value: 'old.md'}});
        fireEvent.click(screen.getByText('撤回修改'));
        expect(api.restore).not.toHaveBeenCalled();
        fireEvent.click(screen.getByText('取消'));
        expect(api.restore).not.toHaveBeenCalled();
        fireEvent.click(screen.getByText('撤回修改'));
        api.restore.mockResolvedValue(state({skill: {...SKILL, version: 'v2', is_default: false}}));
        fireEvent.click(screen.getByText('确认撤回'));
        await screen.findByText('你自己的写法');
        expect(api.restore).toHaveBeenCalledWith({name: 'old.md', base_version: 'v1'});
    });
    it('requires a new confirmation against the latest version after a conflict', async () => {
        setup(state({skill: {...SKILL, is_default: false}}));
        await screen.findByText('你自己的写法');
        fireEvent.click(screen.getByText('恢复默认写法'));
        api.restore.mockRejectedValueOnce(new Error('笔记写法已在别处改过，请重新读取后再修改。'));
        api.read.mockResolvedValue(state({skill: {...SKILL, is_default: false, version: 'v2'}}));
        fireEvent.click(screen.getByText('确认撤回'));
        await screen.findByRole('alert');
        expect(screen.queryByText('确认撤回')).toBeNull();
        api.restore.mockResolvedValue(state());
        fireEvent.click(screen.getByText('恢复默认写法'));
        fireEvent.click(screen.getByText('确认撤回'));
        await waitFor(() => expect(api.restore).toHaveBeenLastCalledWith({use_default: true, base_version: 'v2'}));
    });
    it('can retry a failed read without exposing an HTTP code', async () => {
        api.read.mockRejectedValueOnce(new Error('HTTP 500')).mockResolvedValue(state());
        render(<NoteStylePanel/>);
        await screen.findByRole('alert');
        expect(screen.queryByText('HTTP 500')).toBeNull();
        fireEvent.click(screen.getByText('重新读取'));
        await screen.findByText('FluentFlow 默认写法');
    });
});
