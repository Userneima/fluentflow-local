import {describe, expect, it} from 'vitest';
import {diagnoseTaskError, friendlyTaskError, taskErrorContextForJob} from './format.js';

// Written from what a person using FluentFlow Local should read, not from the
// rule table. This edition has no accounts and no hosted quota: every failure
// has to point at something the user can do on this machine.

describe('an AI service rejecting the key', () => {
    it('never tells a Local user to sign in again', () => {
        for (const message of ['HTTP 401', 'unauthorized', 'FluentFlow account login is required', '账号未登录或登录态已失效']) {
            const diag = diagnoseTaskError(message, 'zh');
            expect(diag.code).toBe('invalid_api_key');
            expect(diag.detail).not.toContain('登录');
            expect(diag.title).not.toContain('登录');
        }
    });

    it('names the provider the job used and points at the settings page', () => {
        const diag = diagnoseTaskError('Incorrect API key provided', 'zh', {provider: 'deepseek'});
        expect(diag.title).toBe('DeepSeek API Key 无效');
        expect(diag.nextAction).toContain('设置页');
        expect(diag.nextAction).toContain('DeepSeek');
    });

    it('reads the provider from the message when the job did not say', () => {
        const diag = diagnoseTaskError('Anthropic 的 API Key 不被接受，请检查后重新填写。', 'zh');
        expect(diag.code).toBe('invalid_api_key');
        expect(diag.title).toContain('Anthropic');
    });
});

describe('an AI service with no balance', () => {
    it('says whose balance ran out, and does not mention recharging an account here', () => {
        const diag = diagnoseTaskError('insufficient_quota: You exceeded your current quota', 'zh', {provider: 'openai'});
        expect(diag.code).toBe('provider_balance_exhausted');
        expect(diag.title).toContain('OpenAI');
        expect(diag.detail).not.toContain('联系维护者');
        expect(diag.nextAction).toContain('OpenAI');
    });

    it('falls back to a generic service name when nothing names the provider', () => {
        const diag = diagnoseTaskError('余额不足', 'zh');
        expect(diag.title).toBe('AI 服务 余额用完了');
    });
});

describe('a timeout', () => {
    it('blames the download only when the material came from a video link', () => {
        const link = diagnoseTaskError('Read timeout', 'zh', {sourceType: 'video_link'});
        expect(link.code).toBe('video_download_timeout');
        expect(link.nextAction).toContain('上传本地视频');
    });

    it('does not tell someone who uploaded a file to upload the file', () => {
        for (const sourceType of ['video_file', 'audio_file', 'queue_upload', '']) {
            const diag = diagnoseTaskError('Read timeout', 'zh', {sourceType});
            expect(diag.code).toBe('processing_timeout');
            expect(diag.detail).not.toContain('下载');
            expect(diag.nextAction).not.toContain('上传');
        }
    });

    it('keeps the transcription timeout its own diagnosis', () => {
        expect(diagnoseTaskError('STT processing timed out', 'zh', {sourceType: 'video_link'}).code).toBe('stt_timeout');
    });

    it('trusts a message that itself says the video download timed out', () => {
        expect(diagnoseTaskError('视频下载超时', 'zh').code).toBe('video_download_timeout');
    });
});

describe('the local service being down', () => {
    it('tells the user to reopen FluentFlow Local and that nothing is lost', () => {
        for (const message of ['TypeError: Failed to fetch', 'NetworkError when attempting to fetch resource.', 'Load failed']) {
            expect(friendlyTaskError(message, 'zh')).toBe('本机服务没有响应，请重新打开 FluentFlow Local；任务和记录都还在。');
        }
        expect(diagnoseTaskError('TypeError: Failed to fetch', 'en').detail).toContain('Reopen FluentFlow Local');
    });
});

describe('context lent by a job record', () => {
    it('takes the provider from the queued options and the source type from the job', () => {
        const job = {source_type: 'video_link', metadata: {queue_options: {ai_provider: 'qwen'}}};
        expect(taskErrorContextForJob(job)).toEqual({provider: 'qwen', sourceType: 'video_link'});
        expect(friendlyTaskError('HTTP 401', 'zh', taskErrorContextForJob(job))).toContain('百炼 / DashScope');
    });
});

describe('unchanged diagnoses', () => {
    it('still recognises the Douyin login and unsupported file cases', () => {
        expect(diagnoseTaskError('抖音的登录信息过期了', 'zh').code).toBe('douyin_login_expired');
        expect(diagnoseTaskError('Unsupported file type', 'zh').code).toBe('unsupported_file_type');
    });

    it('keeps a Feishu export failure out of the API-key diagnosis', () => {
        expect(diagnoseTaskError('lark-cli auth failed: unauthorized', 'zh').code).toBe('lark_cli_login_required');
    });
});

describe('failures the backend now reports in Chinese keep their category on the records page', () => {
    it('names rate limiting and tells the person to wait', () => {
        const d = diagnoseTaskError('Error code: 429 - {"error":{"message":"Rate limit reached"}}', 'zh', {provider: 'deepseek'});
        expect(d.code).toBe('ai_rate_limited');
        expect(d.nextAction).toContain('重生笔记');
    });
    it('says too many files were picked and is not retryable', () => {
        const d = diagnoseTaskError('一次最多提交 5 个文件，这次选了 8 个。请分成几批提交。', 'zh');
        expect(d.code).toBe('too_many_files');
        expect(d.retryable).toBe(false);
    });
    it('blames the Feishu folder, not a missing task', () => {
        const d = diagnoseTaskError('飞书导出失败：folder not found (404)', 'zh');
        expect(d.code).toBe('feishu_folder_not_found');
    });
});
