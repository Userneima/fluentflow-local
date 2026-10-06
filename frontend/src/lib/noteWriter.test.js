import {describe, expect, it} from 'vitest';
import {noteWriterBatchClause, noteWriterSentence, resolveNoteWriter} from './noteWriter.js';

// Requirement: every screen names the same writer, and it is the one the
// backend will actually use (local_intake_flow.auto_note_will_run).

describe('who writes the note', () => {
    it('is Claude on the subscription when the build writes its own note and the channel is open', () => {
        const writer = resolveNoteWriter({
            runtimeConfig: {writesItsOwnNote: true},
            credentialStatus: {visual_note_available: true},
            settings: {aiProvider: 'deepseek'},
        });
        expect(writer.kind).toBe('claude');
        expect(noteWriterSentence(writer, 'zh')).toBe('当前写笔记的是 Claude（本机已登录的订阅）。');
        expect(noteWriterBatchClause(writer, 'zh')).toContain('Claude 订阅额度');
    });

    it('is Claude on the user\'s own key when one is configured', () => {
        const writer = resolveNoteWriter({
            runtimeConfig: {writesItsOwnNote: true},
            credentialStatus: {visual_note_available: true, anthropic_api_key_configured: true},
        });
        expect(noteWriterSentence(writer, 'zh')).toBe('当前写笔记的是 Claude（你的 Anthropic API Key）。');
        expect(noteWriterBatchClause(writer, 'zh')).not.toContain('订阅');
    });

    it('falls back to the selected text model when the Claude channel is closed', () => {
        const writer = resolveNoteWriter({
            runtimeConfig: {writesItsOwnNote: true},
            credentialStatus: {visual_note_available: false, deepseek_api_key_configured: true},
            settings: {aiProvider: 'deepseek', aiModel: 'deepseek-reasoner'},
        });
        expect(writer.kind).toBe('text_model');
        expect(noteWriterSentence(writer, 'zh')).toBe('当前写笔记的是 DeepSeek（deepseek-reasoner）。');
        expect(noteWriterBatchClause(writer, 'zh')).not.toContain('Claude');
    });

    it('checks the key of the provider that is selected, not any key', () => {
        const writer = resolveNoteWriter({
            runtimeConfig: {writesItsOwnNote: false},
            credentialStatus: {deepseek_api_key_configured: true},
            settings: {aiProvider: 'openai'},
        });
        expect(writer.kind).toBe('none');
        expect(noteWriterSentence(writer, 'zh')).toContain('还没有填写模型 Key');
    });

    it('says transcript-only when the user switched the note off', () => {
        const writer = resolveNoteWriter({
            runtimeConfig: {writesItsOwnNote: false},
            credentialStatus: {deepseek_api_key_configured: true},
            settings: {aiProvider: 'deepseek', skipAiSummary: true},
        });
        expect(writer.kind).toBe('skipped');
        expect(noteWriterBatchClause(writer, 'zh')).toContain('仅转录');
    });
});

describe('an expired Claude login is said before anything is submitted', () => {
    const runtimeConfig = {writesItsOwnNote: true};
    it('names the login and how to restore it, then who writes the note instead', () => {
        const writer = resolveNoteWriter({
            runtimeConfig,
            credentialStatus: {visual_note_available: false, visual_note_login_expired: true, deepseek_api_key_configured: true},
            settings: {aiProvider: 'deepseek', aiModel: 'deepseek-chat'},
        });
        const sentence = noteWriterSentence(writer, 'zh');
        expect(sentence).toContain('登录已过期');
        expect(sentence).toContain('claude');
        expect(sentence).toContain('DeepSeek');
    });
    it('says nothing about the login when it is fine', () => {
        const writer = resolveNoteWriter({
            runtimeConfig,
            credentialStatus: {visual_note_available: true, visual_note_login_expired: false},
            settings: {},
        });
        expect(noteWriterSentence(writer, 'zh')).not.toContain('过期');
    });
});
