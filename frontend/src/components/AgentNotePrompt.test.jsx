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
});
