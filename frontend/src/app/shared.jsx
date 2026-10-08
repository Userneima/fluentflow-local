import {useState,createContext,useContext,useMemo} from 'react';
import { localExecutionHeaders } from '../lib/localExecution.js';
import {
    isLocalLarkExportRoute,
    normalizeLarkExportRoute,
    sanitizeSettings,
} from '../lib/settingsModel.js';
import { _dl } from '../lib/download.js';
import { currentApiBase } from '../lib/apiBase.js';

/** API 根路径。判据见 lib/apiBase.js：默认同源，只有 Vite 开发服务器才跨端口。 */
export const API_BASE = currentApiBase();

export const ACCESS_TOKEN_KEY = 'fluentflow_access_token';
export const CLIENT_ID_KEY = 'fluentflow_client_id';
export const LOCAL_SINGLE_USER_CLIENT_ID = 'local-single-user';
export const getAccessToken = () => (localStorage.getItem(ACCESS_TOKEN_KEY) || '').trim();
export const setAccessToken = (token) => {
    const value = String(token || '').trim();
    if (value) localStorage.setItem(ACCESS_TOKEN_KEY, value);
    else localStorage.removeItem(ACCESS_TOKEN_KEY);
};
export const createClientId = () => (
    window.crypto?.randomUUID?.()
    || `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
);
export const shouldUseLocalSingleUserClientId = () => {
    const { hostname } = window.location;
    return hostname === '127.0.0.1' || hostname === 'localhost';
};
// Also written as a cookie, not only to localStorage.
//
// The browser loads a note's inline frames itself, as <img>, and an <img> cannot
// carry the client-id header the fetch helper adds — so those requests arrived
// unidentified and the server answered 404 for a task it holds. The backend has
// always accepted this cookie as the second place to look for the same value;
// nothing ever set it. Now that the note's pictures *are* the note, an image that
// silently fails to load is a broken product feature, not a cosmetic gap.
export const rememberClientIdCookie = (value) => {
    const id = String(value || '').trim();
    if (!id) return;
    try {
        document.cookie = `${CLIENT_ID_KEY}=${encodeURIComponent(id)}; path=/; max-age=31536000; samesite=lax`;
    } catch (_) {
        // Cookies unavailable: fetches still work through the header, and inline
        // images stay broken. Not worth failing anything over.
    }
};
export const getClientId = () => {
    if (shouldUseLocalSingleUserClientId()) {
        localStorage.setItem(CLIENT_ID_KEY, LOCAL_SINGLE_USER_CLIENT_ID);
        rememberClientIdCookie(LOCAL_SINGLE_USER_CLIENT_ID);
        return LOCAL_SINGLE_USER_CLIENT_ID;
    }
    const existing = (localStorage.getItem(CLIENT_ID_KEY) || '').trim();
    if (existing) {
        rememberClientIdCookie(existing);
        return existing;
    }
    const next = createClientId();
    localStorage.setItem(CLIENT_ID_KEY, next);
    rememberClientIdCookie(next);
    return next;
};
export const apiFetch = (input, init={}) => {
    const token = getAccessToken();
    const headers = new Headers(init.headers || {});
    if (!headers.has('X-FluentFlow-Client-Id')) {
        headers.set('X-FluentFlow-Client-Id', getClientId());
    }
    if (token && !headers.has('X-FluentFlow-Access-Token')) {
        headers.set('X-FluentFlow-Access-Token', token);
    }
    return fetch(input, {...init, credentials: init.credentials || 'include', headers});
};
export const apiErrorMessage = (payload, fallback='Request failed') => {
    const detail = payload?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg || item?.message || String(item)).join('; ');
    if (detail && typeof detail === 'object') {
        const message = detail.message || detail.detail || fallback;
        if (detail.required_units != null && detail.balance_units != null) {
            return `${message} 当前 ${detail.balance_units}，预计需要 ${detail.required_units}。`;
        }
        return String(message);
    }
    return fallback;
};

export { fileNameStem, stripGeneratedFilenamePrefix, displayTitleForUser, compactDisplayFilename, videoLinkDisplayTitle } from '../lib/format.js';
export {
    normalizeSourceMode,
    SENSITIVE_SETTING_KEYS,
    LEGACY_REMOVED_SETTING_KEYS,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_FRONTEND_NOTE_MODES,
    NOTE_MODE_OPTIONS,
    LARK_EXPORT_ROUTE_OPENAPI,
    LARK_EXPORT_ROUTE_LOCAL_CLI,
    LARK_EXPORT_ROUTE_AUTO,
    normalizeLarkExportRoute,
    larkExportRouteFromSettings,
    isLocalLarkExportRoute,
    normalizeAiModel,
    sanitizeSettings,
    sensitivePatchFromSettings,
    noteModeLabel,
    DEFAULT_STT_MODEL,
    normalizeSttModel,
} from '../lib/settingsModel.js';
export {
    normalizeSttProvider,
    defaultRuntimeConfig,
    normalizeRuntimeConfig,
    effectiveSttProvider,
    submittedSttProvider,
    sttRouteOptions,
    sttProviderLabel,
} from '../lib/sttPolicy.js';
export const createTaskId = () => (
    window.crypto?.randomUUID?.() ||
    `task_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
);
// localExecutionHeaders now has a single source of truth in
// ../lib/localExecution.js (imported at the top). Re-exported here so existing
// importers of shared.jsx keep working, without a second copy to drift.
export { localExecutionHeaders };
export const isLocalHistoryResult = (result={}) => (
    !!result?.imported_from_local_history ||
    result?.source === 'imported_local_history' ||
    result?.source === 'browser_local_history' ||
    String(result?.task_id || '').startsWith('imported_')
);
/* ═══════════════ i18n ═══════════════ */
export const msgs = {
  en:{
    'nav.subtitle':'Video-to-Lark AI','nav.processing':'Processing records','nav.editor':'Editor','nav.settings':'Settings',
    'status.ready':'System ready','status.idle':'Awaiting task','status.queued':'Queued','status.resolving':'Resolving link…','status.downloading':'Downloading video…','status.saving':'Saving video…','status.upload':'Uploading…','status.prepare_media':'Removing breath gaps (takes minutes)…','status.audio':'Extracting audio…','status.stt':'Transcribing…','status.translation':'Translating subtitles…','status.transcript_ready':'Transcript ready','status.summary':'Writing the note…','status.export':'Exporting to Lark…','status.done':'Done','status.failed':'Failed',
    'dash.minUnit':'min','dash.docUnit':'docs','dash.linkEmpty':'Paste a share text or video link first.','dash.viewTasks':'View records','dash.done':'Processing complete','dash.viewEditor':'View in Editor','dash.recent':'Recent Activity','dash.viewAll':'View All','dash.fileError':'Unsupported format. Please select a video or audio file.','dash.subtitleFileError':'Unsupported transcript file. Please select SRT, VTT, TXT, or MD.','dash.noActivity':'No activity yet. Completed jobs will appear here.','dash.justNow':'just now','dash.mAgo':'m ago','dash.hAgo':'h ago','dash.dAgo':'d ago',
    'dash.statusCompleted':'Completed','dash.statusFailed':'Failed','dash.statusProcessing':'Processing','dash.cancel':'Cancel','dash.fileSize':'File Size','dash.waitingSegment':'Waiting for first transcript segment','dash.progressUnknown':'Working','dash.sttStarting':'Starting transcription engine','dash.sttLoadingModel':'Loading local model','dash.sttChunking':'Preparing progress tracking','dash.sttPreparingAudio':'Preparing audio features','dash.sttWaitingFirst':'Waiting for the first transcript segment','dash.sttChunks':'Transcribing audio','dash.sttSegments':'Receiving transcript segments',
    'edit.title':'Editor','edit.noResult':'No result selected','edit.noResultDesc':'Choose a completed record, then review and edit its transcript and note here.','edit.chooseRecord':'Choose record','edit.summaryPending':'The note is being written; it appears here when done.','edit.summarySkipped':'No note was written this time, as requested. Click Regenerate note when you want one.','edit.summaryFailed':'The transcript is saved, but the note was not written. Click Regenerate note to try again.','edit.export':'Export to Lark','edit.regenerate':'Regenerate note','edit.regenerating':'Regenerating…','edit.regenerateConfirmTitle':'Regenerate this note?','edit.regenerateConfirmDesc':'FluentFlow will regenerate the note from the current transcript and replace the note body. The transcript itself will not be retranscribed.','edit.regenerateConfirmAction':'Regenerate note','edit.retranscribe':'Retranscribe','edit.retranscribing':'Retranscribing…','edit.retranscribeDone':'Retranscription complete','edit.retranscribeConfirmTitle':'Retranscribe this audio?','edit.retranscribeConfirmDesc':'FluentFlow will transcribe again with the current settings and replace the transcript and note for this result.','edit.retranscribeUnavailableTitle':'Source file is not available','edit.retranscribeUnavailableDesc':'Browsers cannot reopen a local file from history without your permission. Choose the original audio/video file to retranscribe it with current settings.','edit.retranscribeConfirmAction':'Start retranscription','edit.retranscribeChooseAction':'Choose original file','edit.cancel':'Cancel','edit.sttElapsed':'Transcription time','edit.exportDone':'Exported to Lark','edit.exportOpen':'Open','edit.exportedOnce':'Exported','edit.exportAgainConfirm':'This note was already exported once. Exporting again creates a new document. Continue?','edit.exportImagesMissing':'{n} screenshot(s) did not upload','edit.autoExportFailed':'Auto-export to Lark failed','edit.exportFail':'Export failed','edit.regenDone':'Note regenerated','edit.transcriptSaving':'Saving…','edit.transcriptSaveFailed':'Save failed','edit.editRecords':'Edit records','edit.editRecordsTitle':'Transcript edit records','edit.editRecordsDesc':'Each record keeps the changed sentence and nearby context. These records are saved locally with the edited transcript.','edit.editRecordsEmpty':'No changed segment has been recorded yet.','edit.before':'Before','edit.after':'After','edit.previousSentence':'Previous sentence','edit.nextSentence':'Next sentence','edit.followPlayback':'Follow playback','edit.audioUnavailable':'Choose the original audio/video to listen while editing.','edit.chooseAudio':'Choose source audio','edit.sourceLoading':'Loading source audio…',
    'prompt.label':'Prompt Template','prompt.select':'Select prompt style','prompt.customPlaceholder':'Enter your custom system prompt here...','prompt.collapsed':'Change prompt','prompt.editHint':'Edit prompt before regenerating','prompt.saveAsPreset':'Save custom as preset',
    'dl.summary':'Download note','dl.txt':'Plain Text (.txt)','dl.md':'Markdown (.md)','dl.srt':'Source subtitles (.srt)','dl.vtt':'Source WebVTT (.vtt)','dl.bilingualSrt':'Bilingual subtitles (.srt)','dl.bilingualVtt':'Bilingual WebVTT (.vtt)','dl.sourceVideo':'Source video','dl.pdf':'PDF Document','dl.word':'Word Document (.docx)','dl.generating':'Generating…','dl.success':'Download started','dl.pdfPrintOpened':'Print dialog opened. Choose Save as PDF.',
    'set.title':'Settings','set.autoExport':'Auto-export to Lark after processing','set.larkExportRoute':'Lark export route','set.larkRouteOpenapi':'Feishu app export','set.larkRouteOpenapiHint':'Exports with a Feishu app you created on the open platform. Enter its App ID and App Secret under Advanced · Other credentials below.','set.larkRouteLocalCli':'Local identity export','set.larkRouteLocalCliHint':'Uses the Feishu command-line tool lark-cli already signed in on this computer, and writes to My Library as you. No app needed.','set.larkRouteAuto':'Automatic','set.larkRouteAutoHint':'If lark-cli is installed and signed in on this computer, writes to My Library as you; otherwise uses your Feishu app.','set.larkFolder':'Feishu folder (app export)','set.larkFolderHint':'Paste a Feishu folder link. Without one, app exports land in the app\'s own space, which you may not be able to open. Share the folder with your app (can edit) first.','set.larkFolderPh':'https://….feishu.cn/drive/folder/…','set.larkHistory':'Export History','set.providerLocal':'Local transcription','set.sttSpeed':'Transcription Speed','set.speedFast':'Fast','set.speedBalanced':'Balanced','set.speedAccurate':'Accurate','set.provider':'Provider','set.aiModel':'AI Model','set.openaiKey':'OpenAI API Key','set.deepseekKey':'DeepSeek API Key','set.dashscopeKey':'Qwen (Alibaba Cloud Bailian) API Key','set.editCoursePrompt':'Edit “General Study Notes” system prompt','set.editBuiltinTemplate':'Edit this template','set.deleteBuiltinPrompt':'Delete this template category','set.deleteBuiltinPromptConfirm':'Delete this template category (remove it from the UI)?','set.presetNamePh':'Preset name','set.deletePreset':'Delete','set.deletePresetConfirm':'Delete this saved preset?','set.presetSaved':'Preset saved',
  },
  zh:{
    'nav.subtitle':'视频转飞书 AI','nav.processing':'处理记录','nav.editor':'编辑器','nav.settings':'设置',
    'status.ready':'系统就绪','status.idle':'等待任务','status.queued':'排队中','status.resolving':'解析链接中…','status.downloading':'下载视频中…','status.saving':'保存视频中…','status.upload':'上传中…','status.prepare_media':'正在剪掉气口，要几分钟…','status.audio':'音频提取中…','status.stt':'转录中…','status.translation':'正在翻译字幕…','status.transcript_ready':'转录已完成','status.summary':'正在写笔记…','status.export':'导出到飞书…','status.done':'完成','status.failed':'失败',
    'dash.minUnit':'分钟','dash.docUnit':'份','dash.linkEmpty':'请先粘贴分享文本或视频链接。','dash.viewTasks':'查看记录','dash.done':'处理完成','dash.viewEditor':'在编辑器中查看','dash.recent':'最近活动','dash.viewAll':'查看全部','dash.fileError':'不支持的格式，请选择视频或音频文件。','dash.subtitleFileError':'不支持的字幕/转录文件，请选择 SRT、VTT、TXT 或 MD。','dash.noActivity':'暂无活动记录，完成的任务会显示在这里。','dash.justNow':'刚刚','dash.mAgo':'分钟前','dash.hAgo':'小时前','dash.dAgo':'天前',
    'dash.statusCompleted':'已完成','dash.statusFailed':'失败','dash.statusProcessing':'处理中','dash.cancel':'取消','dash.fileSize':'文件大小','dash.waitingSegment':'等待第一段转录结果','dash.progressUnknown':'处理中','dash.sttStarting':'正在启动转录引擎','dash.sttLoadingModel':'正在加载本地模型','dash.sttChunking':'正在准备进度追踪','dash.sttPreparingAudio':'正在准备音频特征','dash.sttWaitingFirst':'等待第一段转录结果','dash.sttChunks':'正在转录音频','dash.sttSegments':'正在接收转录片段',
    'edit.title':'编辑器','edit.noResult':'未选择结果','edit.noResultDesc':'从处理记录选择一条已完成结果后，在这里复查和编辑转录与笔记。','edit.chooseRecord':'选择处理记录','edit.summaryPending':'正在写笔记，写好会自动出现。','edit.summarySkipped':'这次按要求没写笔记，需要时点「重生笔记」。','edit.summaryFailed':'转录已保存，但笔记没写成。可以点「重生笔记」再试一次。','edit.export':'导出到飞书','edit.regenerate':'重生笔记','edit.regenerating':'重生中…','edit.regenerateConfirmTitle':'重生当前笔记？','edit.regenerateConfirmDesc':'FluentFlow 会基于当前转录重生笔记，并替换右侧笔记正文；不会重新转录音频。','edit.regenerateConfirmAction':'确认重生笔记','edit.retranscribe':'重新转录','edit.retranscribing':'重新转录中…','edit.retranscribeDone':'重新转录完成','edit.retranscribeConfirmTitle':'重新转录当前音频？','edit.retranscribeConfirmDesc':'FluentFlow 会按当前设置重新转录，并替换当前结果里的转录文本和笔记。','edit.retranscribeUnavailableTitle':'当前没有可直接重转的原文件','edit.retranscribeUnavailableDesc':'浏览器不会在历史记录里长期保留本地音视频文件权限。请选择原始音视频文件，再用当前设置重新转录。','edit.retranscribeConfirmAction':'确认重新转录','edit.retranscribeChooseAction':'选择原始文件','edit.cancel':'取消','edit.sttElapsed':'转录耗时','edit.exportDone':'已导出到飞书','edit.exportOpen':'打开','edit.exportedOnce':'已导出','edit.exportAgainConfirm':'已经导出过一次，再导出会新建一篇文档。继续吗？','edit.exportImagesMissing':'{n} 张截图没传上去','edit.autoExportFailed':'自动导出到飞书没成功','edit.exportFail':'导出失败','edit.regenDone':'笔记已重生','edit.transcriptSaving':'保存中…','edit.transcriptSaveFailed':'保存失败','edit.editRecords':'修改记录','edit.editRecordsTitle':'转录稿修改记录','edit.editRecordsDesc':'每条记录会保留修改句子和相邻上下文，并随编辑稿一起保存到本地。','edit.editRecordsEmpty':'还没有记录到分段修改。','edit.before':'修改前','edit.after':'修改后','edit.previousSentence':'上一句','edit.nextSentence':'下一句','edit.followPlayback':'跟随播放','edit.audioUnavailable':'选择原始音视频后，可边听边校对。','edit.chooseAudio':'选择原音频','edit.sourceLoading':'正在读取原音频…',
    'prompt.label':'提示词模板','prompt.select':'选择提示词风格','prompt.customPlaceholder':'在此输入自定义系统提示词…','prompt.collapsed':'更换提示词','prompt.editHint':'重生笔记前可编辑提示词','prompt.saveAsPreset':'将自定义保存为预设',
    'dl.summary':'下载笔记','dl.txt':'纯文本 (.txt)','dl.md':'Markdown (.md)','dl.srt':'原文字幕 (.srt)','dl.vtt':'原文 WebVTT (.vtt)','dl.bilingualSrt':'中英双语字幕 (.srt)','dl.bilingualVtt':'中英双语 WebVTT (.vtt)','dl.sourceVideo':'原视频','dl.pdf':'PDF 文档','dl.word':'Word 文档 (.docx)','dl.generating':'生成中…','dl.success':'已开始下载','dl.pdfPrintOpened':'已打开系统打印，可选择另存为 PDF。',
    'set.title':'设置','set.autoExport':'处理完成后自动导出到飞书','set.larkExportRoute':'飞书导出路线','set.larkRouteOpenapi':'飞书应用导出','set.larkRouteOpenapiHint':'用你自己在飞书开放平台建的应用导出，App ID 和 App Secret 填在下方「高级 · 其他凭证」里。','set.larkRouteLocalCli':'本机身份导出','set.larkRouteLocalCliHint':'用这台电脑上已登录的飞书命令行工具 lark-cli，以你本人的身份写进「我的文档库」，不用建应用。','set.larkRouteAuto':'自动选择','set.larkRouteAutoHint':'这台电脑装了并登录了 lark-cli，就以你本人的身份写进「我的文档库」；否则用你的飞书应用导出。','set.larkFolder':'飞书文件夹（应用导出用）','set.larkFolderHint':'粘贴一个飞书云空间文件夹的链接。不填的话，用应用导出的文档会建在应用自己的空间里，你可能打不开。先把这个文件夹共享给你的应用（可编辑）。','set.larkFolderPh':'https://….feishu.cn/drive/folder/…','set.larkHistory':'导出记录','set.providerLocal':'本地转录','set.sttSpeed':'转录速度','set.speedFast':'快速','set.speedBalanced':'均衡','set.speedAccurate':'高准确率','set.provider':'服务商','set.aiModel':'AI 模型','set.openaiKey':'OpenAI API Key','set.deepseekKey':'DeepSeek API Key','set.dashscopeKey':'通义千问（阿里云百炼）API Key','set.editCoursePrompt':'编辑「通用学习笔记」系统提示词','set.editBuiltinTemplate':'编辑该模板内容','set.deleteBuiltinPrompt':'删除该模板类目','set.deleteBuiltinPromptConfirm':'确定删除该模板类目（从界面移除）？','set.presetNamePh':'预设名称','set.deletePreset':'删除','set.deletePresetConfirm':'确定删除该保存的预设？','set.presetSaved':'已保存预设',
  },
};
export const I18nCtx = createContext();
export const I18nProvider = ({children}) => {
    const [lang,setLang] = useState(() => localStorage.getItem('fluentflow_lang')||'zh');
    const t = (k) => msgs[lang]?.[k] ?? msgs.en[k] ?? k;
    const toggleLang = () => { const n = lang==='en'?'zh':'en'; setLang(n); localStorage.setItem('fluentflow_lang',n); };
    return <I18nCtx.Provider value={{t,lang,toggleLang}}>{children}</I18nCtx.Provider>;
};
export const useI18n = () => useContext(I18nCtx);

export { accountJobsCacheKey, readCachedAccountJobs, writeCachedAccountJobs, cacheJobRecord, mergeCachedJobs, sortJobsForHistoryView, hasTranscriptResult, historyStatusFromJob, jobVisibleInHistory, resultDisplayTitle, jobDisplayTitle, resultToHistoryEntry, jobToHistoryEntry, jobToCurrentJob, historyEntryToResult } from '../lib/jobMappers.js';

export { fmtTime, autoSizeTextarea, composeTranscriptText, normalizeTranscriptSegments, normalizeDisplaySegments, pickTranscriptSegments, pickTranscriptBaselineSegments, pickDisplayTranscriptSegments, buildTranscriptEditRecords, fmtElapsed, fmtFileSize, totalFileSizeMb, fmtBytes, fmtDateTime, friendlyTaskError, diagnoseTaskError, taskErrorContextForJob, providerDisplayName, fmtSttRelative, sttStatusLabel, sttProgressFraction, isSttProgressUnmeasured, isDownloadProgressUnmeasured, videoSourceProgressOf, jobProgressLabel, timeAgo, noteGenerationDiagnosis } from '../lib/format.js';

export { MD_TABLE_ALIGN_RE, splitMdTableRow, isPipeTableRow, looksLikeMdTable, looksLikeLoosePipeTable, renderTableHtml, simpleMd } from '../lib/markdown.js';

export { _dl, _baseName, _fmtSrtTime, _fmtVttTime, dlTranscriptTxt, dlTranscriptSrt, dlTranscriptVtt, dlBilingualTranscriptSrt, dlBilingualTranscriptVtt, dlSummaryTxt, dlSummaryMd, dlSummaryWord, dlSummaryPdf, dlSummaryImage } from '../lib/download.js';

export {DropdownMenu} from './DropdownMenu.jsx';

/* ═══════════════ hooks ═══════════════ */
// The API surface has no inputs, so one object per component is enough. Pages
// list these functions as effect dependencies; a fresh identity every render
// would re-run those effects every render.
export const useApi = () => useMemo(createApi, []);
const createApi = () => {
    // Every way into the pipeline sends the same settings under the same names.
    // The upload, the link, a path from the system dialog and a whole folder used
    // to each spell their own list, and the shorter lists dropped settings
    // without an error: a recording picked through the dialog quietly skipped
    // the Feishu export the same file got when dragged in. One list, here.
    const submitOptionFields = (options={}) => {
        const fields = {};
        if(options.exportToLark) {
            const larkRoute = normalizeLarkExportRoute(options.larkExportRoute, !!options.larkViaCli);
            fields.export_to_lark = "true";
            fields.lark_export_route = larkRoute;
            fields.lark_via_cli = isLocalLarkExportRoute(larkRoute) ? "true" : "false";
        }
        if(options.title) fields.title = options.title;
        if(options.folderToken) fields.folder_token = options.folderToken;
        if(options.skipSummary) fields.skip_summary = "true";
        if(options.aiProvider) fields.ai_provider = options.aiProvider;
        if(options.aiModel) fields.ai_model = options.aiModel;
        if(options.systemPrompt) fields.system_prompt = options.systemPrompt;
        if(options.noteMode) fields.note_mode = options.noteMode;
        if(options.promptPreset) fields.prompt_preset = options.promptPreset;
        if(options.promptPresetLabel) fields.prompt_preset_label = options.promptPresetLabel;
        if(options.generateVisuals) fields.generate_visuals = "true";
        if(options.sttProvider) fields.stt_provider = options.sttProvider;
        if(options.sttModel) fields.stt_model = options.sttModel;
        if(options.sttSpeed) fields.stt_speed = options.sttSpeed;
        if(options.sttLanguage) fields.stt_language = options.sttLanguage;
        if(options.speakerDiarization) fields.speaker_diarization = "true";
        if(options.voiceEnhance) fields.voice_enhance = "true";
        if(options.durationLimitSeconds) fields.duration_limit_seconds = String(options.durationLimitSeconds);
        return fields;
    };
    const appendProcessOptions = (fd, options={}) => {
        Object.entries(submitOptionFields(options)).forEach(([key, value]) => fd.append(key, value));
    };
    // A subtitle file is not transcribed or exported: only the note fields apply.
    const appendAiOptions = (fd, options={}) => {
        const fields = submitOptionFields(options);
        ["ai_provider", "ai_model", "system_prompt", "note_mode", "prompt_preset", "prompt_preset_label", "generate_visuals"]
            .forEach((key) => { if (fields[key]) fd.append(key, fields[key]); });
    };
    const readSseResult = async (r, onProgress) => {
        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let buf = '', result = null;
        while(true){
            const {value,done} = await reader.read();
            if(done) break;
            buf += decoder.decode(value,{stream:true});
            const parts = buf.split('\n\n');
            buf = parts.pop() || '';
            for(const part of parts){
                const dl = part.split('\n').find(l=>l.startsWith('data: '));
                if(!dl) continue;
                try{
                    const data = JSON.parse(dl.slice(6));
                    if(data.stage==='done'){ result=data.result; onProgress?.({stage:'done',progress:100,result:data.result}); }
                    else if(data.stage==='transcript_ready'){ onProgress?.({stage:'transcript_ready',progress:data.progress||60,result:data.result}); }
                    else if(data.stage==='error'){ throw new Error(data.error||'Processing failed'); }
                    else { onProgress?.(data); }
                }catch(pe){ if(pe.message && !pe.message.startsWith('Unexpected')) throw pe; }
            }
        }
        if(!result) throw new Error('No result received from server');
        return result;
    };
    const processVideoSSE = async (file, options={}, onProgress, signal) => {
        const fd = new FormData();
        fd.append("file", file);
        if(options.taskId) fd.append("task_id", options.taskId);
        if(options.sourceLastModifiedMs) fd.append("source_last_modified_ms", String(options.sourceLastModifiedMs));
        appendProcessOptions(fd, options);
        const headers = localExecutionHeaders(options);
        const r = await apiFetch(`${API_BASE}/process`,{method:"POST",body:fd,headers,signal});
        if(!r.ok){
            const e = await r.json().catch(()=>({}));
            const err = new Error(apiErrorMessage(e, `HTTP ${r.status}`));
            err.status = r.status;
            err.payload = e;
            throw err;
        }
        return await readSseResult(r, onProgress);
    };
    // Uploads go through XHR (not fetch) so we can report real upload progress
    // and, critically, fail fast on a stalled connection instead of hanging the
    // UI forever. A watchdog aborts the request if no bytes move (and no server
    // response arrives) for `stallMs`.
    const enqueueProcessFiles = (files, options={}, {onProgress, signal, stallMs=120000}={}) => {
        return new Promise((resolve, reject) => {
        const fd = new FormData();
        Array.from(files || []).forEach((file) => fd.append("files", file));
        appendProcessOptions(fd, options);
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_BASE}/queue/process`);
        xhr.withCredentials = true;
        const token = getAccessToken();
        xhr.setRequestHeader("X-FluentFlow-Client-Id", getClientId());
        if (token) xhr.setRequestHeader("X-FluentFlow-Access-Token", token);
        Object.entries(localExecutionHeaders(options)).forEach(([k, v]) => xhr.setRequestHeader(k, v));

        let lastTick = Date.now();
        let settled = false;
        const finish = (fn) => { if (settled) return; settled = true; clearInterval(watchdog); fn(); };
        const watchdog = setInterval(() => {
            if (Date.now() - lastTick > stallMs) {
                // Reject with the stall error BEFORE aborting, so the abort's
                // onabort handler (which would look like a user cancel) is a no-op.
                finish(() => reject(new Error("Upload stalled or timed out. Please try again.")));
                try { xhr.abort(); } catch(_) {}
            }
        }, 5000);

        xhr.upload.onprogress = (e) => {
            lastTick = Date.now();
            if (onProgress && e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.upload.onload = () => { lastTick = Date.now(); };
        xhr.onload = () => {
            let data = {};
            try { data = JSON.parse(xhr.responseText || "{}"); } catch(_) {}
            if (xhr.status >= 200 && xhr.status < 300) finish(() => resolve(data));
            else finish(() => reject(Object.assign(new Error(apiErrorMessage(data, `HTTP ${xhr.status}`)), {status: xhr.status})));
        };
        xhr.onerror = () => finish(() => reject(new Error("Upload failed. Please check the connection and try again.")));
        xhr.onabort = () => finish(() => reject(Object.assign(new Error("Upload cancelled."), {aborted: true})));
        if (signal) signal.addEventListener("abort", () => { try { xhr.abort(); } catch(_) {} });
            xhr.send(fd);
        });
    };
    const createVideoSourceJob = async (input, options={}, signal) => {
        const payloadOptions = submitOptionFields(options);
        if(options.cookiesFromBrowser) payloadOptions.cookies_from_browser = options.cookiesFromBrowser;
        const r = await apiFetch(`${API_BASE}/video-sources/jobs`, {
            method:"POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
            // allow_duplicate is a request flag, not a run option: it sits beside
            // `options`, so it is never stored with the task or reused on retry.
            body: JSON.stringify({input, options: payloadOptions, ...(options.allowDuplicate ? {allow_duplicate: true} : {})}),
            signal,
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) {
            // A 409 "already processed this link" carries the earlier task, which
            // the page offers to open; the payload travels with the error.
            const err = new Error(apiErrorMessage(data, `HTTP ${r.status}`));
            err.status = r.status;
            err.payload = data;
            throw err;
        }
        return data;
    };
    const checkVideoCookies = async (browser) => {
        const r = await apiFetch(`${API_BASE}/video-sources/cookie-check`, {
            method:"POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders({})},
            body: JSON.stringify({browser}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    const subscribeJobEvents = async (taskId, onProgress, signal, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/events`, {headers: localExecutionHeaders(options), signal});
        if(!r.ok){
            const e = await r.json().catch(()=>({}));
            const err = new Error(e.detail||`HTTP ${r.status}`);
            err.status = r.status;
            err.payload = e;
            throw err;
        }
        return await readSseResult(r, onProgress);
    };
    const summarizeTranscriptFile = async (file, options={}, signal) => {
        const fd = new FormData();
        fd.append("file", file);
        if(options.taskId) fd.append("task_id", options.taskId);
        if(options.skipSummary) fd.append("skip_summary", "true");
        appendAiOptions(fd, options);
        const r = await apiFetch(`${API_BASE}/summarize-transcript-file`, {method:"POST", body:fd, signal});
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        return data;
    };
    const recordEvent = async (payload) => {
        try {
            await apiFetch(`${API_BASE}/events`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload || {}),
            });
        } catch(_) {}
    };
    const getJob = async (taskId, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}`, {headers: localExecutionHeaders(options)});
        const data = await r.json().catch(()=>({}));
        if(!r.ok) {
            const err = new Error(apiErrorMessage(data, r.status === 404 ? 'Job not found' : `HTTP ${r.status}`));
            err.status = r.status;
            err.payload = data;
            throw err;
        }
        return data;
    };
    const cancelJob = async (taskId, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/cancel`, {
            method:"POST",
            headers: localExecutionHeaders(options),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    // Cancel a record the page is holding, rather than a bare task id.
    const cancelJobRecord = (job={}) => cancelJob(job.taskId, {sttProvider: job.sttProvider});
    const deleteJob = async (taskId, options={}) => {
        const headers = localExecutionHeaders(options);
        let r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}`, {method:"DELETE", headers});
        if (r.status === 405) {
            r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/delete`, {method:"POST", headers});
        }
        const data = await r.json().catch(()=>({}));
        if(!r.ok) {
            const err = new Error(apiErrorMessage(data, `HTTP ${r.status}`));
            err.status = r.status;
            err.payload = data;
            throw err;
        }
        return data;
    };
    const retryJob = async (taskId, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/retry`, {
            method: "POST",
            headers: localExecutionHeaders(options),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) {
            const err = new Error(apiErrorMessage(data, `HTTP ${r.status}`));
            err.status = r.status;
            err.payload = data;
            throw err;
        }
        return data;
    };
    const fetchJobSourceFile = async (taskId, filename='source', options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/source`, {
            headers: localExecutionHeaders(options),
        });
        if(!r.ok) throw new Error('Source file not found');
        const blob = await r.blob();
        return new File([blob], filename || 'source', {type: blob.type || 'application/octet-stream'});
    };
    const fetchJobArtifactFile = async (taskId, kind, filename='artifact', options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(kind)}`, {
            headers: localExecutionHeaders(options),
        });
        if(!r.ok) throw new Error('Artifact not found');
        const blob = await r.blob();
        return new File([blob], filename || kind, {type: blob.type || 'application/octet-stream'});
    };
    const uploadJobPlaybackAudio = async (taskId, file) => {
        const fd = new FormData();
        fd.append("file", file);
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/playback-audio`, {
            method: "POST",
            headers: localExecutionHeaders({sttProvider: 'local'}),
            body: fd,
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    // Every task, not a window: the records page lists all of them. A poll
    // passes `updatedSince` (the newest `updated_at` it already holds) to get
    // only the rows written since, instead of every summary again.
    const getJobs = async (options={}) => {
        const params = new URLSearchParams();
        if(options.updatedSince) params.set('updated_since', options.updatedSince);
        const query = params.toString();
        const r = await apiFetch(`${API_BASE}/jobs${query ? `?${query}` : ''}`, {
            headers: localExecutionHeaders(options),
        });
        if(!r.ok) throw new Error('Jobs unavailable');
        const data = await r.json();
        return Array.isArray(data?.jobs) ? data.jobs : [];
    };
    const getInterruptedJobs = async () => {
        const r = await apiFetch(`${API_BASE}/jobs/interrupted`, {
            headers: localExecutionHeaders({sttProvider: 'local'}),
        });
        if(!r.ok) throw new Error('Interrupted tasks unavailable');
        const data = await r.json();
        return Array.isArray(data?.tasks) ? data.tasks : [];
    };
    const acknowledgeInterruptedJobs = async (taskIds=[]) => {
        const r = await apiFetch(`${API_BASE}/jobs/interrupted/acknowledge`, {
            method: "POST",
            headers: {...localExecutionHeaders({sttProvider: 'local'}), 'Content-Type': 'application/json'},
            body: JSON.stringify({task_ids: taskIds}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    const downloadJobArtifact = async (taskId, kind, filename, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(kind)}`, {
            headers: localExecutionHeaders(options),
        });
        if(!r.ok) throw new Error('Artifact not found');
        const blob = await r.blob();
        _dl(blob, filename || `${kind}.txt`);
    };
    // Starts breath-gap removal and returns once it is accepted. The render takes
    // minutes and writes its progress into the job result, so callers poll getJob
    // rather than holding this request open. A refusal — another render running,
    // source gone, task not finished — comes back here with its reason.
    const startJobDebreath = async (taskId, payload={}, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/debreath`, {
            method: "POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
            body: JSON.stringify(payload || {}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    // Two shapes, and the cheap one is the default the page reaches for first.
    // `{preview: true}` answers "would this work, what would it send, and whose
    // Claude allowance pays" without spending anything; a call without it
    // extracts frames and writes the note in the background, so callers poll
    // getJob rather than holding this request open.
    const startJobVisualNote = async (taskId, payload={}, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/visual-note`, {
            method: "POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
            body: JSON.stringify(payload || {}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) {
            // The status travels with the error: a 409 is a refusal with a reason
            // (busy queue, no cut file), which the page words differently.
            const err = new Error(apiErrorMessage(data, `HTTP ${r.status}`));
            err.status = r.status;
            err.payload = data;
            throw err;
        }
        return data;
    };
    // Whether this task can be rewritten by Claude from its frames: it needs a
    // cut file still on disk, a recording rather than a subtitle file, and so
    // on. Asked of the rewrite route itself with `preview: true`, which runs the
    // same eligibility check as the real run and starts nothing. Returns
    // {available: true|false|null, reason}; null means the service could not
    // say, and the page offers both ways then. A busy queue is not part of
    // this answer: the real start reports it as a 409.
    const getVisualNoteAvailability = async (taskId, options={}) => {
        try {
            const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/visual-note`, {
                method: "POST",
                headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
                body: JSON.stringify({preview: true}),
            });
            if(!r.ok) return {available: null, reason: null};
            const data = await r.json().catch(()=>({}));
            if(typeof data?.eligible !== 'boolean') return {available: null, reason: null};
            return {available: data.eligible, reason: data.reason ? String(data.reason) : null, running: data.running === true};
        } catch (_) {
            return {available: null, reason: null};
        }
    };
    // Ask the machine to open its own file dialog, and process what comes back
    // where it lies. The browser's picker cannot tell this page which folder a
    // file came from, so a normal upload has no "next to the original" to save the
    // cut version into; the system dialog answers with a real path.
    const chooseLocalMedia = async (payload={}) => {
        const r = await apiFetch(`${API_BASE}/local/choose-media`, {
            method: "POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders({sttProvider: 'local'})},
            body: JSON.stringify(payload || {}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    // Ask this machine where a dropped file lives, before deciding to upload it.
    //
    // The browser hands over a dropped file's bytes plus three labels — name,
    // size, modification time — and never its folder; a web page must not learn
    // the shape of somebody's disk. This is not a web page. Sending only the
    // labels lets the local service look in the folders it has already been
    // pointed at, and a hit means the gigabyte never has to be copied at all.
    // A miss costs nothing and the caller uploads, exactly as before.
    const locateDroppedFile = async (file) => {
        try {
            const r = await apiFetch(`${API_BASE}/local/locate-dropped`, {
                method: "POST",
                headers: {"Content-Type":"application/json", ...localExecutionHeaders({sttProvider: 'local'})},
                body: JSON.stringify({
                    name: file?.name || '',
                    size_bytes: file?.size || 0,
                    modified_ms: file?.lastModified || null,
                }),
            });
            if (!r.ok) return null;
            const data = await r.json().catch(()=>({}));
            return data?.found ? data : null;
        } catch (_) {
            // Never a reason to refuse the drop: the upload path is still there.
            return null;
        }
    };
    // Ask the machine for a folder, and get back what is in it. One call, because
    // the count is what the next decision is about: a folder is however many
    // Claude calls it has recordings in it.
    const chooseLocalFolder = async (payload={}) => {
        const r = await apiFetch(`${API_BASE}/local/choose-folder`, {
            method: "POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders({sttProvider: 'local'})},
            body: JSON.stringify(payload || {}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    // The by-path routes take the same fields as the upload (submitOptionFields),
    // in a JSON body. Two differences, both about what the route does with a
    // missing field: the paths are always transcribed on this machine, so no
    // engine is named; and speaker separation is ON when the field is absent
    // (for callers with no settings page), so "off" has to be said out loud.
    const localQueueBody = (options={}) => {
        const body = submitOptionFields(options);
        delete body.stt_provider;
        delete body.stt_language;
        body.speaker_diarization = options.speakerDiarization ? "true" : "false";
        return body;
    };
    const processLocalFolder = async (path, options={}) => {
        const r = await apiFetch(`${API_BASE}/queue/process-folder`, {
            method: "POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders({sttProvider: 'local'})},
            body: JSON.stringify({path, ...localQueueBody(options)}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    const processLocalPaths = async (paths, options={}) => {
        const r = await apiFetch(`${API_BASE}/queue/process-local-files`, {
            method: "POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders({sttProvider: 'local'})},
            body: JSON.stringify({paths, ...localQueueBody(options)}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    const saveTranscriptEdit = async (taskId, payload={}, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/transcript`, {
            method: "PATCH",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
            body: JSON.stringify(payload),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        return data;
    };
    const saveSummaryEdit = async (taskId, payload={}, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/summary`, {
            method: "PATCH",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
            body: JSON.stringify(payload),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        return data;
    };
    const translateJobSegments = async (taskId, payload={}, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/translations/zh`, {
            method: "POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
            body: JSON.stringify(payload || {}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data;
    };
    const getCredentialsStatus = async () => {
        const r = await apiFetch(`${API_BASE}/credentials/status`);
        if(!r.ok) throw new Error('Credential status unavailable');
        return await r.json();
    };
    const getSpeakerDiarizationStatus = async () => {
        const r = await apiFetch(`${API_BASE}/speaker-diarization/status`);
        if(!r.ok) throw new Error('Speaker diarization status unavailable');
        return await r.json();
    };
    const saveCredentials = async (payload) => {
        const r = await apiFetch(`${API_BASE}/credentials`, {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify(payload || {}),
        });
        if(!r.ok) throw new Error('Credential save failed');
        return await r.json();
    };
    // The service's remembered choices (see lib/videoLinkPrefs.js). Both return
    // the whole preference map.
    const getPreferences = async () => {
        const r = await apiFetch(`${API_BASE}/preferences`);
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data?.preferences && typeof data.preferences === 'object' ? data.preferences : {};
    };
    const savePreferences = async (patch) => {
        const r = await apiFetch(`${API_BASE}/preferences`, {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify(patch || {}),
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
        return data?.preferences && typeof data.preferences === 'object' ? data.preferences : {};
    };
    const checkHealth = async () => { try{ const r = await apiFetch(`${API_BASE}/health`); return r.ok ? await r.json() : false;}catch(_){return false;} };
    return {processVideoSSE, enqueueProcessFiles, createVideoSourceJob, checkVideoCookies, subscribeJobEvents, summarizeTranscriptFile, recordEvent, getJob, cancelJob, cancelJobRecord, deleteJob, retryJob, getJobs, getInterruptedJobs, acknowledgeInterruptedJobs, fetchJobSourceFile, fetchJobArtifactFile, uploadJobPlaybackAudio, downloadJobArtifact, startJobDebreath, startJobVisualNote, getVisualNoteAvailability, chooseLocalMedia, chooseLocalFolder, locateDroppedFile, processLocalPaths, processLocalFolder, saveTranscriptEdit, saveSummaryEdit, translateJobSegments, getCredentialsStatus, saveCredentials, getSpeakerDiarizationStatus, getPreferences, savePreferences, checkHealth};
};

export const useSettings = () => {
    const loadSettings = () => {
        try{
            const raw = JSON.parse(localStorage.getItem("fluentflow_settings")||"{}");
            const clean = sanitizeSettings(raw);
            if (JSON.stringify(raw) !== JSON.stringify(clean)) {
                localStorage.setItem("fluentflow_settings", JSON.stringify(clean));
            }
            return clean;
        } catch(_){return {};}
    };
    const saveSettings = (s) => localStorage.setItem("fluentflow_settings", JSON.stringify(sanitizeSettings(s)));
    return {loadSettings, saveSettings};
};
