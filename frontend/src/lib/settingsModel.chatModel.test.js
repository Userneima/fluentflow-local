import {describe, expect, it} from 'vitest';
import {normalizeAiModel, sanitizeSettings} from './settingsModel.js';

describe('the DeepSeek model the user picks is the one that runs', () => {
    it('keeps a current model the user picked', () => {
        expect(normalizeAiModel('deepseek', 'deepseek-v4-pro')).toBe('deepseek-v4-pro');
        expect(sanitizeSettings({aiProvider: 'deepseek', aiModel: 'deepseek-v4-pro'}).aiModel).toBe('deepseek-v4-pro');
    });
    it('falls back to the current Flash model when nothing is chosen', () => {
        expect(normalizeAiModel('deepseek', '')).toBe('deepseek-flash');
    });
    // DeepSeek retired deepseek-chat and deepseek-reasoner (change log,
    // 2026-04-24) and only routes the V4 Flash names for now (2026-09-10).
    it('moves a retired name kept from before onto the current Flash model', () => {
        for (const retired of ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4-flash']) {
            expect(sanitizeSettings({aiProvider: 'deepseek', aiModel: retired}).aiModel).toBe('deepseek-flash');
        }
    });
});
