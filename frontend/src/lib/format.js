// Only real media/subtitle/document extensions are removed. Recordings are often
// named "5.投资人视角下的AI浪潮", and treating whatever follows the last dot as an
// extension cut such titles down to "5". Keep in step with KNOWN_SUFFIXES in
// backend/core/title_display.py.
const KNOWN_SUFFIX_RE = /\.(?:mp4|mov|m4v|mkv|webm|avi|flv|wmv|mpg|mpeg|ts|mp3|wav|flac|aac|ogg|m4a|wma|opus|aiff|aif|srt|vtt|ass|txt|md|docx|pdf)$/i;
export const fileNameStem = (name) => String(name || "").replace(KNOWN_SUFFIX_RE, "") || "";
export const stripGeneratedFilenamePrefix = (name) => String(name || '').replace(/^(?:[0-9]{10,24}|BV[a-zA-Z0-9]{8,})[-_]+/, '');
export const displayTitleForUser = (value, fallback='') => {
    const clean = stripGeneratedFilenamePrefix(fileNameStem(value)).trim();
    if (clean) return clean;
    return stripGeneratedFilenamePrefix(fileNameStem(fallback)).trim();
};
export const videoLinkDisplayTitle = (value, lang='zh') => {
    const raw = String(value || '').trim();
    const match = raw.match(/https?:\/\/[^\s，。！？、'"“”‘’）)\]】]+/i);
    if (!match) return displayTitleForUser(raw, raw) || (lang === 'zh' ? '视频链接' : 'Video link');
    const url = match[0].replace(/[)）\]】"'“”‘’。，,]+$/g, '');
    try {
        const parsed = new URL(url);
        const host = parsed.hostname.replace(/^www\./, '').toLowerCase();
        const parts = parsed.pathname.split('/').filter(Boolean);
        const bv = parts.find((part) => /^BV[a-zA-Z0-9]+$/.test(part));
        if (host.includes('bilibili.com') || host === 'b23.tv') {
            return bv
                ? (lang === 'zh' ? `Bilibili 视频 ${bv}` : `Bilibili video ${bv}`)
                : (lang === 'zh' ? 'Bilibili 视频' : 'Bilibili video');
        }
        if (host.includes('douyin.com')) return lang === 'zh' ? '抖音视频链接' : 'Douyin video link';
        if (host.includes('youtube.com') || host.includes('youtu.be')) return lang === 'zh' ? 'YouTube 视频' : 'YouTube video';
        if (host) return lang === 'zh' ? `${host} 视频链接` : `${host} video link`;
    } catch(_) {}
    return lang === 'zh' ? '视频链接' : 'Video link';
};
export const compactDisplayFilename = (name, maxChars=42) => {
    const value = displayTitleForUser(name, name) || String(name || '').trim();
    const chars = Array.from(value);
    if (chars.length <= maxChars) return value;
    const extMatch = value.match(/(\.[^./\s]{1,8})$/);
    const ext = extMatch ? extMatch[1] : '';
    const extLength = Array.from(ext).length;
    const keep = Math.max(16, maxChars - extLength - 1);
    return `${chars.slice(0, keep).join('')}…${ext}`;
};

export const fmtTime = (sec) => { const m=Math.floor(sec/60); const s=Math.floor(sec%60); return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`; };
export const autoSizeTextarea = (node) => {
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${node.scrollHeight}px`;
};
export const composeTranscriptText = (segments, fallback='') => (
    Array.isArray(segments) && segments.length > 0
        ? segments.map((seg) => (seg?.text || '').trim()).filter(Boolean).join('\n')
        : (fallback || '')
);
export const normalizeTranscriptSegments = (value) => (
    Array.isArray(value)
        ? value
            .filter((seg) => seg && typeof seg === 'object' && String(seg.text || '').trim())
            .map((seg) => ({...seg, text: String(seg.text || '')}))
        : []
);
export const normalizeDisplaySegments = (value) => (
    Array.isArray(value)
        ? value
            .filter((seg) => seg && typeof seg === 'object' && (String(seg.text || '').trim() || String(seg.text_zh || seg.zh || '').trim()))
            .map((seg) => ({
                ...seg,
                text: String(seg.text || seg.text_en || ''),
                ...(String(seg.text_zh || seg.zh || '').trim() ? {text_zh: String(seg.text_zh || seg.zh || '')} : {}),
            }))
        : []
);
export const pickTranscriptSegments = (source={}) => {
    for (const key of ['raw_segments', 'segments', 'cleaned_segments']) {
        const segments = normalizeTranscriptSegments(source?.[key]);
        if (segments.length > 0) return segments;
    }
    return [];
};
// The baseline for edit records is the same pick: the raw segments when the
// result still has them, else whatever segments it carries.
export const pickTranscriptBaselineSegments = pickTranscriptSegments;
export const pickDisplayTranscriptSegments = (source={}, rawSegments=[]) => {
    for (const key of ['display_segments', 'bilingual_segments']) {
        const segments = normalizeDisplaySegments(source?.[key]);
        if (segments.length > 0) return segments;
    }
    const translated = normalizeDisplaySegments(source?.translated_segments_zh);
    const raw = Array.isArray(rawSegments) && rawSegments.length > 0 ? rawSegments : pickTranscriptSegments(source);
    if (raw.length > 0 && translated.length > 0) {
        return raw.map((segment, index) => {
            const textZh = String(translated[index]?.text_zh || translated[index]?.text || '').trim();
            return textZh ? {...segment, text_zh: textZh} : {...segment};
        }).filter((segment) => String(segment.text || '').trim() || String(segment.text_zh || '').trim());
    }
    return normalizeDisplaySegments(raw);
};
export const buildTranscriptEditRecords = (beforeSegments=[], afterSegments=[], source={}) => {
    if (!Array.isArray(beforeSegments) || !Array.isArray(afterSegments) || beforeSegments.length === 0) return source?.transcript_edit_records || [];
    const now = new Date().toISOString();
    const limit = Math.max(beforeSegments.length, afterSegments.length);
    const records = [];
    for (let i = 0; i < limit; i += 1) {
        const before = beforeSegments[i] || {};
        const after = afterSegments[i] || {};
        const beforeText = String(before.text || '').trim();
        const afterText = String(after.text || '').trim();
        if (!beforeText && !afterText) continue;
        if (beforeText === afterText) continue;
        records.push({
            index: i,
            start: Number(before.start ?? after.start ?? 0) || 0,
            end: Number(before.end ?? after.end ?? before.start ?? after.start ?? 0) || 0,
            before: beforeText,
            after: afterText,
            previous_before: String(beforeSegments[i - 1]?.text || '').trim(),
            next_before: String(beforeSegments[i + 1]?.text || '').trim(),
            previous_after: String(afterSegments[i - 1]?.text || '').trim(),
            next_after: String(afterSegments[i + 1]?.text || '').trim(),
            created_at: now,
        });
    }
    return records;
};
export const fmtElapsed = (sec) => {
    const n = Math.max(0, Number(sec) || 0);
    const h = Math.floor(n / 3600);
    const m = Math.floor((n % 3600) / 60);
    const s = Math.floor(n % 60);
    return h > 0
        ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
        : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
};
export const fmtDurationCompact = (sec) => {
    const n = Math.max(0, Number(sec) || 0);
    const h = Math.floor(n / 3600);
    const m = Math.floor((n % 3600) / 60);
    const s = Math.floor(n % 60);
    if (h > 0) return `${h}h ${m}m ${s}s`;
    return `${m}m ${s}s`;
};
export const fmtFileSize = (mb) => {
    const n = Number(mb);
    if(!Number.isFinite(n) || n <= 0) return '-';
    if(n >= 1024) return `${(n/1024).toFixed(n >= 10240 ? 0 : 1)} GB`;
    return `${n.toFixed(n >= 10 ? 1 : 2)} MB`;
};
export const totalFileSizeMb = (files=[]) => (
    Math.round(Array.from(files || []).reduce((sum, file) => sum + (Number(file?.size) || 0), 0) / 1024 / 1024 * 1000) / 1000
);
export const fmtBytes = (bytes) => {
    const n = Number(bytes);
    if(!Number.isFinite(n) || n <= 0) return '';
    return fmtFileSize(n / 1024 / 1024);
};
export const fmtDateTime = (value, lang='zh') => {
    const ts = Date.parse(value || '');
    if(!Number.isFinite(ts)) return '-';
    try {
        return new Date(ts).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    } catch(_) {
        return '-';
    }
};
const taskErrorDiagnosis = ({code, titleZh, titleEn, detailZh, detailEn, nextZh, nextEn, severity='error', retryable=true}) => ({
    code,
    title: {zh: titleZh, en: titleEn},
    detail: {zh: detailZh, en: detailEn},
    nextAction: {zh: nextZh, en: nextEn},
    severity,
    retryable,
});

const PROVIDER_NAMES = {
    deepseek: {zh: 'DeepSeek', en: 'DeepSeek'},
    openai: {zh: 'OpenAI', en: 'OpenAI'},
    qwen: {zh: '百炼 / DashScope', en: 'Bailian / DashScope'},
    dashscope: {zh: '百炼 / DashScope', en: 'Bailian / DashScope'},
    anthropic: {zh: 'Anthropic', en: 'Anthropic'},
    claude: {zh: 'Anthropic', en: 'Anthropic'},
};
const normalizeProviderKey = (value) => {
    const key = String(value || '').trim().toLowerCase();
    return PROVIDER_NAMES[key] ? key : '';
};
// Which AI service a failure is about: the caller's context first (the job
// records which provider it asked), then whatever the message itself names.
const providerFromError = (raw, context={}) => {
    const explicit = normalizeProviderKey(context?.provider);
    if (explicit) return explicit;
    const lower = String(raw || '').toLowerCase();
    for (const key of ['deepseek', 'openai', 'dashscope', 'qwen', 'anthropic', 'claude']) {
        if (lower.includes(key)) return key;
    }
    if (lower.includes('百炼') || lower.includes('通义')) return 'qwen';
    return '';
};
export const providerDisplayName = (provider, lang='zh') => {
    const key = normalizeProviderKey(provider);
    if (!key) return lang === 'zh' ? 'AI 服务' : 'the AI service';
    return lang === 'zh' ? PROVIDER_NAMES[key].zh : PROVIDER_NAMES[key].en;
};
// Whether the failing job fetched its material from a video platform. Only then
// is "the download timed out, upload the file instead" advice that fits; for a
// local recording there was no download, so the same words send the user after
// the wrong subsystem.
const isVideoLinkContext = (context={}) => (
    String(context?.sourceType || context?.source_type || '').trim().toLowerCase() === 'video_link'
);
// Context the caller knows about the failed task and the message cannot carry:
// `provider` (deepseek / openai / qwen / dashscope / anthropic) and `sourceType`
// (video_link / video_file / audio_file / transcript_file / queue_upload).
export const diagnoseTaskError = (message, lang='zh', context={}) => {
    const raw = String(message || '').trim();
    const zh = lang === 'zh';
    const provider = providerFromError(raw, context);
    const providerName = providerDisplayName(provider, lang);
    const videoLink = isVideoLinkContext(context);
    const pick = (diag) => ({
        code: diag.code,
        severity: diag.severity || 'error',
        title: zh ? diag.title.zh : diag.title.en,
        detail: zh ? diag.detail.zh : diag.detail.en,
        nextAction: zh ? diag.nextAction.zh : diag.nextAction.en,
        retryable: diag.retryable !== false,
        raw,
    });
    if(!raw) return pick(taskErrorDiagnosis({
        code: 'unknown_error',
        titleZh: '任务处理失败',
        titleEn: 'Task failed',
        detailZh: '处理失败，但没有返回具体原因。请重试一次。',
        detailEn: 'The task failed without a specific reason. Try again.',
        nextZh: '重新提交任务；如果连续失败，请把任务详情发给维护者排查。',
        nextEn: 'Submit again; if it keeps failing, send the task detail to the maintainer.',
    }));
    const lower = raw.toLowerCase();

    const patterns = [
        [
            lower.includes('failed to fetch') || lower.includes('networkerror') || lower.includes('network error') || lower.includes('load failed') || lower.includes('err_connection_refused') || lower.includes('econnrefused'),
            taskErrorDiagnosis({
                code: 'backend_unreachable',
                titleZh: '本机服务没有响应',
                titleEn: 'The local service is not responding',
                detailZh: '本机服务没有响应，请重新打开 FluentFlow Local；任务和记录都还在。',
                detailEn: 'The local service is not responding. Reopen FluentFlow Local; your tasks and records are still there.',
                nextZh: '重新打开 FluentFlow Local 后再试一次。',
                nextEn: 'Reopen FluentFlow Local and try again.',
            }),
        ],
        [
            raw.includes('抖音的登录信息过期了'),
            taskErrorDiagnosis({
                code: 'douyin_login_expired',
                titleZh: '抖音登录信息过期',
                titleEn: 'Douyin login expired',
                detailZh: raw,
                detailEn: 'Douyin needs a fresh login in your browser before this link can be read.',
                nextZh: '在浏览器里打开 douyin.com 登录一次，再重试这个链接。',
                nextEn: 'Open douyin.com in your browser, sign in once, then retry this link.',
            }),
        ],
        [
            raw.includes('失去了访问自己程序文件夹的权限') || (lower.includes('operation not permitted') && !raw.includes('/')),
            taskErrorDiagnosis({
                code: 'backend_folder_access_lost',
                titleZh: '后台服务没有文件夹权限',
                titleEn: 'The background service lost folder access',
                detailZh: '后台服务失去了访问自己程序文件夹的权限（macOS 隐私保护），重试不会好。',
                detailEn: 'The background service lost permission to its own folder (macOS privacy protection). Retrying will not help.',
                nextZh: '退出 FluentFlow 再重新打开；还不行就到「系统设置 → 隐私与安全性 → 文件与文件夹」，给启动它的终端或应用打开「文稿」权限。',
                nextEn: 'Quit and reopen FluentFlow. If it persists, open System Settings → Privacy & Security → Files and Folders and allow Documents for the terminal or app that starts FluentFlow.',
                retryable: false,
            }),
        ],
        [
            !lower.includes('lark') && !lower.includes('feishu') && !raw.includes('飞书') && (lower.includes('incorrect api key') || lower.includes('invalid_api_key') || lower.includes('invalid api key') || lower.includes('apikey-error') || lower.includes('api-key-error')
            || lower.includes('api key 不被接受') || lower.includes('authentication_error') || lower.includes('authenticationerror')
            || lower.includes('http 401') || /\b401\b/.test(lower) || lower.includes('unauthorized') || lower.includes('invalid_request_error: incorrect')
            || lower.includes('login is required') || (lower.includes('login') && lower.includes('summary'))
            || raw.includes('账号未登录') || raw.includes('登录态') || raw.includes('重新登录')),
            taskErrorDiagnosis({
                code: 'invalid_api_key',
                titleZh: `${providerName} API Key 无效`,
                titleEn: `${providerName} API key is invalid`,
                detailZh: `AI 笔记没有生成：${providerName} 不接受当前的 API Key（无效、已失效或没有权限）。转录和字幕已保存。`,
                detailEn: `The AI note was not generated: ${providerName} rejected the current API key (invalid, expired, or without permission). Transcript and subtitles are saved.`,
                nextZh: `到设置页替换 ${providerName} 的 API Key，再回到编辑器点击“重生笔记”。`,
                nextEn: `Replace the ${providerName} API key in Settings, then return to the editor and click Regenerate note.`,
            }),
        ],
        [
            lower.includes('error code: 429') || lower.includes('rate limit reached') || lower.includes('rate_limit_error') || lower.includes('rate limit exceeded') || lower.includes('限流'),
            taskErrorDiagnosis({
                code: 'ai_rate_limited',
                titleZh: `${providerName} 暂时限流`,
                titleEn: `${providerName} is rate limiting requests`,
                detailZh: `AI 笔记没有生成：${providerName} 这段时间收到的请求太多，暂时拒绝了。转录和字幕已保存。`,
                detailEn: `The AI note was not generated: ${providerName} is refusing requests for now. Transcript and subtitles are saved.`,
                nextZh: '过几分钟回到编辑器点击“重生笔记”；反复出现就到服务商后台看额度。',
                nextEn: 'Return to the editor in a few minutes and click Regenerate note; if it keeps happening, check your plan at the provider.',
            }),
        ],
        [
            lower.includes('一次最多提交') || lower.includes('too many files'),
            taskErrorDiagnosis({
                code: 'too_many_files',
                titleZh: '一次选的文件太多',
                titleEn: 'Too many files at once',
                detailZh: raw,
                detailEn: 'More files were selected than one batch allows.',
                nextZh: '分成几批提交。',
                nextEn: 'Submit them in smaller batches.',
                retryable: false,
            }),
        ],
        [
            (lower.includes('飞书') || lower.includes('lark') || lower.includes('feishu')) && (lower.includes('folder not found') || lower.includes('文件夹不存在') || lower.includes('notexist')),
            taskErrorDiagnosis({
                code: 'feishu_folder_not_found',
                titleZh: '飞书目标文件夹不存在',
                titleEn: 'Feishu folder not found',
                detailZh: '笔记已生成，但导出时飞书找不到设置里填的目标文件夹。',
                detailEn: 'The note was written, but Feishu could not find the target folder from Settings.',
                nextZh: '到设置页重新选择飞书文件夹，再回到编辑器重新导出。',
                nextEn: 'Pick the Feishu folder again in Settings, then export again from the editor.',
            }),
        ],
        [
            lower.includes('quota') || lower.includes('balance') || lower.includes('insufficient_funds') || lower.includes('额度') || lower.includes('余额') || lower.includes('欠费'),
            taskErrorDiagnosis({
                code: 'provider_balance_exhausted',
                titleZh: `${providerName} 余额用完了`,
                titleEn: `${providerName} balance is exhausted`,
                detailZh: `AI 笔记没有生成：${providerName} 账号的余额或额度已用完。转录和字幕已保存。`,
                detailEn: `The AI note was not generated: the ${providerName} account has no balance or quota left. Transcript and subtitles are saved.`,
                nextZh: `到 ${providerName} 的控制台充值，或在设置页换一个服务商，再回到编辑器点击“重生笔记”。`,
                nextEn: `Top up at ${providerName}, or switch provider in Settings, then return to the editor and click Regenerate note.`,
            }),
        ],
        [
            lower.includes('no position encodings are defined'),
            taskErrorDiagnosis({
                code: 'diarization_unsupported',
                titleZh: '说话人区分不可用',
                titleEn: 'Diarization unavailable',
                detailZh: '本地说话人区分模型无法处理当前音频长度。请关闭说话人区分。',
                detailEn: 'Local diarization cannot handle this audio length. Disable diarization.',
                nextZh: '关闭说话人区分后重试。',
                nextEn: 'Disable diarization and retry.',
            }),
        ],
        [
            lower.includes("confirm you're not a bot") || lower.includes('confirm your age') || lower.includes('sign in to confirm') || lower.includes('inappropriate for some users'),
            taskErrorDiagnosis({
                code: 'youtube_login_required',
                titleZh: 'YouTube 需要登录',
                titleEn: 'YouTube requires sign-in',
                detailZh: 'YouTube 要求登录后才能下载：可能是年龄限制／会员／私享视频，或触发了「请确认你不是机器人」验证。请到设置 →「视频链接下载登录态」选择你已登录 YouTube 的浏览器后重试。',
                detailEn: 'YouTube requires sign-in for this video: it may be age-restricted / members-only / private, or it triggered the "confirm you\'re not a bot" check. In Settings → "Video link login", pick a browser where you are signed into YouTube, then retry.',
                nextZh: '到设置开启「视频链接下载登录态」（选已登录 YouTube 的浏览器）后重试。',
                nextEn: 'Enable "Video link login" in Settings (pick a browser signed into YouTube) and retry.',
            }),
        ],
        [
            lower.includes('http error 403') || raw.includes('视频下载失败：403') || lower.includes('forbidden'),
            taskErrorDiagnosis({
                code: 'platform_forbidden',
                titleZh: '平台拒绝下载',
                titleEn: 'Platform refused download',
                detailZh: '平台拒绝下载当前视频。已尽量优先使用字幕；如果仍失败，请稍后重试、到设置开启「视频链接下载登录态」，或上传本地视频。',
                detailEn: 'The platform refused this video download. FluentFlow will prefer captions when possible; retry later, enable "Video link login" in Settings, or upload the local video.',
                nextZh: '稍后重试、到设置开启「视频链接下载登录态」，或上传本地视频。',
                nextEn: 'Retry later, enable "Video link login" in Settings, or upload the local video.',
            }),
        ],
        [
            lower.includes('http error 429') || lower.includes('too many requests'),
            taskErrorDiagnosis({
                code: 'platform_rate_limited',
                titleZh: '平台请求过于频繁',
                titleEn: 'Platform rate limit',
                detailZh: '平台请求过于频繁，暂时限制了视频或字幕获取。请稍后重试，或上传本地视频/字幕文件。',
                detailEn: 'The platform is temporarily rate-limiting video or subtitle access. Retry later, or upload the local video/subtitle file.',
                nextZh: '稍后重试，或直接上传本地视频/字幕文件。',
                nextEn: 'Retry later, or upload the local video/subtitle file.',
            }),
        ],
        // Must precede the download-timeout rule below, which matches any
        // "timeout": a transcription that ran out of time has nothing to do
        // with downloading, and telling someone who uploaded a local file to
        // "upload the local video" sends them after the wrong subsystem.
        [
            lower.includes('stt processing timed out') || raw.includes('转写超时'),
            taskErrorDiagnosis({
                code: 'stt_timeout',
                titleZh: '转写超时',
                titleEn: 'Transcription timed out',
                detailZh: '转写超过时间上限被中止。通常是音视频太长，或所选引擎在当前机器上跑不完。',
                detailEn: 'Transcription was stopped at the time limit, usually because the recording is long or the chosen engine cannot finish it on this machine.',
                nextZh: '在设置里把「转录速度」调成「快速」后重试；如果素材很长，先拆成几段再处理。',
                nextEn: 'Set Transcription speed to Fast in settings and retry, or split a long recording into parts.',
            }),
        ],
        [
            raw.includes('视频下载超时') || (videoLink && (lower.includes('timed out') || lower.includes('timeout'))),
            taskErrorDiagnosis({
                code: 'video_download_timeout',
                titleZh: '视频下载超时',
                titleEn: 'Video download timed out',
                detailZh: '视频下载时间过长，可能是视频较大或当前网络较慢。笔记会优先尝试使用字幕；如仍失败，请稍后重试或上传本地视频。',
                detailEn: 'The video download took too long, likely due to a large video or slow network. FluentFlow will prefer captions when possible; retry later or upload the local video.',
                nextZh: '稍后重试，或上传本地视频。',
                nextEn: 'Retry later, or upload the local video.',
            }),
        ],
        [
            lower.includes('timed out') || lower.includes('timeout') || raw.includes('超时'),
            taskErrorDiagnosis({
                code: 'processing_timeout',
                titleZh: '处理超时',
                titleEn: 'Processing timed out',
                detailZh: '这一步超过时间上限被中止。通常是素材太长，或这台机器当时很忙。',
                detailEn: 'This step was stopped at its time limit, usually because the material is long or the machine was busy.',
                nextZh: '重试一次；素材很长的话先拆成几段再处理。',
                nextEn: 'Retry once; split long material into parts first.',
            }),
        ],
        [
            raw.includes('暂时无法自动解析这个视频链接'),
            taskErrorDiagnosis({
                code: 'video_link_parse_failed',
                titleZh: '链接暂时无法解析',
                titleEn: 'Link cannot be parsed',
                detailZh: '暂时无法自动解析这个视频链接。请换一个分享链接，或直接上传视频文件。',
                detailEn: 'FluentFlow cannot parse this video link yet. Try another share link, or upload the video file directly.',
                nextZh: '换一个分享链接，或直接上传视频文件。',
                nextEn: 'Try another share link, or upload the video file directly.',
            }),
        ],
        [
            lower.includes('downloaded video is too large') || lower.includes('file is too large') || raw.includes('视频文件过大'),
            taskErrorDiagnosis({
                code: 'file_too_large',
                titleZh: '文件超过限制',
                titleEn: 'File too large',
                detailZh: '文件超过当前上传限制。请压缩视频、拆分文件，或调高后端上传大小限制。',
                detailEn: 'The file exceeds the current upload limit.',
                nextZh: '压缩或拆分文件后重试。',
                nextEn: 'Compress or split the file, then retry.',
            }),
        ],
        [
            lower.includes('unsupported transcript file type'),
            taskErrorDiagnosis({
                code: 'unsupported_transcript_type',
                titleZh: '字幕格式不支持',
                titleEn: 'Subtitle format unsupported',
                detailZh: '不支持这个字幕/转录文件格式。请上传 SRT、VTT、TXT 或 Markdown 文件。',
                detailEn: 'This transcript format is unsupported. Upload SRT, VTT, TXT, or Markdown.',
                nextZh: '换成 SRT、VTT、TXT 或 Markdown 后重试。',
                nextEn: 'Use SRT, VTT, TXT, or Markdown and retry.',
                retryable: false,
            }),
        ],
        [
            lower.includes('unsupported file type'),
            taskErrorDiagnosis({
                code: 'unsupported_file_type',
                titleZh: '文件格式不支持',
                titleEn: 'File format unsupported',
                detailZh: '不支持这个文件格式。请上传视频或音频文件。',
                detailEn: 'This file format is unsupported. Upload a video or audio file.',
                nextZh: '换成支持的视频或音频文件后重试。',
                nextEn: 'Use a supported video or audio file and retry.',
                retryable: false,
            }),
        ],
        [
            lower.includes('queued source file is missing') || raw.includes('原始文件已不存在'),
            taskErrorDiagnosis({
                code: 'source_file_missing',
                titleZh: '原始文件已不存在',
                titleEn: 'Source file missing',
                detailZh: '后台任务找不到原始文件。文件可能已被清理，请重新上传。',
                detailEn: 'The background task cannot find the source file. It may have been cleaned up.',
                nextZh: '重新上传原始文件后再处理。',
                nextEn: 'Upload the source file again.',
                retryable: false,
            }),
        ],
        [
            lower.includes('queued processing request failed') || lower.includes('queued transcript summary request failed'),
            taskErrorDiagnosis({
                code: lower.includes('summary') ? 'queue_summary_failed' : 'queue_processing_failed',
                titleZh: lower.includes('summary') ? '后台笔记生成调用失败' : '后台队列调用失败',
                titleEn: lower.includes('summary') ? 'Background note request failed' : 'Background queue request failed',
                detailZh: lower.includes('summary') ? '后台任务调用笔记生成接口失败。请重试；如果连续出现，请重启后端服务。' : '处理流程调用转录接口失败。请重试；如果连续出现，请重启后端服务。',
                detailEn: lower.includes('summary') ? 'The background task could not call the note endpoint. Retry or restart the backend.' : 'The processing flow could not call the transcription endpoint. Retry or restart the backend.',
                nextZh: lower.includes('summary') ? '重试；如果转录已保存，打开结果后重生笔记。' : '重试；如果连续出现，重启后端服务后再提交。',
                nextEn: lower.includes('summary') ? 'Retry; if the transcript is saved, reopen the result and regenerate the note.' : 'Retry; if it keeps failing, restart the backend and submit again.',
            }),
        ],
        [
            lower.includes('unsupported note generation mode') || lower.includes('chapter_coverage'),
            taskErrorDiagnosis({
                code: 'unsupported_note_mode',
                titleZh: '笔记模式不受当前版本支持',
                titleEn: 'Note mode unsupported',
                detailZh: '当前版本不支持这类笔记生成模式。请选择“自动”或“高保真”后重新提交任务。',
                detailEn: 'This note generation mode is not supported by this version. Choose Auto or High fidelity and submit again.',
                nextZh: '切换为“自动”或“高保真”后重新提交任务。',
                nextEn: 'Switch to Auto or High fidelity and submit again.',
                retryable: false,
            }),
        ],
        [
            lower.includes('empty result') || lower.includes('returned empty') || raw.includes('空笔记'),
            taskErrorDiagnosis({
                code: 'empty_ai_note',
                titleZh: 'AI 返回了空笔记',
                titleEn: 'AI returned an empty note',
                detailZh: 'AI 返回了空笔记，没有生成可用内容。',
                detailEn: 'The AI returned an empty note and produced no usable content.',
                nextZh: '重生笔记；如果重复出现，换用直接生成模式或调整提示词。',
                nextEn: 'Regenerate the note; if it repeats, use direct mode or adjust the prompt.',
            }),
        ],
        [
            lower.includes('feishu') || raw.includes('飞书') || lower.includes('lark'),
            taskErrorDiagnosis({
                code: lower.includes('lark-cli') && (lower.includes('login') || lower.includes('auth')) ? 'lark_cli_login_required' : 'feishu_export_failed',
                titleZh: lower.includes('lark-cli') && (lower.includes('login') || lower.includes('auth')) ? '本机飞书登录失效' : '飞书导出失败',
                titleEn: lower.includes('lark-cli') && (lower.includes('login') || lower.includes('auth')) ? 'Local Lark login expired' : 'Feishu export failed',
                detailZh: lower.includes('lark-cli') && (lower.includes('login') || lower.includes('auth')) ? '飞书导出失败：当前 lark-cli 没有可用登录身份。' : '飞书导出失败。请检查授权、导出路线和目标文档权限。',
                detailEn: lower.includes('lark-cli') && (lower.includes('login') || lower.includes('auth')) ? 'Lark export failed: lark-cli has no usable login.' : 'Feishu export failed. Check authorization, export route, and target document permissions.',
                nextZh: lower.includes('lark-cli') && (lower.includes('login') || lower.includes('auth')) ? '在本机重新登录 lark-cli 后重试导出。' : '检查飞书授权和导出路线后重试导出。',
                nextEn: lower.includes('lark-cli') && (lower.includes('login') || lower.includes('auth')) ? 'Sign in to lark-cli locally, then retry export.' : 'Check Feishu authorization and export route, then retry.',
            }),
        ],
    ];
    const match = patterns.find(([condition]) => condition);
    if(match) return pick(match[1]);
    return pick(taskErrorDiagnosis({
        code: 'unknown_error',
        titleZh: '任务处理失败',
        titleEn: 'Task failed',
        detailZh: raw,
        detailEn: raw,
        nextZh: '重试一次；如果连续失败，请把任务详情发给维护者排查。',
        nextEn: 'Retry once; if it keeps failing, send the task detail to the maintainer.',
    }));
};

export const friendlyTaskError = (message, lang='zh', context={}) => {
    return diagnoseTaskError(message, lang, context).detail;
};
// The context a job record can lend to its own failure message.
export const taskErrorContextForJob = (job={}) => {
    const metadata = job?.metadata || {};
    const queueOptions = metadata.queue_options && typeof metadata.queue_options === 'object' ? metadata.queue_options : {};
    return {
        provider: queueOptions.ai_provider || metadata.ai_provider || job?.result?.ai_provider || job?.ai_provider || '',
        sourceType: job?.source_type || job?.result?.source || job?.sourceType || '',
    };
};

export const noteGenerationDiagnosis = (result={}, lang='zh') => {
    const summary = String(result?.summary_markdown || '').trim();
    const status = String(result?.summary_status || '').trim().toLowerCase();
    const stage = String(result?.stage || '').trim().toLowerCase();
    const rawError = String(result?.summary_error || result?.error_reason || '').trim();
    const hasTranscript = !!String(result?.transcript_text || result?.transcript_text_preview || '').trim()
        || (Array.isArray(result?.raw_segments) && result.raw_segments.length > 0)
        || (Array.isArray(result?.display_segments) && result.display_segments.length > 0);
    const zh = lang === 'zh';
    const base = {
        status: 'pending',
        code: 'note_pending',
        severity: 'info',
        title: zh ? '笔记还在生成' : 'Note is still generating',
        detail: zh ? '转录已进入摘要阶段，等待 AI 返回笔记。' : 'The transcript has entered the summary stage and is waiting for the AI note.',
        nextAction: zh ? '稍等片刻；如果长时间没有变化，再刷新任务状态。' : 'Wait a moment; refresh the task status if it does not change.',
        canRegenerate: false,
    };

    if(summary) {
        return {
            ...base,
            status: 'completed',
            code: 'note_completed',
            severity: 'success',
            title: zh ? '笔记已生成' : 'Note generated',
            detail: zh ? '当前结果包含可用的 AI 笔记。' : 'This result contains an AI-generated note.',
            nextAction: '',
            canRegenerate: true,
        };
    }
    if(!hasTranscript) {
        return {
            ...base,
            status: 'unavailable',
            code: 'transcript_missing',
            severity: 'warning',
            title: zh ? '还没有可用于生成笔记的转录' : 'No transcript available for note generation',
            detail: zh ? '需要先完成转录，AI 才能生成笔记。' : 'Transcription must finish before the AI can generate a note.',
            nextAction: zh ? '先等待或重新提交转录任务。' : 'Wait for transcription or submit the task again.',
        };
    }
    if(result?.summary_skipped || status === 'skipped') {
        return {
            ...base,
            status: 'skipped',
            code: 'transcript_only_mode',
            severity: 'neutral',
            title: zh ? '本次开启了仅转录模式' : 'Transcript-only mode was used',
            detail: zh ? '系统按设置跳过了 AI 笔记，转录和字幕已保留。' : 'The system skipped AI note generation by setting; transcript and subtitles are preserved.',
            nextAction: zh ? '需要笔记时，打开结果并点击“重生笔记”。' : 'Open the result and click Regenerate note when you need a note.',
            canRegenerate: true,
        };
    }
    if(status === 'failed' || rawError) {
        const diag = diagnoseTaskError(rawError, lang, {provider: result?.ai_provider, sourceType: result?.source});
        const code = diag.code === 'unknown_error' ? 'ai_note_failed' : diag.code;
        const title = diag.code === 'unknown_error' ? (zh ? 'AI 笔记生成失败' : 'AI note generation failed') : diag.title;
        const nextAction = diag.code === 'unknown_error'
            ? (zh ? '打开结果后点击“重生笔记”；如果仍失败，换一个笔记模式或缩短材料。' : 'Open the result and click Regenerate note; if it still fails, change the note mode or shorten the material.')
            : diag.nextAction;
        return {
            ...base,
            status: 'failed',
            code,
            severity: 'error',
            title,
            detail: diag.detail,
            nextAction,
            canRegenerate: true,
        };
    }
    if(status === 'pending' || stage === 'summary') return base;
    return {
        ...base,
        code: 'note_missing_unknown',
        severity: 'warning',
        title: zh ? '暂时没有可见笔记' : 'No visible note yet',
        detail: zh ? '转录已存在，但结果里没有记录明确的笔记状态。' : 'A transcript exists, but the result does not record a clear note status.',
        nextAction: zh ? '打开结果点击“重生笔记”；如果失败，再查看任务详情。' : 'Open the result and click Regenerate note; check task details if it fails.',
        canRegenerate: true,
    };
};

export const sttStatusLabel = (status, t) => {
    const key = {
        starting: 'dash.sttStarting',
        loading_model: 'dash.sttLoadingModel',
        chunking_audio: 'dash.sttChunking',
        preparing_audio: 'dash.sttPreparingAudio',
        waiting_first_segment: 'dash.sttWaitingFirst',
        transcribing_chunks: 'dash.sttChunks',
        transcribing_segments: 'dash.sttSegments',
    }[status || ''];
    return key ? t(key) : t('dash.waitingSegment');
};
export const sttProgressFraction = (job) => Math.max(0, Math.min(1, Number(job?.sttProgress) || 0));
export const isSttProgressUnmeasured = (job) => (
    job?.stage === 'stt'
    && sttProgressFraction(job) <= 0
    && job?.sttStatus !== 'transcribing_segments'
);
// Removing the breath gaps has no percentage to report: it is one ffmpeg pipeline
// over the whole file, and the pipeline publishes a single number at the start of
// the stage. On a 24-minute recording that number sat at 2% for twenty minutes,
// which is how a working task got reported as a hung one. No number is the honest
// answer here, and the stage's own label says it takes minutes.
export const isProgressUnmeasured = (job) => (
    job?.stage === 'prepare_media' || isSttProgressUnmeasured(job)
);
export const jobProgressLabel = (job, t) => isProgressUnmeasured(job)
    ? t('dash.progressUnknown')
    : `${Math.round(Math.max(0, Math.min(100, Number(job?.progress) || 0)))}%`;

export const fmtSttRelative = (factor, lang) => {
    const n = Number(factor);
    if(!Number.isFinite(n) || n <= 0) return '';
    if(n * 100 < 1) return lang === 'zh' ? '低于原时长 1%' : '<1% of media duration';
    const pct = Math.round(n * 100);
    return lang === 'zh' ? `约为原时长 ${pct}%` : `${pct}% of media duration`;
};

export const timeAgo = (ts, t) => {
    const d = Date.now()-ts, m=Math.floor(d/60000), h=Math.floor(d/3600000), dy=Math.floor(d/86400000);
    if(m<1) return t('dash.justNow');
    if(m<60) return `${m} ${t('dash.mAgo')}`;
    if(h<24) return `${h} ${t('dash.hAgo')}`;
    return `${dy} ${t('dash.dAgo')}`;
};
