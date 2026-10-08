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
const CJK_RE = /[\u4e00-\u9fff]/;
const LINK_WORDS_RE = /视频|链接|作品|抖音|B ?站|bilibili|youtube|douyin|video|link|url/i;
// The service's own Chinese sentence when it wrote one; otherwise ours.
const zhOr = (raw, fallback) => (CJK_RE.test(raw) ? raw : fallback);
// The chosen browser's cookie store could not be opened or decrypted. Needs a
// word about the browser or cookies; "登录态已失效" alone is an AI-key failure.
const linkCookieStoreUnreadable = (raw, lower) => (
    lower.includes('could not find') && lower.includes('cookies database')
    || lower.includes('failed to decrypt') && lower.includes('cookie')
    || lower.includes('could not copy') && lower.includes('cookie')
    || lower.includes('cookie store') && (lower.includes('unreadable') || lower.includes('cannot be read') || lower.includes('could not be read'))
    || /cookie|浏览器|chrome|safari|edge|firefox|brave/i.test(raw)
        && (raw.includes('读不到') || raw.includes('无法读取') || raw.includes('读取失败') || raw.includes('打不开') || raw.includes('解不开') || raw.includes('解密'))
);
// Over the size cap for link downloads. Phrased by the service as a limit (上限
// / 超过 … MB); an over-size link download is worded the same as an upload one
// in older messages, so those count when the task is a link.
const linkSizeLimit = (raw, lower, videoLink) => (
    (raw.includes('上限') && !raw.includes('时长') && (raw.includes('MB') || raw.includes('GB') || raw.includes('大小') || raw.includes('下载')))
    || (lower.includes('exceeds') && (lower.includes('size limit') || lower.includes('download limit') || lower.includes('max')))
    || lower.includes('max-filesize') || lower.includes('larger than max')
    || (videoLink && (lower.includes('downloaded video is too large') || lower.includes('file is too large') || raw.includes('视频文件过大')))
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
    // Whether a failure can be about a video link at all: the job says so, or
    // the message names a link, a video, or a platform. Submission errors arrive
    // without a job, so the message has to be enough.
    const aboutLink = videoLink || LINK_WORDS_RE.test(raw);
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
        // Link failures the service now names precisely. Its Chinese sentence is
        // already written for the reader and says which link and what to do, so
        // it is shown as is; the English text is ours. These sit ahead of the
        // API-key rule, which also matches words like 登录态.
        [
            linkCookieStoreUnreadable(raw, lower),
            taskErrorDiagnosis({
                code: 'video_cookies_unreadable',
                titleZh: '读不到浏览器登录态',
                titleEn: 'Browser login could not be read',
                detailZh: zhOr(raw, '读不到所选浏览器的登录信息（cookie 库打不开或解不开），这次没法用它下载。'),
                detailEn: 'The browser login chosen in Settings could not be read (its cookie store could not be opened or decrypted), so it could not be used for this download.',
                nextZh: '到设置的「视频链接下载登录态」换一个浏览器、点「检测登录态」确认，或者选「关闭」，再重试。',
                nextEn: 'In Settings → "Video link login", pick another browser and press "Check login", or choose Off, then retry.',
            }),
        ],
        [
            raw.includes('YouTube') && (raw.includes('登录') || raw.includes('机器人') || raw.includes('年龄')),
            taskErrorDiagnosis({
                code: 'youtube_login_required',
                titleZh: 'YouTube 需要登录',
                titleEn: 'YouTube requires sign-in',
                detailZh: raw,
                detailEn: 'YouTube requires sign-in for this video (age check, bot check, or restricted access). In Settings → "Video link login", pick a browser where you are signed into YouTube, then retry.',
                nextZh: '到设置开启「视频链接下载登录态」（选已登录 YouTube 的浏览器）后重试。',
                nextEn: 'Enable "Video link login" in Settings (pick a browser signed into YouTube) and retry.',
            }),
        ],
        [
            aboutLink && raw.includes('没有给出可下载的视频'),
            taskErrorDiagnosis({
                code: 'video_link_no_media',
                titleZh: '平台没有给出可下载的视频',
                titleEn: 'The platform gave no downloadable video',
                detailZh: zhOr(raw, '平台没有给出可以下载的视频，可能是图文、已删除或私密作品。'),
                detailEn: 'The platform returned no video to download; it may be an image post, deleted, or private.',
                nextZh: '在平台里打开确认；能播放的话，下载到本机后上传。',
                nextEn: 'Open it on the platform to check; if it plays, download it and upload the file.',
            }),
        ],
        [
            aboutLink && raw.includes('解析成功，但下载没有成功'),
            taskErrorDiagnosis({
                code: 'video_link_download_failed',
                titleZh: '视频下载没有成功',
                titleEn: 'The video did not download',
                detailZh: zhOr(raw, '链接解析成功了，但下载视频时失败。'),
                detailEn: 'The link resolved, but downloading the video failed.',
                nextZh: '稍后重试；反复失败的话，下载到本机后上传。',
                nextEn: 'Retry later; if it keeps failing, download it and upload the file.',
            }),
        ],
        [
            aboutLink && (raw.includes('需要登录后才能') || raw.includes('需要已登录')),
            taskErrorDiagnosis({
                code: 'video_link_login_required',
                titleZh: '这个链接需要登录',
                titleEn: 'This link needs a signed-in browser',
                detailZh: zhOr(raw, '平台要求登录后才能下载这个视频。'),
                detailEn: 'The platform requires a signed-in account to download this video.',
                nextZh: '到设置里选择已登录这个平台的浏览器，再重试；或下载到本机后上传。',
                nextEn: 'Pick a browser signed into the platform in Settings and retry, or download it and upload the file.',
            }),
        ],
        [
            aboutLink && (raw.includes('限制了请求') || raw.includes('请求过于频繁')),
            taskErrorDiagnosis({
                code: 'video_link_rate_limited',
                titleZh: '平台暂时限制了请求',
                titleEn: 'The platform is rate limiting',
                detailZh: zhOr(raw, '平台暂时限制了这台电脑的请求。'),
                detailEn: 'The platform is temporarily limiting requests from this computer.',
                nextZh: '过几分钟再重试。',
                nextEn: 'Retry in a few minutes.',
            }),
        ],
        [
            raw.includes('视频时长过长'),
            taskErrorDiagnosis({
                code: 'video_link_too_long',
                titleZh: '视频太长',
                titleEn: 'Video is too long',
                detailZh: zhOr(raw, '视频时长超过了当前限制。'),
                detailEn: 'The video is longer than the current limit.',
                nextZh: '下载到本机，拆分后再上传。',
                nextEn: 'Download it, split it, and upload the parts.',
                retryable: false,
            }),
        ],
        [
            aboutLink && (raw.includes('图文') || lower.includes('image post') || lower.includes('photo post') || lower.includes('slideshow')),
            taskErrorDiagnosis({
                code: 'video_link_image_post',
                titleZh: '这条是图文，不是视频',
                titleEn: 'This is an image post, not a video',
                detailZh: zhOr(raw, '这个链接是图文作品，里面没有视频可以下载。'),
                detailEn: 'This link is an image post; there is no video in it to download.',
                nextZh: '换一个视频链接。',
                nextEn: 'Use a link to a video instead.',
                retryable: false,
            }),
        ],
        [
            aboutLink && (raw.includes('会员') || raw.includes('试看') || raw.includes('付费') || raw.includes('充电专属')
            || lower.includes('members-only') || lower.includes("channel's members") || lower.includes('members only')
            || lower.includes('preview only') || lower.includes('only a preview') || lower.includes('requires payment')),
            taskErrorDiagnosis({
                code: 'video_link_member_content',
                titleZh: '会员或付费内容',
                titleEn: 'Members-only or paid content',
                detailZh: zhOr(raw, '这是会员、付费或试看内容，只能拿到试看片段，没法下载完整视频。'),
                detailEn: 'This is members-only, paid, or preview content; only a preview can be fetched, not the full video.',
                nextZh: '如果你有这段视频的完整文件，用「本地上传」处理。',
                nextEn: 'If you have the full file, process it with Local files instead.',
                retryable: false,
            }),
        ],
        [
            aboutLink && (raw.includes('私密') || raw.includes('仅自己可见') || raw.includes('仅粉丝可见') || raw.includes('私享') || lower.includes('private video') || lower.includes('this video is private')),
            taskErrorDiagnosis({
                code: 'video_link_private',
                titleZh: '私密视频',
                titleEn: 'Private video',
                detailZh: zhOr(raw, '这是私密或仅部分人可见的视频，没有权限的账号看不到。'),
                detailEn: 'This video is private or limited to some viewers; an account without access cannot see it.',
                nextZh: '如果你的账号能看，到设置的「视频链接下载登录态」选登录了这个账号的浏览器，再重试。',
                nextEn: 'If your account can see it, pick the browser signed into that account in Settings → "Video link login", then retry.',
            }),
        ],
        [
            aboutLink && (/已删除|被.{0,6}删除/.test(raw) || raw.includes('已下架') || raw.includes('作品不存在') || raw.includes('视频不存在')
            || lower.includes('has been removed') || lower.includes('been deleted') || lower.includes('no longer available')),
            taskErrorDiagnosis({
                code: 'video_link_deleted',
                titleZh: '视频已删除或下架',
                titleEn: 'Video deleted or removed',
                detailZh: zhOr(raw, '这个视频已经被删除或下架，链接打不开了。'),
                detailEn: 'This video has been deleted or taken down; the link no longer opens.',
                nextZh: '确认链接在浏览器里还能打开；打不开就换一个链接。',
                nextEn: 'Check that the link still opens in a browser; if not, use another link.',
                retryable: false,
            }),
        ],
        [
            linkSizeLimit(raw, lower, videoLink),
            taskErrorDiagnosis({
                code: 'video_link_too_large',
                titleZh: '视频超过链接下载上限',
                titleEn: 'Video exceeds the link download limit',
                detailZh: zhOr(raw, '这个视频超过了链接下载的大小上限，重试也一样。'),
                detailEn: 'This video is larger than the link download limit; retrying gives the same result.',
                nextZh: '先在浏览器或平台客户端里把视频下载到本机，再用「本地上传」处理（本地文件的上限大得多）。',
                nextEn: 'Download the video to this computer first (browser or the platform app), then process it with Local files, which allow much larger files.',
                retryable: false,
            }),
        ],
        [
            aboutLink && (lower.includes('unsupported url') || raw.includes('不支持这个网站') || raw.includes('不支持该网站') || raw.includes('不支持这个链接') || raw.includes('不支持的链接') || raw.includes('暂不支持这个平台') || raw.includes('目前只支持')),
            taskErrorDiagnosis({
                code: 'video_link_unsupported_site',
                titleZh: '不支持这个网站',
                titleEn: 'Site not supported',
                detailZh: zhOr(raw, '这个网站的链接还不支持。目前支持抖音、Bilibili、YouTube 和 .mp4 直链。'),
                detailEn: 'Links from this site are not supported. Douyin, Bilibili, YouTube, and direct .mp4 links are.',
                nextZh: '把视频下载到本机后用「本地上传」处理。',
                nextEn: 'Download the video to this computer and process it with Local files.',
                retryable: false,
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
// The download report a link task carries, wherever this job shape keeps it.
export const videoSourceProgressOf = (job) => (
    job?.metadata?.video_source_progress || job?.video_source_progress || job?.videoSourceProgress || null
);
// A link download whose size the platform did not say (yt-dlp, a stream without
// a length) has no percentage. The job's own number then stays at the stage's
// starting value, 10%, until the download ends, which reads as a hung task. Only
// a reported percent or a known total makes the number mean something. With no
// report at all there is nothing to judge by, and the job's number stands.
export const isDownloadProgressUnmeasured = (job) => {
    if (String(job?.stage || '') !== 'downloading') return false;
    const report = videoSourceProgressOf(job);
    if (!report || typeof report !== 'object') return false;
    if (report.percent !== null && report.percent !== undefined && report.percent !== '' && Number.isFinite(Number(report.percent))) return false;
    return !(Number(report.total_bytes) > 0);
};
// Removing the breath gaps has no percentage to report: it is one ffmpeg pipeline
// over the whole file, and the pipeline publishes a single number at the start of
// the stage. On a 24-minute recording that number sat at 2% for twenty minutes,
// which is how a working task got reported as a hung one. No number is the honest
// answer here, and the stage's own label says it takes minutes.
export const isProgressUnmeasured = (job) => (
    job?.stage === 'prepare_media' || isSttProgressUnmeasured(job) || isDownloadProgressUnmeasured(job)
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
