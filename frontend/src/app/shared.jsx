import {useState,createContext,useContext} from 'react';
import { localExecutionHeaders } from '../lib/localExecution.js';
import {
    isLocalLarkExportRoute,
    normalizeLarkExportRoute,
    sanitizeSettings,
} from '../lib/settingsModel.js';
import { _dl } from '../lib/download.js';
import { getDirectUploadTransport } from './directUploadTransport.js';
import { getHostedApiExtension } from './hostedApiExtension.js';

/** API 根路径：线上与后端同域时用相对路径；本地前端单独跑在其它端口时指向本机 8000。 */
export const API_BASE = (() => {
    const normalize = (value) => String(value || '').trim().replace(/\/+$/, '');
    const configured = normalize(window.FLUENTFLOW_CONFIG?.apiBase || localStorage.getItem('fluentflow_api_base'));
    if (configured) return configured;
    const { hostname, port } = window.location;
    if (!hostname) return "http://127.0.0.1:8000";
    const local = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
    if (local && port && port !== "8000") return "http://127.0.0.1:8000";
    return "";
})();

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
export const getClientId = () => {
    if (shouldUseLocalSingleUserClientId()) {
        localStorage.setItem(CLIENT_ID_KEY, LOCAL_SINGLE_USER_CLIENT_ID);
        return LOCAL_SINGLE_USER_CLIENT_ID;
    }
    const existing = (localStorage.getItem(CLIENT_ID_KEY) || '').trim();
    if (existing) return existing;
    const next = createClientId();
    localStorage.setItem(CLIENT_ID_KEY, next);
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
    SENSITIVE_SETTING_KEYS,
    LEGACY_REMOVED_SETTING_KEYS,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_FRONTEND_NOTE_MODES,
    NOTE_MODE_OPTIONS,
    LARK_EXPORT_ROUTE_OPENAPI,
    LARK_EXPORT_ROUTE_LOCAL_CLI,
    normalizeLarkExportRoute,
    larkExportRouteFromSettings,
    extraLarkExportRouteOptions,
    isLocalLarkExportRoute,
    isUserOAuthLarkExportRoute,
    normalizeAiModel,
    sanitizeSettings,
    sensitivePatchFromSettings,
    noteModeLabel,
    DEFAULT_STT_MODEL,
    normalizeSttModel,
} from '../lib/settingsModel.js';
export {
    normalizeSttProvider,
    isCloudSttProvider,
    isCloudSttConfigured,
    defaultRuntimeConfig,
    normalizeRuntimeConfig,
    effectiveSttProvider,
    cloudSttMissingMessage,
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
    'nav.subtitle':'Video-to-Lark AI','nav.dashboard':'Start','nav.processing':'Processing records','nav.editor':'Editor','nav.settings':'Settings','nav.admin':'Admin','nav.newProject':'New Project','nav.search':'Search projects...','nav.projects':'Projects','nav.integrations':'Integrations',
    'status.ready':'System ready','status.idle':'Awaiting task','status.queued':'Queued','status.resolving':'Resolving link…','status.downloading':'Downloading video…','status.saving':'Saving video…','status.upload':'Uploading…','status.audio':'Extracting audio…','status.stt':'Transcribing…','status.translation':'Translating subtitles…','status.transcript_ready':'Transcript ready','status.summary':'AI summarizing…','status.export':'Exporting to Lark…','status.done':'Done','status.failed':'Failed',
    'dash.welcome':'Start a transcription.','dash.subtitle':'Upload media for transcription, or import an existing subtitle file to generate notes directly.','dash.totalMin':'Total Minutes','dash.noteGen':'Notes Generated','dash.minUnit':'min','dash.docUnit':'docs','dash.proTag':'Ready','dash.heroTitle':'Drop a video here to transcribe it.','dash.heroDesc':'FluentFlow extracts audio, transcribes it in the background, and prepares transcript, subtitles, and notes.','dash.selectFile':'Select Audio/Video','dash.selectSubtitle':'Import subtitles to notes','dash.subtitleHint':'Drop audio/video to transcribe, or import SRT/VTT/TXT/MD to generate notes directly.','dash.processing':'Processing…','dash.dragHint':'Drop audio/video to transcribe, or import SRT/VTT/TXT/MD to generate notes directly.','dash.linkPlaceholder':'Paste Douyin share text or a video link','dash.linkSubmit':'Fetch by link','dash.linkSubmitting':'Fetching…','dash.linkEmpty':'Paste a share text or video link first.','dash.linkQueued':'Video link is being fetched. Progress stays visible in processing records.','dash.viewTasks':'View records','dash.cloudUploadHint':'Cloud transcription runs in the background. You can leave this page and watch it from processing records.','dash.uploading':'Uploading and processing','dash.done':'Processing complete','dash.subtitleDone':'Note generated from subtitle file','dash.viewEditor':'View in Editor','dash.recent':'Recent Activity','dash.viewAll':'View All','dash.fileError':'Unsupported format. Please select a video or audio file.','dash.subtitleFileError':'Unsupported transcript file. Please select SRT, VTT, TXT, or MD.','dash.noActivity':'No activity yet. Completed jobs will appear here.','dash.justNow':'just now','dash.mAgo':'m ago','dash.hAgo':'h ago','dash.dAgo':'d ago',
    'dash.statusCompleted':'Completed','dash.statusFailed':'Failed','dash.statusProcessing':'Processing','dash.cancel':'Cancel','dash.cancelTask':'Cancel task','dash.activeTask':'Active Task','dash.elapsed':'Elapsed','dash.fileSize':'File Size','dash.cloudUploadAudio':'Cloud Audio','dash.pipeline':'Pipeline','dash.modelProfile':'Route','dash.summaryMode':'Summary Mode','dash.summaryOn':'AI summary on','dash.summaryOff':'Transcript only','dash.exportOn':'Auto Lark export','dash.exportOff':'Manual export later','dash.currentStage':'Current Stage','dash.waitingForTranscript':'You can leave this page; progress continues in processing records.','dash.transcribedTo':'Transcribed','dash.waitingSegment':'Waiting for first transcript segment','dash.progressUnknown':'Working','dash.sttMeasuring':'STT measuring','dash.sttStarting':'Starting transcription engine','dash.sttLoadingModel':'Loading local model','dash.sttChunking':'Preparing progress tracking','dash.sttPreparingAudio':'Preparing audio features','dash.sttWaitingFirst':'Waiting for the first transcript segment','dash.sttChunks':'Transcribing audio','dash.sttSegments':'Receiving transcript segments','dash.sttCloud':'Cloud transcription in progress','dash.sttCloudUpload':'Uploading audio','dash.sttCloudSubmit':'Submitting cloud job','dash.sttCloudWait':'Waiting for cloud transcription','dash.sttCloudDownload':'Downloading cloud result','dash.sttNoProgressHint':'The first transcript segment has not been produced yet. Progress will advance once local transcription emits real segments.',
    'tasks.title':'History','tasks.subtitle':'Review previous materials, failed runs, outputs, and temporary active work.','tasks.refresh':'Refresh','tasks.open':'Open result','tasks.delete':'Delete','tasks.deleteConfirm':'Delete this record?','tasks.download':'Download','tasks.progress':'Progress','tasks.route':'Route','tasks.updated':'Updated','tasks.empty':'No history yet. Start with an upload from Start.','tasks.queued':'Queued','tasks.running':'Running','tasks.completed':'Completed','tasks.failed':'Failed','tasks.error':'Failure reason','tasks.source':'Source','tasks.summary':'Summary','tasks.detail':'Stage detail','tasks.artifacts':'Outputs','tasks.outputsReady':'Ready outputs','tasks.noOutputs':'Outputs appear here after completion.','tasks.larkDoc':'Lark doc','tasks.srt':'SRT','tasks.txt':'TXT','tasks.vtt':'VTT','tasks.bilingualSrt':'Bilingual SRT','tasks.bilingualVtt':'Bilingual VTT','tasks.md':'Summary',
    'proc.title':'Processing records','proc.subtitle':'See what the Agent will do, what it uses as evidence, and which preferences still come from Settings.','proc.noJob':'No active processing','proc.noJobDesc':'Upload a video from Start when you are ready.','proc.audioExtract':'Audio Extraction','proc.transcription':'Transcription','proc.aiSumm':'AI Summarization','proc.larkExport':'Lark Export','proc.waiting':'Waiting…','proc.running':'Running…','proc.done':'Done','proc.pipeline':'Pipeline Progress',
    'edit.title':'Editor','edit.noResult':'No result selected','edit.noResultDesc':'Choose a completed record, then review and edit its transcript and note here.','edit.chooseRecord':'Choose record','edit.transcript':'Full Transcript','edit.aiSummary':'AI Summary','edit.summaryPending':'AI summary is still generating.','edit.summarySkipped':'Transcript-only mode is enabled. Click Regenerate note when you need an AI summary.','edit.summaryFailed':'Transcript is saved, but AI summary failed. Click Regenerate note to try again.','edit.share':'Share','edit.export':'Export to Lark','edit.confidence':'AI Generated','edit.regenerate':'Regenerate note','edit.regenerating':'Regenerating…','edit.regenerateConfirmTitle':'Regenerate this note?','edit.regenerateConfirmDesc':'FluentFlow will regenerate the note from the current transcript and replace the note body. The transcript itself will not be retranscribed.','edit.regenerateConfirmAction':'Regenerate note','edit.retranscribe':'Retranscribe','edit.retranscribing':'Retranscribing…','edit.pickSourceAgain':'Choose source file','edit.retranscribeDone':'Retranscription complete','edit.retranscribeConfirmTitle':'Retranscribe this audio?','edit.retranscribeConfirmDesc':'FluentFlow will run STT again with the current Workbench settings and replace the transcript and summary for this result.','edit.retranscribeUnavailableTitle':'Source file is not available','edit.retranscribeUnavailableDesc':'Browsers cannot reopen a local file from history without your permission. Choose the original audio/video file to retranscribe it with current settings.','edit.retranscribeConfirmAction':'Start retranscription','edit.retranscribeChooseAction':'Choose original file','edit.cancel':'Cancel','edit.segments':'segments','edit.duration':'Duration','edit.sttElapsed':'Transcription time','edit.exportDone':'Export request sent','edit.exportFail':'Export failed','edit.regenDone':'Note regenerated','edit.clearHistory':'Clear History','edit.clearConfirm':'All history cleared','edit.clearConfirmAgain':'Click again to confirm','edit.copied':'Copied','edit.editedTranscript':'Edited transcript','edit.transcriptSaving':'Saving…','edit.transcriptSaved':'Saved','edit.transcriptSaveFailed':'Save failed','edit.editRecords':'Edit records','edit.editRecordsTitle':'Transcript edit records','edit.editRecordsDesc':'Each record keeps the changed sentence and nearby context. These records are saved locally with the edited transcript.','edit.editRecordsEmpty':'No changed segment has been recorded yet.','edit.before':'Before','edit.after':'After','edit.previousSentence':'Previous sentence','edit.nextSentence':'Next sentence','edit.followPlayback':'Follow playback','edit.audioUnavailable':'Choose the original audio/video to listen while editing.','edit.chooseAudio':'Choose source audio','edit.sourceLoading':'Loading source audio…',
    'prompt.label':'Prompt Template','prompt.select':'Select prompt style','prompt.customPlaceholder':'Enter your custom system prompt here...','prompt.expanded':'Collapse prompt','prompt.collapsed':'Change prompt','prompt.activeHint':'Active: ','prompt.editHint':'Edit prompt before regenerating','prompt.saveAsPreset':'Save custom as preset',
    'dl.transcript':'Export Transcript','dl.summary':'Download Summary','dl.txt':'Plain Text (.txt)','dl.md':'Markdown (.md)','dl.srt':'Source subtitles (.srt)','dl.vtt':'Source WebVTT (.vtt)','dl.bilingualSrt':'Bilingual subtitles (.srt)','dl.bilingualVtt':'Bilingual WebVTT (.vtt)','dl.sourceVideo':'Source video','dl.pdf':'PDF Document','dl.word':'Word Document (.docx)','dl.generating':'Generating…','dl.success':'Download started','dl.pdfPrintOpened':'Print dialog opened. Choose Save as PDF.',
    'set.title':'Settings','set.subtitle':'Keep template maintenance, export history, and app preferences here.','set.larkTitle':'Lark / Feishu Credentials','set.larkDesc':'Store credentials only. Export behavior now lives in Run Settings.','set.autoExport':'Auto-export to Lark after processing','set.larkExportRoute':'Lark export route','set.larkRouteOpenapi':'Feishu app export','set.larkRouteOpenapiHint':'Uses backend-configured Feishu OpenAPI credentials. Recommended for cloud product use.','set.larkRouteLocalCli':'Local identity export','set.larkRouteLocalCliHint':'Uses the local lark-cli login available to the backend process. Best for personal desktop automation.','set.larkViaCli':'Export via local lark-cli (My Library)','set.larkViaCliHint':'Uses your lark-cli login; App ID not required. Backend must run lark-cli on PATH.','set.larkHistory':'Export History','set.sttProvider':'Transcription Route','set.providerLocal':'Local transcription','set.providerCloud':'Cloud transcription','set.sttLanguage':'Audio Language','set.langAuto':'Auto detect','set.langZh':'Chinese','set.langEn':'English','set.sttSpeed':'Transcription Speed','set.speedFast':'Fast','set.speedBalanced':'Balanced','set.speedAccurate':'Accurate','set.intelligence':'Intelligence','set.skipSummary':'Transcript-only mode','set.skipSummaryHint':'Skip AI summary after audio transcription. Subtitle import always generates a note.','set.provider':'Provider','set.aiModel':'AI Model','set.openaiKey':'OpenAI API Key','set.deepseekKey':'DeepSeek API Key','set.dashscopeKey':'Bailian / DashScope API Key','set.prefs':'App Preferences','set.theme':'Interface Theme','set.light':'Light','set.dark':'Dark','set.saved':'Saved!','set.saveAll':'Save All Changes','set.promptTitle':'Prompt Template Library','set.promptDesc':'Edit reusable prompt templates here. Choose the active default in Run Settings.','set.defaultPrompt':'Default Prompt','set.templateToEdit':'Template to edit','set.editCoursePrompt':'Edit “General Study Notes” system prompt','set.editBuiltinTemplate':'Edit this template','set.resetBuiltinPrompt':'Reset to built-in default','set.deleteBuiltinPrompt':'Delete this template category','set.deleteBuiltinPromptConfirm':'Delete this template category (remove it from the UI)?','set.myPresets':'Saved presets','set.presetNamePh':'Preset name','set.saveAsPreset':'Save as preset','set.deletePreset':'Delete','set.deletePresetConfirm':'Delete this saved preset?','set.presetSaved':'Preset saved',
    'work.defaults':'Run defaults','work.defaultsDesc':'These values are used by Dashboard uploads, subtitle imports, and editor reruns.','work.activePrompt':'Default prompt template','work.transcription':'Transcription','work.close':'Close','work.summary':'Summary AI','work.summaryMode':'Note generation mode','work.noteModeAuto':'Auto switches by transcript length: direct under about 20k chars, high-fidelity above it.','work.noteModeDirect':'Sends the transcript in one pass. Faster, best for shorter materials.','work.noteModeHighFidelity':'Extracts evidence in chunks, then writes and checks coverage. Slower, better for long courses.','work.export':'Feishu export','work.currentRun':'Current run','work.activeRunHint':'Dashboard now shows the detailed live progress. Workbench stays focused on run defaults.','work.viewProgress':'View progress','work.saved':'Saved automatically','work.credentialsLink':'Credentials stay in Settings',
  },
  zh:{
    'nav.subtitle':'视频转飞书 AI','nav.dashboard':'开始处理','nav.processing':'处理记录','nav.editor':'编辑器','nav.settings':'设置','nav.admin':'管理','nav.newProject':'新建项目','nav.search':'搜索项目…','nav.projects':'项目','nav.integrations':'集成',
    'status.ready':'系统就绪','status.idle':'等待任务','status.queued':'排队中','status.resolving':'解析链接中…','status.downloading':'下载视频中…','status.saving':'保存视频中…','status.upload':'上传中…','status.audio':'音频提取中…','status.stt':'转录中…','status.translation':'正在翻译字幕…','status.transcript_ready':'转录已完成','status.summary':'AI 摘要中…','status.export':'导出到飞书…','status.done':'完成','status.failed':'失败',
    'dash.welcome':'开始一次转录','dash.subtitle':'上传音视频做转录，也可以直接导入已有字幕生成笔记。','dash.totalMin':'累计时长','dash.noteGen':'已生成笔记','dash.minUnit':'分钟','dash.docUnit':'份','dash.proTag':'就绪','dash.heroTitle':'把视频拖到这里开始转录。','dash.heroDesc':'FluentFlow 会自动提取音频，在后台转录，并生成转录文本、字幕和结构化笔记。','dash.selectFile':'选择音视频','dash.selectSubtitle':'导入字幕生成笔记','dash.subtitleHint':'拖放音视频开始转录；也可导入 SRT/VTT/TXT/MD 直接生成笔记。','dash.processing':'处理中…','dash.dragHint':'拖放音视频开始转录；也可导入 SRT/VTT/TXT/MD 直接生成笔记。','dash.linkPlaceholder':'粘贴抖音分享文本或视频链接','dash.linkSubmit':'通过链接获取','dash.linkSubmitting':'获取中…','dash.linkEmpty':'请先粘贴分享文本或视频链接。','dash.linkQueued':'视频链接正在获取中，进度会显示在处理记录里。','dash.viewTasks':'查看记录','dash.cloudUploadHint':'云端转录会在后台继续运行，你可以离开本页并在处理记录里查看进度。','dash.uploading':'正在上传并处理','dash.done':'处理完成','dash.subtitleDone':'已根据字幕文件生成笔记','dash.viewEditor':'在编辑器中查看','dash.recent':'最近活动','dash.viewAll':'查看全部','dash.fileError':'不支持的格式，请选择视频或音频文件。','dash.subtitleFileError':'不支持的字幕/转录文件，请选择 SRT、VTT、TXT 或 MD。','dash.noActivity':'暂无活动记录，完成的任务会显示在这里。','dash.justNow':'刚刚','dash.mAgo':'分钟前','dash.hAgo':'小时前','dash.dAgo':'天前',
    'dash.statusCompleted':'已完成','dash.statusFailed':'失败','dash.statusProcessing':'处理中','dash.cancel':'取消','dash.cancelTask':'取消任务','dash.activeTask':'当前任务','dash.elapsed':'已用时间','dash.fileSize':'文件大小','dash.cloudUploadAudio':'云端音频','dash.pipeline':'处理流水线','dash.modelProfile':'转录路线','dash.summaryMode':'摘要模式','dash.summaryOn':'生成 AI 摘要','dash.summaryOff':'仅转录','dash.exportOn':'自动导出飞书','dash.exportOff':'完成后手动导出','dash.currentStage':'当前阶段','dash.waitingForTranscript':'你可以离开本页，进度会在记录里继续更新。','dash.transcribedTo':'已转录','dash.waitingSegment':'等待第一段转录结果','dash.progressUnknown':'处理中','dash.sttMeasuring':'STT 计算中','dash.sttStarting':'正在启动转录引擎','dash.sttLoadingModel':'正在加载本地模型','dash.sttChunking':'正在准备进度追踪','dash.sttPreparingAudio':'正在准备音频特征','dash.sttWaitingFirst':'等待第一段转录结果','dash.sttChunks':'正在转录音频','dash.sttSegments':'正在接收转录片段','dash.sttCloud':'云端转录中','dash.sttCloudUpload':'正在上传音频','dash.sttCloudSubmit':'正在提交云端任务','dash.sttCloudWait':'等待云端转录','dash.sttCloudDownload':'正在下载云端结果','dash.sttNoProgressHint':'第一段转录结果还没有产出。后续会按本地转录真实返回的片段推进进度。',
    'tasks.title':'历史记录','tasks.subtitle':'查看过去的材料、失败记录、结果产物和临时进行中的任务。','tasks.refresh':'刷新','tasks.open':'打开结果','tasks.delete':'删除记录','tasks.deleteConfirm':'删除这条记录？','tasks.download':'下载','tasks.progress':'进度','tasks.route':'路线','tasks.updated':'更新于','tasks.empty':'暂无历史记录。从开始处理页上传文件后会出现在这里。','tasks.queued':'排队中','tasks.running':'处理中','tasks.completed':'已完成','tasks.failed':'失败','tasks.error':'失败原因','tasks.source':'来源','tasks.summary':'摘要','tasks.detail':'阶段详情','tasks.artifacts':'结果产物','tasks.outputsReady':'可下载产物','tasks.noOutputs':'完成后会在这里显示下载入口。','tasks.larkDoc':'飞书文档','tasks.srt':'SRT','tasks.txt':'TXT','tasks.vtt':'VTT','tasks.bilingualSrt':'双语 SRT','tasks.bilingualVtt':'双语 VTT','tasks.md':'摘要',
    'proc.title':'处理记录','proc.subtitle':'这里展示 Agent 会做什么、依据什么判断，以及哪些长期偏好来自设置页。','proc.noJob':'当前没有任务','proc.noJobDesc':'从开始处理页上传文件。','proc.audioExtract':'音频提取','proc.transcription':'语音转录','proc.aiSumm':'AI 摘要','proc.larkExport':'飞书导出','proc.waiting':'等待中…','proc.running':'运行中…','proc.done':'完成','proc.pipeline':'流水线进度',
    'edit.title':'编辑器','edit.noResult':'未选择结果','edit.noResultDesc':'从处理记录选择一条已完成结果后，在这里复查和编辑转录与笔记。','edit.chooseRecord':'选择处理记录','edit.transcript':'完整转录','edit.aiSummary':'AI 摘要','edit.summaryPending':'AI 摘要仍在生成中。','edit.summarySkipped':'当前是仅转录模式，未生成 AI 摘要。需要时可点击重生笔记。','edit.summaryFailed':'转录已保存，但 AI 摘要失败。可以点击重生笔记再试一次。','edit.share':'分享','edit.export':'导出到飞书','edit.confidence':'AI 生成','edit.regenerate':'重生笔记','edit.regenerating':'重生中…','edit.regenerateConfirmTitle':'重生当前笔记？','edit.regenerateConfirmDesc':'FluentFlow 会基于当前转录重生笔记，并替换右侧笔记正文；不会重新转录音频。','edit.regenerateConfirmAction':'确认重生笔记','edit.retranscribe':'重新转录','edit.retranscribing':'重新转录中…','edit.pickSourceAgain':'选择原文件','edit.retranscribeDone':'重新转录完成','edit.retranscribeConfirmTitle':'重新转录当前音频？','edit.retranscribeConfirmDesc':'FluentFlow 会使用当前工作台设置重新执行 STT，并替换当前结果里的转录文本和摘要。','edit.retranscribeUnavailableTitle':'当前没有可直接重转的原文件','edit.retranscribeUnavailableDesc':'浏览器不会在历史记录里长期保留本地音视频文件权限。请选择原始音视频文件，再用当前设置重新转录。','edit.retranscribeConfirmAction':'确认重新转录','edit.retranscribeChooseAction':'选择原始文件','edit.cancel':'取消','edit.segments':'段','edit.duration':'时长','edit.sttElapsed':'转录耗时','edit.exportDone':'导出请求已发送','edit.exportFail':'导出失败','edit.regenDone':'笔记已重生','edit.clearHistory':'清除记录','edit.clearConfirm':'所有记录已清除','edit.clearConfirmAgain':'再次点击确认','edit.copied':'已复制','edit.editedTranscript':'已修改转录','edit.transcriptSaving':'保存中…','edit.transcriptSaved':'已保存','edit.transcriptSaveFailed':'保存失败','edit.editRecords':'修改记录','edit.editRecordsTitle':'转录稿修改记录','edit.editRecordsDesc':'每条记录会保留修改句子和相邻上下文，并随编辑稿一起保存到本地。','edit.editRecordsEmpty':'还没有记录到分段修改。','edit.before':'修改前','edit.after':'修改后','edit.previousSentence':'上一句','edit.nextSentence':'下一句','edit.followPlayback':'跟随播放','edit.audioUnavailable':'选择原始音视频后，可边听边校对。','edit.chooseAudio':'选择原音频','edit.sourceLoading':'正在读取原音频…',
    'prompt.label':'提示词模板','prompt.select':'选择提示词风格','prompt.customPlaceholder':'在此输入自定义系统提示词…','prompt.expanded':'收起提示词','prompt.collapsed':'更换提示词','prompt.activeHint':'当前：','prompt.editHint':'重生笔记前可编辑提示词','prompt.saveAsPreset':'将自定义保存为预设',
    'dl.transcript':'导出转录文本','dl.summary':'下载摘要','dl.txt':'纯文本 (.txt)','dl.md':'Markdown (.md)','dl.srt':'原文字幕 (.srt)','dl.vtt':'原文 WebVTT (.vtt)','dl.bilingualSrt':'中英双语字幕 (.srt)','dl.bilingualVtt':'中英双语 WebVTT (.vtt)','dl.sourceVideo':'原视频','dl.pdf':'PDF 文档','dl.word':'Word 文档 (.docx)','dl.generating':'生成中…','dl.success':'已开始下载','dl.pdfPrintOpened':'已打开系统打印，可选择另存为 PDF。',
    'set.title':'设置','set.subtitle':'这里只保留模板维护、导出历史和应用偏好。','set.larkTitle':'飞书凭证','set.larkDesc':'这里只保存连接凭证；是否自动导出等处理偏好由设置页维护。','set.autoExport':'处理完成后自动导出到飞书','set.larkExportRoute':'飞书导出路线','set.larkRouteOpenapi':'飞书应用导出','set.larkRouteOpenapiHint':'使用后台统一配置的飞书 OpenAPI 凭证，适合线上产品和普通用户。','set.larkRouteLocalCli':'本机身份导出','set.larkRouteLocalCliHint':'使用后端进程可调用的本机 lark-cli 和当前登录身份，适合个人桌面自动化。','set.larkViaCli':'用本机 lark-cli 导出到「我的文档库」','set.larkViaCliHint':'使用你已登录的 lark-cli，无需填 App 凭证；后端进程需能调用本机 PATH 上的 lark-cli。','set.larkHistory':'导出记录','set.sttProvider':'转录路线','set.providerLocal':'本地转录','set.providerCloud':'云端转录','set.sttLanguage':'音频语言','set.langAuto':'自动识别','set.langZh':'中文','set.langEn':'英文','set.sttSpeed':'转录速度','set.speedFast':'快速','set.speedBalanced':'均衡','set.speedAccurate':'高准确率','set.intelligence':'AI 智能','set.skipSummary':'仅转录模式','set.skipSummaryHint':'音视频转录后跳过 AI 摘要；字幕导入入口始终生成笔记。','set.provider':'服务商','set.aiModel':'AI 模型','set.openaiKey':'OpenAI API Key','set.deepseekKey':'DeepSeek API Key','set.dashscopeKey':'百炼 / DashScope API Key','set.prefs':'应用偏好','set.theme':'界面主题','set.light':'浅色','set.dark':'暗色','set.saved':'已保存！','set.saveAll':'保存所有更改','set.promptTitle':'提示词模板库','set.promptDesc':'在这里维护可复用提示词，并选择默认模板。','set.defaultPrompt':'默认提示词','set.templateToEdit':'要编辑的模板','set.editCoursePrompt':'编辑「通用学习笔记」系统提示词','set.editBuiltinTemplate':'编辑该模板内容','set.resetBuiltinPrompt':'恢复为内置默认','set.deleteBuiltinPrompt':'删除该模板类目','set.deleteBuiltinPromptConfirm':'确定删除该模板类目（从界面移除）？','set.myPresets':'已保存的预设','set.presetNamePh':'预设名称','set.saveAsPreset':'保存为预设','set.deletePreset':'删除','set.deletePresetConfirm':'确定删除该保存的预设？','set.presetSaved':'已保存预设',
    'work.defaults':'处理默认值','work.defaultsDesc':'仪表盘上传、字幕导入、编辑器重生笔记都会使用这些设置。','work.activePrompt':'默认提示词模板','work.transcription':'转录','work.close':'关闭','work.summary':'摘要 AI','work.summaryMode':'笔记生成模式','work.noteModeAuto':'按转录长度自动切换：约 2 万字以内直接生成，超过后用高保真模式。','work.noteModeDirect':'整段一次发送给模型，速度更快，适合较短材料。','work.noteModeHighFidelity':'先分段提取证据，再成文并检查覆盖率，耗时更久，适合长课程。','work.export':'飞书导出','work.currentRun':'当前任务','work.activeRunHint':'主页已经展示更完整的实时进度，工作台只保留本次运行参数。','work.viewProgress':'查看进度','work.saved':'自动保存','work.credentialsLink':'凭证仍在设置页',
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

export {AuthCtx, useAuth} from './auth.jsx';

export { accountJobsCacheKey, readCachedAccountJobs, writeCachedAccountJobs, cacheJobRecord, mergeCachedJobs, sortJobsForHistoryView, hasTranscriptResult, historyStatusFromJob, jobVisibleInHistory, resultDisplayTitle, jobDisplayTitle, resultToHistoryEntry, jobToHistoryEntry, jobToCurrentJob, historyEntryToResult } from '../lib/jobMappers.js';

export { fmtTime, autoSizeTextarea, composeTranscriptText, normalizeTranscriptSegments, normalizeDisplaySegments, pickTranscriptSegments, pickTranscriptBaselineSegments, pickDisplayTranscriptSegments, buildTranscriptEditRecords, fmtElapsed, fmtFileSize, totalFileSizeMb, fmtBytes, fmtDateTime, friendlyTaskError, diagnoseTaskError, fmtSttRelative, sttStatusLabel, sttProgressFraction, isSttProgressUnmeasured, jobProgressLabel, timeAgo, noteGenerationDiagnosis } from '../lib/format.js';

export { MD_TABLE_ALIGN_RE, splitMdTableRow, isPipeTableRow, looksLikeMdTable, looksLikeLoosePipeTable, renderTableHtml, simpleMd } from '../lib/markdown.js';

export { _dl, _baseName, _fmtSrtTime, _fmtVttTime, dlTranscriptTxt, dlTranscriptSrt, dlTranscriptVtt, dlBilingualTranscriptSrt, dlBilingualTranscriptVtt, dlSummaryTxt, dlSummaryMd, dlSummaryWord, dlSummaryPdf, dlSummaryImage } from '../lib/download.js';

export {DropdownMenu} from './DropdownMenu.jsx';

/* ═══════════════ hooks ═══════════════ */
export const useApi = () => {
    const appendAiOptions = (fd, options={}) => {
        if(options.aiProvider) fd.append("ai_provider", options.aiProvider);
        if(options.aiModel) fd.append("ai_model", options.aiModel);
        if(options.systemPrompt) fd.append("system_prompt", options.systemPrompt);
        if(options.noteMode) fd.append("note_mode", options.noteMode);
        if(options.promptPreset) fd.append("prompt_preset", options.promptPreset);
        if(options.promptPresetLabel) fd.append("prompt_preset_label", options.promptPresetLabel);
        if(options.generateVisuals) fd.append("generate_visuals", "true");
    };
    const appendProcessOptions = (fd, options={}) => {
        if(options.exportToLark) {
            fd.append("export_to_lark","true");
            const larkRoute = normalizeLarkExportRoute(options.larkExportRoute, !!options.larkViaCli);
            fd.append("lark_export_route", larkRoute);
            fd.append("lark_via_cli", isLocalLarkExportRoute(larkRoute) ? "true" : "false");
        }
        if(options.title) fd.append("title", options.title);
        if(options.folderToken) fd.append("folder_token", options.folderToken); // kept for future use
        if(options.skipSummary) fd.append("skip_summary", "true");
        appendAiOptions(fd, options);
        if(options.sttProvider) fd.append("stt_provider", options.sttProvider);
        if(options.sttModel) fd.append("stt_model", options.sttModel);
        if(options.sttSpeed) fd.append("stt_speed", options.sttSpeed);
        if(options.sttLanguage) fd.append("stt_language", options.sttLanguage);
        if(options.speakerDiarization) fd.append("speaker_diarization", "true");
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
        // The direct-upload transport is registered by the hosted composition
        // root only; without one (local edition) uploads always take the
        // regular queue path below.
        const directUpload = options.directOssUpload ? getDirectUploadTransport() : null;
        if (directUpload) {
            return directUpload({
                files,
                options,
                apiBase: API_BASE,
                fetcher: apiFetch,
                normalizeLarkRoute: normalizeLarkExportRoute,
                isLocalLarkRoute: isLocalLarkExportRoute,
                onProgress,
                signal,
                stallMs,
            });
        }
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
        const payloadOptions = {};
        if(options.exportToLark) {
            const larkRoute = normalizeLarkExportRoute(options.larkExportRoute, !!options.larkViaCli);
            payloadOptions.export_to_lark = "true";
            payloadOptions.lark_export_route = larkRoute;
            payloadOptions.lark_via_cli = isLocalLarkExportRoute(larkRoute) ? "true" : "false";
        }
        if(options.title) payloadOptions.title = options.title;
        if(options.skipSummary) payloadOptions.skip_summary = "true";
        if(options.aiProvider) payloadOptions.ai_provider = options.aiProvider;
        if(options.aiModel) payloadOptions.ai_model = options.aiModel;
        if(options.systemPrompt) payloadOptions.system_prompt = options.systemPrompt;
        if(options.noteMode) payloadOptions.note_mode = options.noteMode;
        if(options.promptPreset) payloadOptions.prompt_preset = options.promptPreset;
        if(options.promptPresetLabel) payloadOptions.prompt_preset_label = options.promptPresetLabel;
        if(options.generateVisuals) payloadOptions.generate_visuals = "true";
        if(options.sttProvider) payloadOptions.stt_provider = options.sttProvider;
        if(options.sttModel) payloadOptions.stt_model = options.sttModel;
        if(options.sttSpeed) payloadOptions.stt_speed = options.sttSpeed;
        if(options.sttLanguage) payloadOptions.stt_language = options.sttLanguage;
        if(options.speakerDiarization) payloadOptions.speaker_diarization = "true";
        if(options.cookiesFromBrowser) payloadOptions.cookies_from_browser = options.cookiesFromBrowser;
        const r = await apiFetch(`${API_BASE}/video-sources/jobs`, {
            method:"POST",
            headers: {"Content-Type":"application/json", ...localExecutionHeaders(options)},
            body: JSON.stringify({input, options: payloadOptions}),
            signal,
        });
        const data = await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(apiErrorMessage(data, `HTTP ${r.status}`));
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
    const getJobs = async (limit=100, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs?limit=${encodeURIComponent(limit)}`, {
            headers: localExecutionHeaders(options),
        });
        if(!r.ok) throw new Error('Jobs unavailable');
        const data = await r.json();
        return Array.isArray(data?.jobs) ? data.jobs : [];
    };
    const downloadJobArtifact = async (taskId, kind, filename, options={}) => {
        const r = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(kind)}`, {
            headers: localExecutionHeaders(options),
        });
        if(!r.ok) throw new Error('Artifact not found');
        const blob = await r.blob();
        _dl(blob, filename || `${kind}.txt`);
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
    const checkHealth = async () => { try{ const r = await apiFetch(`${API_BASE}/health`); return r.ok ? await r.json() : false;}catch(_){return false;} };
    // Local-safe API methods available in every edition.
    const baseMethods = {processVideoSSE, enqueueProcessFiles, createVideoSourceJob, checkVideoCookies, subscribeJobEvents, summarizeTranscriptFile, recordEvent, getJob, cancelJob, deleteJob, retryJob, getJobs, fetchJobSourceFile, fetchJobArtifactFile, uploadJobPlaybackAudio, downloadJobArtifact, saveTranscriptEdit, saveSummaryEdit, translateJobSegments, getCredentialsStatus, saveCredentials, getSpeakerDiarizationStatus, checkHealth};
    // The hosted composition root registers the hosted-only fetch helpers
    // (guest trial, account quota, admin, hosted Feishu OAuth, desktop sync).
    // The local edition registers nothing, so those methods stay absent and the
    // hosted route strings never enter this shared module.
    const hostedExtension = getHostedApiExtension();
    const hostedMethods = hostedExtension
        ? hostedExtension({apiFetch, API_BASE, apiErrorMessage, readSseResult, appendAiOptions})
        : {};
    return {...baseMethods, ...hostedMethods};
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
