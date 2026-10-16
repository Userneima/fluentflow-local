import {describe, expect, it} from 'vitest';
import {normalizeAiModel, sanitizeSettings} from './settingsModel.js';

// Requirements (owner, 2026-10-16): a model the user picked is the one that
// runs; a user who never picked one follows the default, so a default changed
// later (a new model, a retired name) reaches them without touching settings.
describe('the text model setting', () => {
    it('keeps a model the user picked', () => {
        expect(normalizeAiModel('deepseek', 'deepseek-v4-pro')).toBe('deepseek-v4-pro');
        expect(sanitizeSettings({aiProvider: 'deepseek', aiModel: 'deepseek-v4-pro'}).aiModel).toBe('deepseek-v4-pro');
    });
    it('follows the default when nothing was picked', () => {
        expect(normalizeAiModel('deepseek', '')).toBe('');
        expect(sanitizeSettings({aiProvider: 'openai'}).aiModel).toBe('');
    });
    it('turns a default the page once wrote in, or a retired name, back into following the default', () => {
        for (const stored of ['deepseek-reasoner', 'deepseek-chat', 'deepseek-flash', 'gpt-5.4-mini', 'gpt-6-luna', 'qwen-plus']) {
            expect(sanitizeSettings({aiProvider: 'deepseek', aiModel: stored}).aiModel).toBe('');
        }
    });
});
