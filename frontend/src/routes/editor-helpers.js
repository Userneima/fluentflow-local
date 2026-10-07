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

// ---- The note while it is being written --------------------------------------
//
// A task counts as finished once its transcript is in, but with the automatic
// note the note itself is still on its way: the result says
// summary_status 'pending' (local_intake_flow.mark_note_running) and, while
// Claude is reading the frames, visual_note.status 'running'. The editor polls
// the job until neither is true any more.

export const NOTE_POLL_INTERVAL_MS = 5000;

const VISUAL_NOTE_WRITTEN_FROM = 'debreath_media_note';

// `job` is optional: the editor only holds the result, a poll also has the job
// row, whose stage is 'note' for exactly this window.
export const noteIsBeingWritten = (result, job = null) => {
    if (!result) return false;
    if (result.summary_status === 'pending') return true;
    if (result.visual_note?.status === 'running') return true;
    return job?.stage === 'note';
};

// Whether a poll answer settles the note. Only the result is consulted: the job
// row's stage is written after the result, and a stage left at 'note' by an
// interrupted service would otherwise keep the page polling forever.
export const noteWritingSettled = (result) => !!result && !noteIsBeingWritten(result);

// Whether the Claude run started from this page (at `requestedAtMs`) has ended.
// A previous run's 'completed' is still on the result until the new run marks
// itself running, so a terminal status only counts when that run started after
// the request. Server stamps have whole seconds, hence the floor.
export const claudeRewriteSettled = (result, {requestedAtMs = 0, sawRunning = false} = {}) => {
    const state = result?.visual_note || {};
    if (!['completed', 'failed'].includes(state.status)) return false;
    if (result.summary_status === 'pending') return false;
    if (sawRunning) return true;
    const startedAt = Date.parse(state.started_at || '');
    return Number.isFinite(startedAt) && startedAt >= Math.floor(requestedAtMs / 1000) * 1000;
};

// Whether the note on screen was written by Claude from the frames. A text
// rewrite replaces it with prose only, and its screenshots go with it.
export const noteCameFromClaude = (result) => {
    if (!result) return false;
    // The stamp is set when a Claude note becomes the task's note and cleared
    // when an older note is put back, so once present it is the answer. Older
    // results without it fall back to the run record.
    if (result.summary_written_from !== undefined) {
        return result.summary_written_from === VISUAL_NOTE_WRITTEN_FROM;
    }
    const state = result.visual_note || {};
    return state.status === 'completed' && state.promoted !== false;
};

// The note's part of a fresh result. The transcript side of the page has its
// own sync and its own unsaved edits, so a finished note must not carry an
// older copy of the transcript in with it.
const NOTE_FIELD_PREFIXES = ['summary_', 'note_mode_', 'visual_note'];
const NOTE_FIELDS = new Set([
    'artifacts', 'requested_note_mode', 'resolved_note_mode', 'chapter_coverage',
    'prompt_preset', 'prompt_preset_label',
]);
export const mergeNoteFields = (current, fresh) => {
    if (!fresh) return current;
    const picked = {};
    Object.keys(fresh).forEach((key) => {
        if (NOTE_FIELDS.has(key) || NOTE_FIELD_PREFIXES.some((prefix) => key.startsWith(prefix))) {
            picked[key] = fresh[key];
        }
    });
    return {...current, ...picked};
};

// What the regenerate dialog says about who will write the new note, and what
// the user loses by it.
export const regenerateDialogCopy = ({writerKind, writerLabel, currentNoteFromClaude}, lang = 'zh') => {
    const zh = lang === 'zh';
    if (writerKind === 'claude') {
        return {
            desc: zh
                ? `将由 ${writerLabel} 结合剪后视频的画面和当前转录重写笔记，写好后替换右侧笔记，被替换的笔记可以恢复。需要几分钟，期间可以离开这个页面。`
                : `${writerLabel} will rewrite the note from the cut video's frames and the current transcript, then replace the note on the right. The replaced note can be restored. This takes a few minutes; you can leave this page meanwhile.`,
            warning: '',
        };
    }
    return {
        desc: zh
            ? `将由 ${writerLabel} 基于当前转录重写笔记，并替换右侧笔记正文；不会重新转录音频。`
            : `${writerLabel} will rewrite the note from the current transcript and replace the note on the right. The audio is not transcribed again.`,
        warning: currentNoteFromClaude
            ? (zh
                ? '当前这份笔记是 Claude 结合画面写的。这次只按文字重写，笔记里的截图不会保留。'
                : 'The current note was written by Claude from the frames. This rewrite reads text only, so the screenshots in the note will not be kept.')
            : '',
    };
};

// A refusal from the Claude rewrite, in words the user can act on. Every 409
// carries the backend's own reason; the busy queue is the common one.
export const visualNoteStartErrorMessage = (err, lang = 'zh') => {
    const zh = lang === 'zh';
    const detail = String(err?.message || '').trim();
    if (err?.status === 409) {
        if (!detail || detail.includes('队列')) {
            return zh
                ? '队列里还有任务在处理，等它处理完再重生笔记。'
                : 'Another task is still being processed. Regenerate the note once the queue is empty.';
        }
        return zh ? `这次没法用 Claude 重写笔记：${detail}` : `Claude cannot rewrite this note: ${detail}`;
    }
    return detail || (zh ? '重生笔记没能开始' : 'The note rewrite did not start');
};
