import {describe, expect, it} from 'vitest';
import {noteExplanation, notePromptScopeSentence, noteWriterBatchClause, noteWriterSentence, resolveNoteWriter} from './noteWriter.js';

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
            settings: {aiProvider: 'deepseek', aiModel: 'deepseek-flash'},
        });
        expect(writer.kind).toBe('text_model');
        expect(noteWriterSentence(writer, 'zh')).toMatch(/^当前写笔记的是 DeepSeek（deepseek-flash）。由它写的笔记只根据说出来的话写/);
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

// Requirement: the prompt templates and the note mode only shape the text-model
// note. When Claude writes from the frames, the person changing them is told so
// before expecting a different note; for a text-model writer nothing is added.
describe('what the prompt button says about who uses the prompt', () => {
    it('says Claude does not read the prompt and points to the text-model choice in 重写笔记', () => {
        const writer = resolveNoteWriter({
            runtimeConfig: {writesItsOwnNote: true},
            credentialStatus: {visual_note_available: true},
        });
        const zh = notePromptScopeSentence(writer, 'zh');
        expect(zh).toMatch(/Claude 结合画面写笔记/);
        expect(zh).toMatch(/重写笔记/);
        expect(zh).toMatch(/改用文本模型按文字重写/);
        expect(notePromptScopeSentence(writer, 'en')).toMatch(/rewrite from the text with the text model/);
    });

    it('says nothing extra when a text model writes the note, since the prompt applies directly', () => {
        const writer = resolveNoteWriter({
            runtimeConfig: {writesItsOwnNote: false},
            credentialStatus: {deepseek_api_key_configured: true},
            settings: {aiProvider: 'deepseek'},
        });
        expect(writer.kind).toBe('text_model');
        expect(notePromptScopeSentence(writer, 'zh')).toBe('');
    });
});


describe('a text model as the writer', () => {
    it('says up front that its notes leave the picture out and that cutting is not affected', () => {
        const sentence = noteWriterSentence({kind: 'text_model', provider: 'deepseek', model: ''}, 'zh');
        expect(sentence).toContain('不看画面');
        expect(sentence).toContain('不配截图');
        expect(sentence).toContain('剪辑不受影响');
        expect(noteWriterSentence({kind: 'claude', viaApiKey: false}, 'zh')).not.toContain('不看画面');
    });
});

describe('noteExplanation', () => {
    it('says a text-model stand-in was written from the transcript only, why, and how to get the frame note', () => {
        const explained = noteExplanation({
            task_id: 't1',
            display_title: '面试',
            summary_written_from: 'text_fallback',
            note_written_by: 'text_fallback',
            note_text_model: 'deepseek/deepseek-flash',
            note_fallback_reason: '还没有配置 Anthropic 的 API Key。',
        }, 'zh');
        expect(explained.attention).toBe(true);
        expect(explained.rows[0].value).toBe('文本模型（deepseek/deepseek-flash）');
        expect(explained.rows[1].value).toMatch(/只有转录稿/);
        // Said as what happened, not as instructions for a key (owner, 2026-10-18).
        expect(explained.why).toMatch(/没有配 Claude/);
        expect(explained.why).not.toMatch(/填入/);
        expect(explained.agentPrompt).toMatch(/get_agent_note_material/);
        expect(explained.keyOption).toMatch(/Anthropic API Key/);
    });

    it('names Claude, the frames cited and the version for a frame note, and asks nothing of the reader', () => {
        const explained = noteExplanation({
            summary_written_from: 'debreath_media_note',
            visual_note: {channel_label: '本机已登录的 Claude 订阅', model: 'opus', frames_cited: [{}, {}, {}]},
        }, 'zh');
        expect(explained.attention).toBe(false);
        expect(explained.rows.map((row) => row.value)).toEqual([
            '本机已登录的 Claude 订阅（opus）', '转录稿和画面，引用了 3 张截图', '剪掉空白后的版本',
        ]);
        expect(explained.agentPrompt).toBe('');
        expect(explained.keyOption).toBe('');
    });

    it('names the agent for a note the agent saved', () => {
        const explained = noteExplanation({summary_written_from: 'agent_note', visual_note: {channel: 'agent', model: 'agent', media_source: {}}}, 'zh');
        expect(explained.rows[0].value).toBe('你的 agent');
    });
});

describe('noteExplanation of what the backend records', () => {
    it('names the agent from summary_written_from alone', () => {
        expect(noteExplanation({summary_written_from: 'agent_note'}, 'zh').rows[0].value).toBe('你的 agent');
    });
});

describe('noteExplanation of other reasons', () => {
    it('shows an expired login as it was given', () => {
        const explained = noteExplanation({summary_written_from: 'text_fallback', note_fallback_reason: '本机 Claude 的登录已过期。'}, 'zh');
        expect(explained.why).toBe('本机 Claude 的登录已过期。');
    });
});
