// @vitest-environment jsdom

import {describe, expect, it} from 'vitest';
import {render, screen} from '@testing-library/react';
import AgentNotePrompt from './AgentNotePrompt.jsx';
import {agentNotePrompt} from '../lib/noteWriter.js';

describe('AgentNotePrompt', () => {
    const result = {task_id: 'abc123', display_title: '什么是科学'};

    it('hands over a sentence naming the task and both agent tools', () => {
        const prompt = agentNotePrompt(result, 'zh');
        expect(prompt).toContain('abc123');
        expect(prompt).toContain('什么是科学');
        expect(prompt).toContain('get_agent_note_material');
        expect(prompt).toContain('save_agent_note');
    });

    it('shows that sentence with a copy button', () => {
        render(<AgentNotePrompt result={result} lang="zh"/>);
        expect(screen.getByText(agentNotePrompt(result, 'zh'))).toBeTruthy();
        expect(screen.getByRole('button', {name: '复制这句'})).toBeTruthy();
    });

    it('tells an agent that is not connected yet how to connect, with this machine\'s paths and no token', () => {
        const prompt = agentNotePrompt(result, 'zh', {
            repoRoot: String.raw`D:\Github\fluentflow-local`,
            pythonExecutable: String.raw`D:\Github\fluentflow-local\.venv\Scripts\python.exe`,
        });
        expect(prompt).toContain('get_agent_note_material');
        expect(prompt).toContain('MCP 服务，名字叫 fluentflow');
        expect(prompt).toContain(String.raw`D:\Github\fluentflow-local\scripts\fluentflow_mcp_server.py`);
        expect(prompt).toContain(String.raw`D:\Github\fluentflow-local\.venv\Scripts\python.exe`);
        expect(prompt).not.toMatch(/token=|ACCESS_TOKEN|ff-/i);
    });

    it('keeps forward slashes for a Mac path', () => {
        const prompt = agentNotePrompt(result, 'zh', {repoRoot: '/Users/me/fluentflow-local/', pythonExecutable: '/usr/bin/python3'});
        expect(prompt).toContain('/Users/me/fluentflow-local/scripts/fluentflow_mcp_server.py');
    });

    it('says only how to write when the service did not say where it lives', () => {
        expect(agentNotePrompt(result, 'zh', {})).not.toContain('MCP 服务');
    });
});
