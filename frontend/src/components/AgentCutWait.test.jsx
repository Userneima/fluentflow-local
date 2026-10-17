// @vitest-environment jsdom

// Requirements (owner, 2026-10-17):
// - A task waiting for the user's agent to judge the cut says so on the page,
//   and offers to let FluentFlow judge instead.
// - Pressing it asks the service once and says FluentFlow is judging now.
// - After FluentFlow judged because the agent did not answer, the page says why.
// - Any other task shows nothing here.

import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import AgentCutWait from './AgentCutWait.jsx';

vi.mock('../app/shared.jsx', () => ({API_BASE: '/api', apiFetch: vi.fn()}));
import {apiFetch} from '../app/shared.jsx';

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

const job = (agentCut) => ({task_id: 't1', result: {agent_cut: agentCut}});

describe('AgentCutWait', () => {
    it('says the task waits for the agent and lets FluentFlow judge', async () => {
        apiFetch.mockResolvedValueOnce({ok: true, json: () => Promise.resolve({ok: true})});
        render(<AgentCutWait job={job({status: 'waiting'})}/>);
        expect(screen.getByText(/在等你的 agent 判断剪哪里/)).toBeTruthy();

        fireEvent.click(screen.getByRole('button', {name: '让 FluentFlow 自己判断'}));

        await waitFor(() => expect(screen.getByText(/马上由 FluentFlow 自己判断/)).toBeTruthy());
        expect(apiFetch).toHaveBeenCalledWith('/api/jobs/t1/agent-cut/self-judge', {method: 'POST'});
        expect(screen.queryByRole('button')).toBeNull();
    });

    it('shows the refusal when the task stopped waiting meanwhile', async () => {
        apiFetch.mockResolvedValueOnce({ok: false, status: 409, json: () => Promise.resolve({detail: '这个任务已经不在等 agent 判断了。'})});
        render(<AgentCutWait job={job({status: 'waiting'})}/>);
        fireEvent.click(screen.getByRole('button', {name: '让 FluentFlow 自己判断'}));
        await waitFor(() => expect(screen.getByText('这个任务已经不在等 agent 判断了。')).toBeTruthy());
    });

    it('says why FluentFlow judged when the agent did not answer', () => {
        render(<AgentCutWait job={job({status: 'fallback', fallback_reason: 'agent 的对话已经关掉了'})}/>);
        expect(screen.getByText(/没有等到 agent 的剪辑判断（agent 的对话已经关掉了）/)).toBeTruthy();
    });

    it('shows nothing for a task the agent judged or that never waited', () => {
        const {container: decided} = render(<AgentCutWait job={job({status: 'done'})}/>);
        expect(decided.textContent).toBe('');
        const {container: never} = render(<AgentCutWait job={{task_id: 't2', result: {}}}/>);
        expect(never.textContent).toBe('');
    });
});
