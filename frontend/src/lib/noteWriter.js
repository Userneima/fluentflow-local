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

// Whether the selected text model (DeepSeek, OpenAI or Qwen) has its key. The
// text model is the only writer for anything that does not go through the
// frame note: a subtitle or transcript file, and the fallback note.
export const textModelKeyConfigured = ({credentialStatus = null, settings = {}} = {}) => {
    const status = credentialStatus || {};
    const provider = String(settings?.aiProvider || 'deepseek');
    return provider === 'openai'
        ? !!status.openai_api_key_configured
        : provider === 'qwen'
            ? !!(status.dashscope_api_key_configured || status.qwen_api_key_configured)
            : !!status.deepseek_api_key_configured;
};

const resolveWriterKind = ({runtimeConfig = {}, credentialStatus = null, settings = {}} = {}) => {
    const status = credentialStatus || {};
    const provider = String(settings?.aiProvider || 'deepseek');
    const textKeyConfigured = textModelKeyConfigured({credentialStatus, settings});
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
            ? '还没有填写模型 Key。转录照常进行，笔记可以交给你的 agent（Claude Code、Codex 等）来写。'
            : 'No model key yet. Transcription works as usual, and your agent (Claude Code, Codex, ...) can write the note.';
    }
    if (writer.kind === 'skipped') {
        return zh ? '当前是仅转录模式，不写笔记。' : 'Transcript-only mode is on; no note is written.';
    }
    const sentence = zh ? `当前写笔记的是 ${noteWriterLabel(writer, lang)}。` : `Notes are currently written by ${noteWriterLabel(writer, lang)}.`;
    return writer.kind === 'text_model' ? `${sentence}${zh ? '' : ' '}${textModelLimitSentence(lang)}` : sentence;
};

// Said wherever a text model is the writer (owner, 2026-10-16): the note it
// writes is sent the transcript only, so the user knows before the first note
// what it will lack. The
// cut does not depend on it: deciding what to cut reads the transcript and the
// program's own measure of whether the picture moved, never the frames.
export const textModelLimitSentence = (lang = 'zh') => (lang === 'zh'
    ? '由它写的笔记只根据说出来的话写，不看画面、不配截图，画面上没说出口的内容（幻灯片上的字、代码、表格）不会进笔记。剪辑不受影响。'
    : 'Its notes are written from what was said only, without looking at the picture or adding screenshots; what was only shown on screen (slide text, code, tables) is left out. Cutting is not affected.');

// What one queued recording costs, said in the confirm dialogs before a batch.
export const noteWriterBatchClause = (writer, lang = 'zh') => {
    const zh = lang === 'zh';
    if (!writer || writer.kind === 'none') {
        return zh ? '（还没有模型 Key，这次只出转录稿，笔记可以交给你的 agent 来写）' : ' (no model key yet, so this produces transcripts; your agent can write the notes)';
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
        ? '当前由 Claude 结合画面写笔记，不读提示词和笔记模式。要按提示词写，点「重生笔记」，选「改用文本模型按文字重写」。'
        : 'Claude is writing notes from the video frames and does not read the prompt or note mode. To use the prompt, click Regenerate note and choose to rewrite from the text with the text model.';
};

// Whether this task's note was written by the text model because the frame
// note could not run (local_intake_flow._write_text_note_instead). The person
// expected a note with screenshots and has to be told why there are none.
export const noteWasTextFallback = (result) => (
    result?.note_written_by === 'text_fallback' || result?.summary_written_from === 'text_fallback'
);

// What the editor's 笔记说明 shows: who wrote this note, from what, and — when
// it is not the note the person expected — why, and how to get that one. Was a
// paragraph above the note that took a third of a small window (owner,
// 2026-10-18); now it is asked for.
//
// {attention, rows: [{label, value}], why, fixes: [string], agentPrompt}
export const noteExplanation = (result, lang = 'zh', connection = {}) => {
    const zh = lang === 'zh';
    const from = String(result?.summary_written_from || '');
    const visual = result?.visual_note && typeof result.visual_note === 'object' ? result.visual_note : {};
    const fallback = noteWasTextFallback(result);
    const textModel = String(result?.note_text_model || '').trim();
    const cited = Array.isArray(visual.frames_cited) ? visual.frames_cited.length : 0;
    const byAgent = from === 'agent_note' || visual.channel === 'agent';
    const byFrames = from === 'debreath_media_note' || from === 'source_media_note' || (byAgent && visual.media_source);

    let writer;
    if (byAgent) {
        const model = String(visual.model || '').trim();
        writer = zh ? `你的 agent${model && model !== 'agent' ? `（${model}）` : ''}` : `Your agent${model && model !== 'agent' ? ` (${model})` : ''}`;
    } else if (byFrames) {
        const label = String(visual.channel_label || 'Claude');
        writer = visual.model ? `${label}（${visual.model}）` : label;
    } else {
        const name = textModel || (zh ? '文本模型' : 'the text model');
        writer = textModel ? (zh ? `文本模型（${name}）` : `Text model (${name})`) : (zh ? '文本模型' : 'Text model');
    }

    let basis;
    if (byFrames && cited) basis = zh ? `转录稿和画面，引用了 ${cited} 张截图` : `Transcript and frames, ${cited} screenshot(s) cited`;
    else if (byFrames) basis = zh ? '转录稿和画面，没有截图值得放进笔记' : 'Transcript and frames; no screenshot worth citing';
    else basis = zh ? '只有转录稿：没有看画面，也没有截图' : 'The transcript only: no frames, no screenshots';

    const rows = [
        {label: zh ? '谁写的' : 'Written by', value: writer},
        {label: zh ? '根据什么' : 'From', value: basis},
    ];
    if (from === 'debreath_media_note') rows.push({label: zh ? '哪个版本' : 'Version', value: zh ? '剪掉空白后的版本' : 'The cut version'});
    if (from === 'source_media_note') rows.push({label: zh ? '哪个版本' : 'Version', value: zh ? '原片' : 'The original'});

    const reason = String(result?.note_fallback_reason || '').trim();
    return {
        attention: fallback,
        rows,
        why: fallback ? (reason || (zh ? '这次没连上 Claude。' : 'Claude could not be reached this time.')) : '',
        fixes: fallback ? (zh
            ? ['在「设置 → 笔记」填好 Anthropic API Key，再点「重生笔记」。', '或者把下面这句发给你的 agent（Claude Code、Codex 等），用它的订阅结合画面重写。']
            : ['Add an Anthropic API key under Settings → Notes, then click Regenerate note.', 'Or send this to your agent (Claude Code, Codex, ...) to rewrite it from the frames on its own subscription.'])
            : [],
        agentPrompt: fallback ? agentNotePrompt(result, lang, connection) : '',
    };
};

// Said on the task card: one short line. The reason and what to do about it
// are in the editor's 笔记说明 (owner, 2026-10-18: the full paragraph took too
// much room). Empty for any other note.
export const noteFallbackSentence = (result, lang = 'zh') => {
    if (!noteWasTextFallback(result)) return '';
    return lang === 'zh'
        ? '文本模型代写，没看画面；原因见编辑器里的「笔记说明」'
        : 'Written by the text model without the frames; see About this note in the editor';
};

// What the editor says while the note is still being written. Only Claude,
// reading the frames, takes minutes; a text model is not promised a duration.
export const notePendingSentence = (writer, result = null, lang = 'zh') => {
    const zh = lang === 'zh';
    const claudeWriting = writer?.kind === 'claude' || result?.visual_note?.status === 'running';
    if (claudeWriting) {
        return zh
            ? '正在写笔记。Claude 结合画面写一般要几分钟，写好会自动出现。'
            : 'The note is being written. Claude reading the frames usually takes a few minutes; it appears here when done.';
    }
    if (writer?.kind === 'text_model') {
        return zh
            ? `正在写笔记，由 ${noteWriterLabel(writer, lang)} 按文字写，写好会自动出现。`
            : `The note is being written from the text by ${noteWriterLabel(writer, lang)}; it appears here when done.`;
    }
    return zh ? '正在写笔记，写好会自动出现。' : 'The note is being written; it appears here when done.';
};

// The sentence a person pastes into their agent to have it write this task's
// note (agent_note on the backend: material in, note back, nothing to set up).
//
// It works whether or not the agent is connected yet (owner, 2026-10-18: the
// way to a note on the user's own subscription is a sentence they send to their
// agent, which then connects to FluentFlow itself). ``connection`` is what the
// running service says about itself (runtime-config: repoRoot, pythonExecutable):
// with it the sentence carries the exact MCP server to register. No token: the
// server reads it from the repository's .env, so nothing secret goes to the
// agent or its model provider.
export const agentNotePrompt = (result, lang = 'zh', connection = {}) => {
    const zh = lang === 'zh';
    const taskId = String(result?.task_id || '');
    const title = String(result?.display_title || result?.filename || '').trim();
    const write = zh
        ? `用 FluentFlow 给任务${title ? `「${title}」` : ''}写笔记（任务 ID：${taskId}）：先用 get_agent_note_material 取材料，按里面的要求写，写好用 save_agent_note 交回。`
        : `Use FluentFlow to write the note for task${title ? ` "${title}"` : ''} (task ID: ${taskId}): get the material with get_agent_note_material, write it as instructed, and hand it back with save_agent_note.`;
    const server = mcpServerCommand(connection);
    if (!server) return write;
    return zh
        ? `${write}\n如果你还没有 FluentFlow 的这些工具，先把它接入成 MCP 服务，名字叫 fluentflow，命令是 ${server.command}，参数是 ${server.script}，不用填令牌。接好后告诉我新开一个对话，再把这段话发给你。`
        : `${write}\nIf you do not have these FluentFlow tools yet, first add it as an MCP server named fluentflow, command ${server.command}, argument ${server.script}; no token is needed. Then ask me to start a new conversation and send you this again.`;
};

// The MCP server of this FluentFlow, from the service's own paths. A Windows
// root keeps its backslashes so the path reads as the user's Explorer shows it.
export const mcpServerCommand = ({repoRoot = '', pythonExecutable = ''} = {}) => {
    const root = String(repoRoot || '').replace(/[\\/]+$/, '');
    const command = String(pythonExecutable || '').trim();
    if (!root || !command) return null;
    const separator = root.includes('\\') && !root.includes('/') ? '\\' : '/';
    return {command, script: [root, 'scripts', 'fluentflow_mcp_server.py'].join(separator)};
};
