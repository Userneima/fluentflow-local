import {describe, expect, it} from 'vitest';
import {normalizeAiModel, sanitizeSettings} from './settingsModel.js';

describe('the DeepSeek model the user picks is the one that runs', () => {
    it('keeps deepseek-chat instead of upgrading it to the reasoner', () => {
        expect(normalizeAiModel('deepseek', 'deepseek-chat')).toBe('deepseek-chat');
        expect(sanitizeSettings({aiProvider: 'deepseek', aiModel: 'deepseek-chat'}).aiModel).toBe('deepseek-chat');
    });
    it('still falls back to the default when nothing is chosen', () => {
        expect(normalizeAiModel('deepseek', '')).toBe('deepseek-reasoner');
    });
});
