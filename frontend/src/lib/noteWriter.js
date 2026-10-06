import {providerDisplayName} from './format.js';

// Who writes the note for the next job, in one place.
//
// Three screens used to answer this differently: the start page warned about
// text-model keys, its confirm dialogs promised "one call of your Claude
// allowance", and the settings page called the Anthropic key optional. The
// backend has one rule (local_intake_flow.auto_note_will_run): when the build
// writes its own note and a Claude channel is available, Claude writes it from
// the frames; otherwise the pipeline's text note runs with whichever provider
// is selected, and with no key there is no note at all.
//
//   runtimeConfig    — from /runtime-config (writesItsOwnNote)
//   credentialStatus — from /credentials/status (visual_note_available,
//                      *_api_key_configured)
//   settings         — stored settings (aiProvider, aiModel, skipAiSummary)
export const resolveNoteWriter = ({runtimeConfig = {}, credentialStatus = null, settings = {}} = {}) => {
    const status = credentialStatus || {};
    const provider = String(settings?.aiProvider || 'deepseek');
    const textKeyConfigured = provider === 'openai'
        ? !!status.openai_api_key_configured
        : provider === 'qwen'
            ? !!(status.dashscope_api_key_configured || status.qwen_api_key_configured)
            : !!status.deepseek_api_key_configured;
    if (runtimeConfig?.writesItsOwnNote && status.visual_note_available) {
        return {
            kind: 'claude',
            provider: 'anthropic',
            viaApiKey: !!status.anthropic_api_key_configured,
            model: '',
        };
    }
    if (settings?.skipAiSummary) {
        return {kind: 'skipped', provider: '', viaApiKey: false, model: ''};
    }
    if (textKeyConfigured) {
        return {kind: 'text_model', provider, viaApiKey: true, model: String(settings?.aiModel || '')};
    }
    return {kind: 'none', provider, viaApiKey: false, model: ''};
};

// The short name of the writer, for sentences like "…并由 X 写笔记".
export const noteWriterLabel = (writer, lang = 'zh') => {
    const zh = lang === 'zh';
    if (!writer || writer.kind === 'none') return zh ? '还没有能写笔记的模型' : 'no model that can write a note';
    if (writer.kind === 'skipped') return zh ? '仅转录，不写笔记' : 'transcript only, no note';
    if (writer.kind === 'claude') {
        return writer.viaApiKey
            ? (zh ? 'Claude（你的 Anthropic API Key）' : 'Claude (your Anthropic API key)')
            : (zh ? 'Claude（本机已登录的订阅）' : 'Claude (the subscription signed in on this machine)');
    }
    const name = providerDisplayName(writer.provider, lang);
    return writer.model ? `${name}（${writer.model}）` : name;
};

// The one sentence every screen shows: 当前写笔记的是 X.
export const noteWriterSentence = (writer, lang = 'zh') => {
    const zh = lang === 'zh';
    if (!writer || writer.kind === 'none') {
        return zh
            ? '还没有填写模型 Key。现在处理只会得到转录稿和字幕，没有笔记。'
            : 'No model key yet. Jobs will produce a transcript and subtitles, but no note.';
    }
    if (writer.kind === 'skipped') {
        return zh ? '当前是仅转录模式，不写笔记。' : 'Transcript-only mode is on; no note is written.';
    }
    return zh ? `当前写笔记的是 ${noteWriterLabel(writer, lang)}。` : `Notes are currently written by ${noteWriterLabel(writer, lang)}.`;
};

// What one queued recording costs, said in the confirm dialogs before a batch.
export const noteWriterBatchClause = (writer, lang = 'zh') => {
    const zh = lang === 'zh';
    if (!writer || writer.kind === 'none') {
        return zh ? '（还没有模型 Key，这次只会得到转录稿，没有笔记）' : ' (no model key yet, so this produces transcripts but no notes)';
    }
    if (writer.kind === 'skipped') {
        return zh ? '（仅转录模式，不写笔记）' : ' (transcript-only mode, no notes)';
    }
    if (writer.kind === 'claude' && !writer.viaApiKey) {
        return zh ? '，并各用一次 Claude 订阅额度写笔记' : ', and each one spends one call of the Claude subscription on a note';
    }
    return zh ? `，并由 ${noteWriterLabel(writer, lang)} 各写一份笔记` : `, and ${noteWriterLabel(writer, lang)} writes a note for each`;
};
