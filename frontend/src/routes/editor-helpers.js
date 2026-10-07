// Pure helper functions extracted from editor.jsx (formatting and URL/file
// normalization). No React/JSX dependency.
import {normalizeSttProvider, noteGenerationDiagnosis} from '../app/shared.jsx';


export const jobOptionsForResult = (result) => (
    normalizeSttProvider(result?.stt_provider) === 'local'
    || result?.playback_audio_storage === 'local'
    || result?.source_file_storage === 'local'
        ? {sttProvider: 'local'}
        : {}
);

export const isLikelyVideoFile = (fileOrName) => {
    if (!fileOrName) return false;
    if (typeof fileOrName === 'object' && fileOrName.type) return String(fileOrName.type).startsWith('video/');
    const name = typeof fileOrName === 'string' ? fileOrName : fileOrName.name || '';
    return /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(name);
};

export const isVideoResultSource = (result, sourceFile) => {
    if (!result) return false;
    if (isLikelyVideoFile(sourceFile || result.filename || result.display_title)) return true;
    const source = String(result.source || result.source_type || '').trim().toLowerCase();
    return ['video', 'video_link', 'youtube', 'douyin'].includes(source);
};

export const shouldKeepVideoReviewMounted = ({activeReviewMode}) => activeReviewMode === 'video';

// Whether this task went through the automatic flow: the gaps came out before
// anything read the file, so the media, the subtitles and the note are all one
// shortened file. When true the page has nothing to ask the user for — the two
// manual entries (remove the silence / rewrite the note from the cut version) are
// steps that already happened, and leaving them on screen makes a finished flow
// read like an unfinished one.
export const isAutoCutFlow = (result) => (
    result?.debreath?.ran_before_transcription === true
    && result?.debreath?.used_for_transcription === true
    && result?.transcript_media === 'debreath_media'
);

// The numbers for the one-line record of that. Null when there is nothing to
// report, so the caller renders nothing rather than an empty bar.
export const cutFlowSummary = (result) => {
    if (!isAutoCutFlow(result)) return null;
    const plan = result?.debreath?.plan || {};
    const removedSeconds = Number(plan.removed_seconds) || 0;
    const sourceSeconds = Number(plan.source_duration_seconds) || 0;
    const keptSeconds = Number(plan.kept_seconds) || Math.max(0, sourceSeconds - removedSeconds);
    return {
        cutCount: Number(plan.cut_count) || 0,
        removedSeconds,
        removedPercent: Number(plan.removed_percent) || 0,
        sourceSeconds,
        keptSeconds,
        mediaArtifact: result?.artifacts?.debreath_media || null,
        renderVerified: result?.debreath?.render_verified !== false,
    };
};

// Which stored file the player should load: the one this transcript belongs to.
//
// Not a preference. The transcript's timestamps drive every seek, every
// highlighted line and every jump from the note, and they only mean something
// against one file. Playing the recording while the transcript came from the
// shortened version puts every click progressively further off — and nothing on
// screen would say so, which is why the result records which file it is rather
// than leaving this to be guessed from whichever artifacts exist.
export const playbackMediaChoice = (result) => {
    if (!result?.task_id) return null;
    const cutArtifact = result?.artifacts?.debreath_media;
    const belongsToCut = result?.transcript_media === 'debreath_media'
        || result?.playback_media_kind === 'debreath_media';
    if (belongsToCut && cutArtifact) {
        return {
            kind: 'debreath_media',
            filename: cutArtifact.filename || result.filename || 'debreath-media',
            isCut: true,
        };
    }
    if (result?.source_file_available) {
        return {kind: 'source', filename: result?.filename || 'source', isCut: false};
    }
    return null;
};

export const localSourceFileMatchesResult = (file, result) => {
    if (!file || !result) return false;
    const fingerprint = result.source_fingerprint || {};
    const expectedName = String(fingerprint.source_filename || result.filename || '').trim();
    const expectedSize = Number(fingerprint.source_size_bytes || 0);
    const nameMatches = expectedName && file.name === expectedName;
    const sizeMatches = expectedSize > 0 && file.size === expectedSize;
    if (nameMatches && sizeMatches) return true;
    if (sizeMatches && !expectedName) return true;
    return false;
};

export const summaryFailureNextStep = (result, lang) => {
    if (!(result?.summary_status === 'failed' || result?.summary_error)) return '';
    const diagnosis = noteGenerationDiagnosis(result, lang);
    const next = diagnosis.nextAction ? ` ${lang === 'zh' ? '下一步：' : 'Next: '}${diagnosis.nextAction}` : '';
    return lang === 'zh'
        ? `${diagnosis.title}：${diagnosis.detail}${next}`
        : `${diagnosis.title}: ${diagnosis.detail}${next}`;
};

export const formatElapsedMinuteSecond = (seconds) => {
    const n = Math.max(0, Math.floor(Number(seconds) || 0));
    const m = Math.floor(n / 60);
    const s = n % 60;
    return `${String(m).padStart(2, '0')}m${String(s).padStart(2, '0')}s`;
};

export const formatSttOriginalRatio = (factor, lang) => {
    const n = Number(factor);
    if (!Number.isFinite(n) || n <= 0) return '';
    const pct = Math.max(1, Math.round(n * 100));
    return lang === 'zh' ? `原 ${pct}%` : `${pct}% of original`;
};

export const downloadBrowserFile = (file, fallbackName = 'download') => {
    const url = URL.createObjectURL(file);
    const a = document.createElement('a');
    a.href = url;
    a.download = file?.name || fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
};
