// What "submit again" sends for a failed link task.
//
// A retry is the same request as the first one, with one exception: the
// browser-login choice is today's, not the one stored on the job. Someone whose
// link failed because no login was set goes to Settings, picks a browser, and
// presses "submit again"; sending the stored "none" would fail the same way.

const truthy = (value) => value === true || value === 'true' || value === '1' || value === 1;

const text = (value) => String(value ?? '').trim();

// The link to resubmit. The extracted link comes first because it is stored at
// full length; the input preview is cut at 200 characters, and a long share
// text ("看看这个视频 …… https://v.douyin.com/…") loses its link past that cut.
export const retryInputForJob = (job) => {
    const metadata = job?.metadata || {};
    const videoSource = metadata.video_source || {};
    const candidates = [
        metadata.video_source_url,
        videoSource.source_url,
        videoSource.webpage_url,
        metadata.raw_input,
        metadata.video_source_input_preview,
        videoSource.url,
    ];
    for (const candidate of candidates) {
        const value = text(candidate);
        if (value) return value;
    }
    return '';
};

// Every option the first submission stored, in the camelCase the API client
// takes. `cookiesBrowser` is the current settings choice ('' when off).
export const retryOptionsForJob = (job, {cookiesBrowser = ''} = {}) => {
    const metadata = job?.metadata || {};
    const queueOptions = metadata.queue_options;
    const base = queueOptions && typeof queueOptions === 'object' ? queueOptions : metadata;
    const durationLimit = Number(base.duration_limit_seconds);
    const options = {
        exportToLark: truthy(base.export_to_lark),
        larkExportRoute: base.lark_export_route,
        larkViaCli: truthy(base.lark_via_cli),
        title: text(base.title) || undefined,
        folderToken: text(base.folder_token) || undefined,
        skipSummary: truthy(base.skip_summary),
        aiProvider: base.ai_provider,
        aiModel: base.ai_model,
        systemPrompt: text(base.system_prompt) || undefined,
        noteMode: base.note_mode,
        promptPreset: base.prompt_preset,
        promptPresetLabel: base.prompt_preset_label,
        generateVisuals: truthy(base.generate_visuals),
        sttProvider: base.stt_provider,
        sttModel: base.stt_model,
        sttSpeed: base.stt_speed,
        sttLanguage: base.stt_language || 'auto',
        speakerDiarization: truthy(base.speaker_diarization),
        voiceEnhance: truthy(base.voice_enhance),
        durationLimitSeconds: Number.isFinite(durationLimit) && durationLimit > 0 ? durationLimit : undefined,
        cookiesFromBrowser: text(cookiesBrowser),
    };
    return options;
};

// Whether the service used only the first of several links in the pasted text.
// Read wherever the submit response puts it: on the response, or on its job.
export const submitIgnoredExtraUrls = (response) => {
    const job = response?.job || {};
    return [response?.extra_urls_ignored, job.extra_urls_ignored, job.metadata?.extra_urls_ignored]
        .some((value) => value === true || value === 'true');
};

export const extraUrlsIgnoredNotice = (lang) => (
    lang === 'zh'
        ? '粘贴的内容里有好几个链接，这次只处理了第一个。其他链接请分开提交。'
        : 'The pasted text had several links; only the first one was used. Submit the others separately.'
);
