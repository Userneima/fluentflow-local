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
    const writer = resolveWriterKind({runtimeConfig, credentialStatus, settings});
    // The frame note runs on this machine's Claude login and that login has
    // expired: the backend has already fallen back to the writer above, and the
    // user is the only one who can log in again.
    return {...writer, claudeLoginExpired: !!(credentialStatus || {}).visual_note_login_expired};
};

const resolveWriterKind = ({runtimeConfig = {}, credentialStatus = null, settings = {}} = {}) => {
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

// Said before everything else when the Claude login has expired.
export const claudeLoginSentence = (lang = 'zh') => (lang === 'zh'
    ? '本机 Claude 的登录已过期，结合画面的笔记暂时写不了：打开终端运行 claude 重新登录即可恢复。'
    : 'Claude on this machine is logged out, so notes that read the frames cannot be written: run claude in a terminal to log in again.');

// The one sentence every screen shows: 当前写笔记的是 X.
export const noteWriterSentence = (writer, lang = 'zh') => {
    const base = baseWriterSentence(writer, lang);
    return writer?.claudeLoginExpired ? `${claudeLoginSentence(lang)}${lang === 'zh' ? '' : ' '}${base}` : base;
};

const baseWriterSentence = (writer, lang = 'zh') => {
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

// Said beside "更换提示词" when Claude writes the note from the frames. The
// prompt templates and the note mode feed the text-model note only; the frame
// note has its own instructions, so changing them does nothing to it until the
// note is rewritten with a text model. Empty for every other writer.
export const notePromptScopeSentence = (writer, lang = 'zh') => {
    if (writer?.kind !== 'claude') return '';
    return lang === 'zh'
        ? '当前由 Claude 结合画面写笔记，提示词和笔记模式只在改用文本模型重生时生效。'
        : 'Claude is writing notes from the video frames, so the prompt and note mode only take effect when a text model rewrites the note.';
};
