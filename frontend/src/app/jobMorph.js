export { accountJobsCacheKey, readCachedAccountJobs, writeCachedAccountJobs, cacheJobRecord, mergeCachedJobs, reconcileTaskList, entryToJob, taskKeyForJob, sortJobsForHistoryView, hasTranscriptResult, historyStatusFromJob, jobVisibleInHistory, resultDisplayTitle, jobDisplayTitle, resultToHistoryEntry, jobToHistoryEntry, jobToCurrentJob, historyEntryToResult } from '../lib/jobMappers.js';
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
    isCloudSttProvider,
    isCloudSttConfigured,
    defaultRuntimeConfig,
    normalizeRuntimeConfig,
    effectiveSttProvider,
    cloudSttMissingMessage,
} from '../lib/sttPolicy.js';

export const createTaskId = () => (
    window.crypto?.randomUUID?.() ||
    `task_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
);
