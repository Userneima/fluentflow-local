import {describe, expect, it} from 'vitest';
import {diagnoseTaskError, isDownloadProgressUnmeasured, isProgressUnmeasured, jobProgressLabel} from './format.js';

// Written from what someone whose link failed should read: the service's own
// sentence when it named the problem, and no "submit again" when submitting
// again cannot give a different result.

const link = {sourceType: 'video_link'};

describe('link failures the service names precisely', () => {
    it('shows the service sentence for an image post, and does not offer a retry', () => {
        const message = '这个抖音链接是图文作品，没有视频可以下载。';
        const diag = diagnoseTaskError(message, 'zh', link);
        expect(diag.detail).toBe(message);
        expect(diag.retryable).toBe(false);
        expect(diagnoseTaskError(message, 'en', link).detail).toMatch(/image post/);
    });

    it('treats deleted videos as final and private ones as fixable by signing in', () => {
        const deleted = diagnoseTaskError('这个视频已被作者删除，链接打不开了。', 'zh', link);
        expect(deleted.detail).toBe('这个视频已被作者删除，链接打不开了。');
        expect(deleted.retryable).toBe(false);
        const priv = diagnoseTaskError('ERROR: [youtube] abc: This video is private', 'zh', link);
        expect(priv.code).toBe('video_link_private');
        expect(priv.nextAction).toMatch(/视频链接下载登录态/);
    });

    it('says members-only or preview content cannot be fetched in full, without a retry', () => {
        for (const message of ['B 站会员专享视频，只能下载到试看片段。', "ERROR: Join this channel to get access to members-only content like this video"]) {
            const diag = diagnoseTaskError(message, 'zh', link);
            expect(diag.code).toBe('video_link_member_content');
            expect(diag.retryable).toBe(false);
        }
    });

    it('points an over-size link at downloading it locally, not at compressing', () => {
        const message = '这个视频约 1.2 GB，超过了链接下载 600 MB 的上限。';
        const diag = diagnoseTaskError(message, 'zh', link);
        expect(diag.detail).toBe(message);
        expect(diag.retryable).toBe(false);
        expect(diag.nextAction).toMatch(/本地上传/);
        expect(diag.nextAction).not.toMatch(/压缩/);
        // An older wording, on a link task.
        expect(diagnoseTaskError('Downloaded video is too large', 'zh', link).code).toBe('video_link_too_large');
        // The same old wording on an upload keeps the upload advice.
        expect(diagnoseTaskError('Downloaded video is too large', 'zh', {sourceType: 'video_file'}).code).toBe('file_too_large');
    });

    it('sends an unreadable cookie store to the browser setting, not to the AI key', () => {
        for (const message of [
            '读不到 Chrome 的登录信息（cookie 库打不开），这次没有用登录态下载。',
            'ERROR: could not find chrome cookies database in "/Users/y/Library/Application Support/Google/Chrome"',
            'ERROR: Failed to decrypt cookie (AES-GCM) because the MAC check failed',
        ]) {
            const diag = diagnoseTaskError(message, 'zh', link);
            expect(diag.code).toBe('video_cookies_unreadable');
            expect(diag.nextAction).toMatch(/视频链接下载登录态/);
            expect(diag.retryable).toBe(true);
        }
        // An AI key that lost its login is still an AI key problem.
        expect(diagnoseTaskError('账号未登录或登录态已失效', 'zh').code).toBe('invalid_api_key');
    });

    it('keeps the YouTube sign-in sentence the service wrote', () => {
        const message = 'YouTube 要求登录确认不是机器人。到设置里选一个登录了 YouTube 的浏览器再重试。';
        const diag = diagnoseTaskError(message, 'zh', link);
        expect(diag.code).toBe('youtube_login_required');
        expect(diag.detail).toBe(message);
        expect(diag.retryable).toBe(true);
        expect(diagnoseTaskError("Sign in to confirm you're not a bot", 'zh', link).code).toBe('youtube_login_required');
    });

    it('does not offer a retry for a site that is not supported', () => {
        const diag = diagnoseTaskError('ERROR: Unsupported URL: https://example.com/watch/1', 'zh');
        expect(diag.code).toBe('video_link_unsupported_site');
        expect(diag.retryable).toBe(false);
    });

    it('leaves note-generation failures alone even when they mention paying', () => {
        expect(diagnoseTaskError('余额不足，请充值后付费使用', 'zh', {provider: 'qwen'}).code).toBe('provider_balance_exhausted');
    });
});

describe('download progress', () => {
    const t = (key) => ({'dash.progressUnknown': '处理中'}[key] || key);

    it('has no number when neither a percent nor a total size was reported', () => {
        const job = {stage: 'downloading', progress: 10, metadata: {video_source_progress: {message: '正在下载视频', loaded_bytes: 5_000_000}}};
        expect(isDownloadProgressUnmeasured(job)).toBe(true);
        expect(isProgressUnmeasured(job)).toBe(true);
        expect(jobProgressLabel(job, t)).toBe('处理中');
    });

    it('keeps the number when the service sent a percent or a total size', () => {
        expect(isDownloadProgressUnmeasured({stage: 'downloading', metadata: {video_source_progress: {percent: 40}}})).toBe(false);
        expect(isDownloadProgressUnmeasured({stage: 'downloading', metadata: {video_source_progress: {loaded_bytes: 10, total_bytes: 100}}})).toBe(false);
        expect(isDownloadProgressUnmeasured({stage: 'downloading', video_source_progress: {percent: 0}})).toBe(false);
    });

    it('trusts the job number when no download report came with it', () => {
        expect(isDownloadProgressUnmeasured({stage: 'downloading', progress: 35})).toBe(false);
    });

    it('only concerns the download stage', () => {
        expect(isDownloadProgressUnmeasured({stage: 'resolving', metadata: {}})).toBe(false);
        expect(isDownloadProgressUnmeasured({stage: 'summary', metadata: {}})).toBe(false);
    });
});

// The backend's exact sentences (backend/core/video_source.py, 2026-10-08).
// If either side rewords one, this is where the two stop agreeing.
describe('every sentence the backend raises for a link lands in its own category', () => {
    const cases = [
        ['图文作品没有可转写的音视频：这个抖音链接是图文作品（图片加配乐）。请换一个视频链接。', 'video_link_image_post', false],
        ['抖音没有给出可下载的视频：可能是图文作品（图文作品没有可转写的音视频）、视频已删除或设为私密。请在抖音里打开确认；能播放的话，把视频下载到本机后上传。', 'video_link_no_media', true],
        ['抖音视频解析成功，但下载没有成功，第三方解析也没有拿到。请稍后重试，或把视频下载到本机后上传。', 'video_link_download_failed', true],
        ['抖音的登录信息过期了：在 Chrome 里打开 douyin.com 登录一次，再重试这个链接。', 'douyin_login_expired', true],
        ['读不到 Chrome 里的抖音登录：系统不允许 FluentFlow 读取这个浏览器的数据。可以在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」里允许启动 FluentFlow 的程序，或者先把视频下载到本机再上传。', 'video_cookies_unreadable', true],
        ['这个 B 站视频是会员/付费内容，只能拿到试看片段（拿到约 3 分钟，完整约 10 分钟），没有继续处理。请在设置里选择已登录有权限账号的浏览器后重试，或把完整视频下载到本机后上传。', 'video_link_member_content', false],
        ['这个 B 站链接需要登录后才能下载。请在设置里选择已登录 B 站的浏览器（浏览器登录态），或改为上传本地视频。', 'video_link_login_required', true],
        ['这个 B 站链接的视频已删除、不存在或有地区限制。请在 B 站里打开确认，或上传本地视频。', 'video_link_deleted', false],
        ['B 站暂时限制了请求（请求过于频繁）。请过几分钟再试，或上传本地视频。', 'video_link_rate_limited', true],
        ['YouTube 要求验证「不是机器人」，拒绝了这台电脑对这个链接的请求。可以在设置里选择已登录 YouTube 的浏览器后重试，或上传视频文件。', 'youtube_login_required', true],
        ['链接视频过大：这个视频约 3000 MB，链接下载的上限是 2048 MB。请先下载到本机，压缩或拆分后再上传。', 'video_link_too_large', false],
        ['视频时长过长：约 300 分钟，当前限制为 240 分钟。请先下载到本机，拆分后再上传。', 'video_link_too_long', false],
        ['目前只支持抖音、Bilibili、YouTube 或视频直链', 'video_link_unsupported_site', false],
    ];
    it.each(cases)('%s', (message, code, retryable) => {
        const d = diagnoseTaskError(message, 'zh', {sourceType: 'video_link'});
        expect(d.code).toBe(code);
        expect(d.retryable).toBe(retryable);
        expect(d.code).not.toBe('invalid_api_key');
    });
});
