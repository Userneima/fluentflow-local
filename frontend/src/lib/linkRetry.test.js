import {describe, expect, it} from 'vitest';
import {retryInputForJob, retryOptionsForJob, submitIgnoredExtraUrls} from './linkRetry.js';

// Written from what "submit again" on a failed link task has to do for the
// person pressing it: send the same link, with the same choices they made the
// first time, except the browser login, which is whatever Settings says now.

const LONG_SHARE_TEXT = `${'这期讲得特别好，'.repeat(30)} https://v.douyin.com/abcDEF12/ 复制此链接，打开抖音搜索，直接观看视频！`;

const failedLinkJob = (metadata = {}) => ({
    task_id: 'link-1',
    source_type: 'video_link',
    status: 'failed',
    metadata: {
        video_source_input_preview: LONG_SHARE_TEXT.slice(0, 200),
        queue_options: {
            export_to_lark: 'true',
            lark_export_route: 'local_cli',
            lark_via_cli: 'true',
            title: '我起的名字',
            ai_provider: 'qwen',
            ai_model: 'qwen3.7-plus',
            system_prompt: '只写要点',
            note_mode: 'high_fidelity',
            prompt_preset: 'lecture',
            prompt_preset_label: '讲座',
            generate_visuals: 'true',
            stt_model: 'large',
            stt_speed: 'accurate',
            stt_language: 'zh',
            speaker_diarization: 'true',
            voice_enhance: 'true',
            skip_summary: 'true',
            duration_limit_seconds: '7200',
            cookies_from_browser: 'safari',
        },
        ...metadata,
    },
});

describe('the link a retry sends', () => {
    it('is the full extracted link, not the share text cut at 200 characters', () => {
        const job = failedLinkJob({video_source_url: 'https://v.douyin.com/abcDEF12/'});
        // The cut preview has lost the link entirely.
        expect(job.metadata.video_source_input_preview).not.toContain('v.douyin.com');
        expect(retryInputForJob(job)).toBe('https://v.douyin.com/abcDEF12/');
    });

    it('falls back to the stored input when an older record has no extracted link', () => {
        const job = failedLinkJob({video_source_input_preview: 'https://www.bilibili.com/video/BV1xx411c7mD'});
        expect(retryInputForJob(job)).toBe('https://www.bilibili.com/video/BV1xx411c7mD');
    });

    it('is empty when the record kept nothing to resubmit', () => {
        expect(retryInputForJob({metadata: {}})).toBe('');
    });
});

describe('the options a retry sends', () => {
    it('carries every choice the first submission stored', () => {
        const options = retryOptionsForJob(failedLinkJob(), {cookiesBrowser: 'chrome'});
        expect(options).toMatchObject({
            exportToLark: true,
            larkExportRoute: 'local_cli',
            larkViaCli: true,
            title: '我起的名字',
            aiProvider: 'qwen',
            aiModel: 'qwen3.7-plus',
            systemPrompt: '只写要点',
            noteMode: 'high_fidelity',
            promptPreset: 'lecture',
            promptPresetLabel: '讲座',
            generateVisuals: true,
            sttModel: 'large',
            sttSpeed: 'accurate',
            sttLanguage: 'zh',
            speakerDiarization: true,
            voiceEnhance: true,
            skipSummary: true,
            durationLimitSeconds: 7200,
        });
    });

    it("uses today's browser login, not the one stored on the failed job", () => {
        expect(retryOptionsForJob(failedLinkJob(), {cookiesBrowser: 'chrome'}).cookiesFromBrowser).toBe('chrome');
        // Turned off since: the retry goes without a login.
        expect(retryOptionsForJob(failedLinkJob(), {cookiesBrowser: ''}).cookiesFromBrowser).toBe('');
    });

    it('leaves switches off that were off', () => {
        const options = retryOptionsForJob({metadata: {queue_options: {}}});
        expect(options.generateVisuals).toBe(false);
        expect(options.voiceEnhance).toBe(false);
        expect(options.exportToLark).toBe(false);
        expect(options.sttLanguage).toBe('auto');
    });
});

describe('a pasted text with several links', () => {
    it('is noticed wherever the submit response reports it', () => {
        expect(submitIgnoredExtraUrls({ok: true, extra_urls_ignored: true, job: {}})).toBe(true);
        expect(submitIgnoredExtraUrls({job: {extra_urls_ignored: true}})).toBe(true);
        expect(submitIgnoredExtraUrls({job: {metadata: {extra_urls_ignored: true}}})).toBe(true);
        expect(submitIgnoredExtraUrls({ok: true, job: {}})).toBe(false);
    });
});
