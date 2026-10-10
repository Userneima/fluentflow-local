// @vitest-environment jsdom

// 「我的笔记风格」. What the user should get:
// 1. It says which style Claude writes with now.
// 2. Edits not read yet can be turned into candidate rules with one button.
// 3. A change is compared before it applies: the compare button needs a rule
//    picked and a recording, and sends both.
// 4. While the comparison is written there is nothing to apply; once it is
//    ready both versions are shown, and only "用新写法" applies the change.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';

const api = {
    read: vi.fn(),
    previewTasks: vi.fn(),
    fromEdits: vi.fn(),
    fromExamples: vi.fn(),
    dismiss: vi.fn(),
    propose: vi.fn(),
    apply: vi.fn(),
    discard: vi.fn(),
};
vi.mock('../app/shared.jsx', () => ({useApi: () => ({noteStyle: api})}));

const {NoteStylePanel} = await import('./NoteStylePanel.jsx');

const SKILL = {text: '---\nname: x\n---\n规则', version: 'v1', is_default: true};
const state = (extra = {}) => ({skill: SKILL, candidates: [], unread_edits: 0, proposal: null, history: [], ...extra});

afterEach(() => {
    cleanup();
    Object.values(api).forEach((fn) => fn.mockReset());
});

const setup = (first) => {
    api.read.mockResolvedValue(first);
    api.previewTasks.mockResolvedValue({tasks: [{task_id: 't1', title: '线性代数第一讲'}]});
    render(<NoteStylePanel/>);
};

describe('NoteStylePanel', () => {
    it('says which style is in use', async () => {
        setup(state());
        expect(await screen.findByText('FluentFlow 默认写法')).toBeTruthy();
    });

    it('turns unread edits into candidates with one button', async () => {
        setup(state({unread_edits: 2}));
        const button = await screen.findByText('从我改过的 2 份笔记里找规则');
        api.fromEdits.mockResolvedValue({});
        api.read.mockResolvedValue(state({
            candidates: [{id: 'c1', rule: '术语在正文第一次出现时用括号解释', evidence: '删掉了词汇表'}],
        }));
        fireEvent.click(button);
        expect(await screen.findByText('术语在正文第一次出现时用括号解释')).toBeTruthy();
        expect(api.fromEdits).toHaveBeenCalledTimes(1);
    });

    it('compares a picked rule on the chosen recording before anything changes', async () => {
        setup(state({candidates: [{id: 'c1', rule: '结尾回顾直接列条目'}]}));
        const compare = await screen.findByText(/用选中的 0 条规则写一份对比/);
        expect(compare.closest('button').disabled).toBe(true);
        await waitFor(() => expect(screen.getByDisplayValue('线性代数第一讲')).toBeTruthy());
        fireEvent.click(screen.getByRole('checkbox'));
        api.propose.mockResolvedValue({});
        fireEvent.click(screen.getByText(/用选中的 1 条规则写一份对比/));
        await waitFor(() => expect(api.propose).toHaveBeenCalledWith({task_id: 't1', candidate_ids: ['c1']}));
        expect(api.apply).not.toHaveBeenCalled();
    });

    it('offers nothing to apply while the comparison is written', async () => {
        setup(state({proposal: {status: 'running', rules: ['r']}}));
        expect(await screen.findByText(/写好之前写法不会变/)).toBeTruthy();
        expect(screen.queryByText('用新写法')).toBeNull();
    });

    it('shows both versions and applies only on 用新写法', async () => {
        setup(state({proposal: {
            status: 'ready', rules: ['结尾回顾直接列条目'],
            old: {markdown: '# 旧的笔记'}, new: {markdown: '# 新的笔记'},
        }}));
        expect(await screen.findByText('旧的笔记')).toBeTruthy();
        expect(screen.getByText('新的笔记')).toBeTruthy();
        api.apply.mockResolvedValue({});
        fireEvent.click(screen.getByText('用新写法'));
        await waitFor(() => expect(api.apply).toHaveBeenCalledTimes(1));
        expect(api.discard).not.toHaveBeenCalled();
    });
});
