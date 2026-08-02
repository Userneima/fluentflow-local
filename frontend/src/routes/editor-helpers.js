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

// What the regenerate button says while the server streams its steps.
//
// The percentage alone is not reassuring on a nine-minute run — "42%" could
// still be stuck. Naming the running step ("撰写章节 3/7") is what separates a
// slow run from a hung one, so the label leads with the step and only shows
// n/m when the step actually fans out.
export const regenerateProgressLabel = (progress, {fallback = ''} = {}) => {
    const label = String(progress?.label || '').trim();
    if (!label) return fallback;
    const total = Number(progress?.total) || 0;
    const completed = Math.min(Number(progress?.completed) || 0, total);
    return total > 1 ? `${label} ${completed}/${total}` : label;
};

// Why the editor refuses to touch a `result_partial` payload.
//
// A job-list row and a browser-cache row both carry 240-char previews under
// the same field names a real record uses. Opening one looks fine — the note
// panel just renders short — but the first keystroke arms the 800ms autosave,
// which PATCHes the preview back over the full note. That is how a 13k-char
// note became a 240-char stub with no way to recover it.
//
// So: partial in, no writes out. 'loading' while hydration is still fetching
// the real record, 'unavailable' once it has failed, null when the payload is
// the record itself and editing is safe.
//
// `hydratable` false means no server record exists to recover (an imported
// browser-history entry). Locking those would strand them read-only forever,
// and there is no full note behind them to protect.
export const resultEditingLock = (result, {hydrationFailed = false, hydratable = true} = {}) => {
    if (!result?.result_partial || !hydratable) return null;
    return hydrationFailed ? 'unavailable' : 'loading';
};

// Ordered fallback chain for attaching playable media to a stored result.
//
// A browser cannot reopen the file the user originally picked, and the picked
// File only lives in this page's memory — so after a restart the ONLY way back
// to playback is the copy the local service retained on disk. Streaming it via
// a media grant comes first: one URL serves both the inline player and video
// review, the media element fetches ranges instead of the whole file, and it
// keeps working across restarts without asking the user to reselect anything.
// Blob downloads stay behind it as fallbacks for editions or records where
// streaming is unavailable.
export const mediaSourcePlan = (result, {localFile = null, canPersistResult = true} = {}) => {
    if (!result) return [];
    if (localFile) return [{kind: 'local-file', file: localFile}];
    const taskId = result.task_id;
    if (!taskId) return [];
    const plan = [];
    const storedSource = !!result.source_file_available;
    const sourceName = result.filename || 'source';
    if (storedSource) {
        plan.push({kind: 'stream', mediaKind: isVideoResultSource(result, null) ? 'video' : 'audio'});
    }
    const playbackArtifact = result.artifacts?.playback_audio;
    if (playbackArtifact) {
        plan.push({kind: 'artifact', filename: playbackArtifact.filename || `${sourceName}_audio.mp3`});
    }
    if (storedSource && canPersistResult) {
        plan.push({kind: 'download', filename: sourceName});
    }
    return plan;
};

export const activeTranscriptSegmentIndex = (segments, currentTime) => {
    let low = 0;
    let high = Math.max(0, (segments?.length || 0) - 1);
    const time = Number(currentTime) || 0;
    while (low <= high) {
        const index = Math.floor((low + high) / 2);
        const current = segments[index] || {};
        const start = Number(current.start) || 0;
        const nextStart = Number(segments[index + 1]?.start);
        const end = Number(current.end) || (Number.isFinite(nextStart) ? nextStart : start + 6);
        if (time < start) high = index - 1;
        else if (time >= end) low = index + 1;
        else return index;
    }
    return -1;
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
