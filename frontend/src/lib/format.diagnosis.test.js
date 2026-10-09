import {describe, expect, it} from 'vitest';
import {diagnoseTaskError, friendlyTaskError, larkExportToastText, taskErrorContextForJob} from './format.js';

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
        expect(friendlyTaskError('HTTP 401', 'zh', taskErrorContextForJob(job))).toContain('通义千问（阿里云百炼）');
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

// The editor and the task page show the same Feishu reasons the backend gives:
// each kind of failure reads differently and names the next step, using the
// settings that exist (「飞书导出路线」, 「本机身份导出」, 「飞书应用导出」).
describe('a Feishu export failure', () => {
    const cases = [
        ['lark-cli not found. Install @larksuite/cli', 'lark_cli_not_installed', 'npm install -g @larksuite/cli'],
        ['「本机身份导出」要用飞书命令行工具 lark-cli，这台电脑上没有找到它。', 'lark_cli_not_installed', 'lark-cli auth login'],
        ['lark-cli 失败 [type=authentication]：not logged in', 'lark_cli_login_required', 'lark-cli auth login'],
        ['这台电脑上的 lark-cli 没有可用的登录身份（没登录或登录已过期）。', 'lark_cli_login_required', 'lark-cli auth login'],
        ['lark-cli 失败 [subtype=missing_scope missing_scopes=wiki:node:create]：x', 'feishu_missing_scope', 'lark-cli auth login --scope "wiki:node:create"'],
        ['Lark create-doc error: code=99991672 msg=scopes is required: [docx:document:create]', 'feishu_missing_scope', 'docx:document:create'],
        ['Lark credentials not set: provide app_id/app_secret', 'feishu_app_credentials_missing', '高级 · 其他凭证'],
        ["Feishu tenant token error: {'code': 10014, 'msg': 'app secret invalid'}", 'feishu_app_credentials_invalid', '凭证与基础信息'],
        ['Feishu create-doc HTTP 404: folder not found', 'feishu_folder_not_found', '飞书文件夹链接'],
        ['Feishu 写入块失败: code=99991400 msg=request trigger frequency limit', 'feishu_rate_limited', '过一两分钟'],
    ];

    it.each(cases)('%s → %s', (message, code, nextStep) => {
        const d = diagnoseTaskError(message, 'zh');
        expect(d.code).toBe(code);
        expect(`${d.detail} ${d.nextAction}`).toContain(nextStep);
        expect(`${d.detail} ${d.nextAction}`).not.toContain('用本机 lark-cli 导出到「我的文档库」');
    });

    it('names a half-written document so it can be opened or deleted', () => {
        const d = diagnoseTaskError('Feishu 写入块失败: code=99991400；已建好的半截文档：https://x.feishu.cn/docx/abc', 'zh');
        expect(d.detail).toContain('https://x.feishu.cn/docx/abc');
    });

    it('keeps an unknown Feishu failure in its own words', () => {
        const d = diagnoseTaskError('Feishu create-doc error: document locked by admin', 'zh');
        expect(d.code).toBe('feishu_export_failed');
        expect(d.detail).toContain('document locked by admin');
    });
});

describe('the export toast', () => {
    const t = (key) => ({'edit.exportDone': '已导出到飞书', 'edit.exportImagesMissing': '{n} 张截图没传上去'}[key] || key);

    it('says the export landed', () => {
        expect(larkExportToastText({url: 'u', image_count: 2, image_upload_count: 2}, t)).toBe('已导出到飞书');
    });

    it('counts the screenshots that did not upload', () => {
        expect(larkExportToastText({url: 'u', image_count: 5, image_upload_count: 2}, t)).toBe('已导出到飞书 · 3 张截图没传上去');
    });
});

describe('another device without the access token', () => {
    it('says to enter the token instead of showing a generic failure', () => {
        const d = diagnoseTaskError('从其他设备访问需要访问令牌：请在这台设备的「菜单 → Agent 接入」里填入访问令牌（Agent API 访问令牌无效。）', 'zh');
        expect(d.code).toBe('lan_access_token_missing');
        expect(d.retryable).toBe(false);
        expect(d.nextAction).toContain('Agent 接入');
    });
});
