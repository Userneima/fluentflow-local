const asObject = (value) => (
    value && typeof value === 'object' && !Array.isArray(value) ? value : {}
);

const asArray = (value) => (Array.isArray(value) ? value : []);

const text = (value) => String(value || '').trim();

const normalizeStatus = (value) => {
    const status = text(value).toLowerCase();
    return ['completed', 'no_changes', 'failed', 'unavailable'].includes(status) ? status : '';
};

const normalizeCorrections = (value) => (
    asArray(value)
        .filter((item) => item && typeof item === 'object')
        .map((item) => ({
            ...item,
            original_text: text(item.original_text || item.original || item.before),
            corrected_text: text(item.corrected_text || item.corrected || item.after),
            reason: text(item.reason),
        }))
        .filter((item) => item.original_text || item.corrected_text)
);

export const transcriptCorrectionInfo = (source={}) => {
    const payload = asObject(source);
    const transcript = asObject(payload.transcript);
    const correction = Object.keys(asObject(payload.transcript_correction)).length
        ? asObject(payload.transcript_correction)
        : asObject(transcript.correction);
    const corrections = normalizeCorrections(
        payload.transcript_corrections || transcript.corrections
    );
    const correctedText = text(payload.corrected_transcript_text || transcript.corrected_text);
    const correctedSegments = asArray(payload.corrected_segments || transcript.corrected_segments);
    const appliedCount = Number(correction.applied_count ?? corrections.length) || corrections.length;
    const status = normalizeStatus(
        payload.transcript_correction_status
        || correction.status
        || transcript.correction_status
    ) || (appliedCount > 0 || correctedText || correctedSegments.length > 0 ? 'completed' : '');
    const noteInputSource = text(
        payload.note_generation_transcript_source
        || transcript.note_input_source
        || correction.note_input_source
    );
    const noteUsesCorrected = noteInputSource === 'corrected_transcript' || correction.note_input_applied === true;
    const ran = !!(
        status
        || Object.keys(correction).length
        || corrections.length
        || correctedText
        || correctedSegments.length
    );

    return {
        ran,
        status,
        correction,
        corrections,
        correctedText,
        correctedSegments,
        appliedCount,
        rejectedCount: Number(correction.rejected_count) || 0,
        segmentCount: Number(correction.segment_count) || 0,
        minConfidence: Number(correction.min_confidence) || null,
        provider: text(correction.provider),
        model: text(correction.model),
        error: text(correction.error),
        noteInputSource: noteInputSource || 'transcript_text',
        noteUsesCorrected,
        hasAcceptedCorrections: appliedCount > 0 || corrections.length > 0 || !!correctedText || correctedSegments.length > 0,
    };
};

export const transcriptCorrectionStatusText = (info, lang='zh') => {
    const isZh = lang === 'zh';
    if (!info?.ran) return '';
    if (info.noteUsesCorrected && info.hasAcceptedCorrections) {
        return isZh
            ? `笔记已基于修正后的字幕生成，接受了 ${info.appliedCount || info.corrections.length} 处高置信修正。`
            : `The note was generated from the corrected transcript with ${info.appliedCount || info.corrections.length} accepted high-confidence correction(s).`;
    }
    if (info.hasAcceptedCorrections) {
        return isZh
            ? `已完成保守字幕纠错，接受了 ${info.appliedCount || info.corrections.length} 处高置信修正。`
            : `Conservative transcript correction accepted ${info.appliedCount || info.corrections.length} high-confidence correction(s).`;
    }
    if (info.status === 'no_changes' || info.status === 'completed') {
        return isZh
            ? '已检查字幕，未发现高置信字幕错误。'
            : 'Transcript checked; no high-confidence subtitle errors were found.';
    }
    if (info.status === 'failed') {
        return isZh
            ? `字幕纠错未完成，笔记使用原始字幕。${info.error || ''}`.trim()
            : `Transcript correction did not complete; the note used the original transcript. ${info.error || ''}`.trim();
    }
    if (info.status === 'unavailable') {
        return isZh
            ? `字幕纠错不可用，笔记使用原始字幕。${info.error || ''}`.trim()
            : `Transcript correction was unavailable; the note used the original transcript. ${info.error || ''}`.trim();
    }
    return isZh ? '字幕纠错状态已记录。' : 'Transcript correction status is recorded.';
};

export const formatCorrectionConfidence = (value, lang='zh') => {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return lang === 'zh' ? '未记录' : 'Not recorded';
    return `${Math.round(Math.min(1, n) * 100)}%`;
};
