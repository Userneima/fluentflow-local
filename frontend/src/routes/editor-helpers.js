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

// The sentence playing at `time`: the last one starting at or before it, as
// long as it has not ended (a sentence without an end lasts until the next one
// starts, or six seconds). -1 between sentences. Binary search, since it runs
// on every tick of the player against lists of several thousand sentences.
export const findSegmentAt = (segments, time) => {
    const list = Array.isArray(segments) ? segments : [];
    let lo = 0;
    let hi = list.length - 1;
    let found = -1;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if ((Number(list[mid]?.start) || 0) <= time) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    if (found < 0) return -1;
    const start = Number(list[found]?.start) || 0;
    const nextStart = Number(list[found + 1]?.start);
    const end = Number(list[found]?.end) || (Number.isFinite(nextStart) ? nextStart : start + 6);
    return time < end ? found : -1;
};

// Whether the browser sizes a textarea to its text by itself (CSS
// field-sizing). Chrome, Edge and Safari do; Firefox does not yet.
export const textareaSizesItself = () => typeof CSS !== 'undefined' && !!CSS.supports?.('field-sizing', 'content');

// For a browser that does not: size every box in one pass, all writes, then
// all reads, then all writes, so the page is laid out once. Measuring them one
// by one laid the page out once per box: 11.6 s for 7104 sentences.
export const sizeTextareas = (nodes) => {
    const list = Array.from(nodes || []);
    list.forEach((node) => { node.style.height = 'auto'; });
    const heights = list.map((node) => node.scrollHeight);
    list.forEach((node, index) => { node.style.height = `${heights[index]}px`; });
};

// How many places of the cut wait for the user's judgement: items of the
// report's judgement group without an answer. The full result carries the
// report and the answers; a records-list copy carries only the count.
export const cutItemsToJudge = (result) => {
    const survey = result?.debreath?.survey;
    if (!survey || typeof survey !== 'object') return 0;
    const sections = Array.isArray(survey.report?.sections) ? survey.report.sections : null;
    if (!sections) return Math.max(0, Number(survey.to_judge) || 0);
    const verdicts = survey.verdicts && typeof survey.verdicts === 'object' ? survey.verdicts : {};
    return sections
        .filter((section) => section?.judge)
        .flatMap((section) => section.items || [])
        .filter((item) => item && !verdicts[item.key])
        .length;
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

// ---- The note while it is being written --------------------------------------
//
// A task counts as finished once its transcript is in, but with the automatic
// note the note itself is still on its way: the result says
// summary_status 'pending' (local_intake_flow.mark_note_running) and, while
// Claude is reading the frames, visual_note.status 'running'. The editor polls
// the job until neither is true any more.

export const NOTE_POLL_INTERVAL_MS = 5000;

// Notes Claude wrote from the frames: from the cut file, or from the original
// recording when the cut was declined. The service also sends a ready answer,
// `note_from_frames`, which wins when present.
const VISUAL_NOTE_WRITTEN_FROM = new Set(['debreath_media_note', 'source_media_note']);

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
    if (typeof result.note_from_frames === 'boolean') return result.note_from_frames;
    // The stamp is set when a Claude note becomes the task's note and cleared
    // when an older note is put back, so once present it is the answer. Older
    // results without it fall back to the run record.
    if (result.summary_written_from !== undefined) {
        return VISUAL_NOTE_WRITTEN_FROM.has(result.summary_written_from);
    }
    const state = result.visual_note || {};
    return state.status === 'completed' && state.promoted !== false;
};

// The note's part of a fresh result. The transcript side of the page has its
// own sync and its own unsaved edits, so a finished note must not carry an
// older copy of the transcript in with it.
// The Feishu fields ride along: an automatic export runs right after the note,
// and its link or its failure is what the person looks for next.
const LARK_FIELDS = ['lark_response', 'lark_error', 'lark_doc_title', 'feishu_doc_url'];
const NOTE_FIELD_PREFIXES = ['summary_', 'note_mode_', 'visual_note'];
const NOTE_FIELDS = new Set([
    'artifacts', 'requested_note_mode', 'resolved_note_mode', 'chapter_coverage',
    'prompt_preset', 'prompt_preset_label',
    'note_written_by', 'note_fallback_reason', 'deadline_hit',
    // The version travels with the note it belongs to: a save made on the
    // note taken from the service must name that note's version.
    'note_version',
    ...LARK_FIELDS,
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

// Only the export's outcome, for a note that is already on the page and may
// have been edited since.
export const mergeLarkFields = (current, fresh) => {
    if (!fresh) return current;
    const picked = {};
    LARK_FIELDS.forEach((key) => {
        if (key in fresh) picked[key] = fresh[key];
    });
    return {...current, ...picked};
};

// ---- The automatic Feishu export --------------------------------------------
//
// With "处理完成后自动导出到飞书" on, the export runs after the note is written,
// so it lands after the note poll would have stopped. The page keeps asking
// until the result names the document or the failure, for at most this long.
export const AUTO_EXPORT_WAIT_MS = 10 * 60 * 1000;

const truthyOption = (value) => value === true || ['true', '1', 'yes', 'on'].includes(String(value ?? '').trim().toLowerCase());

export const autoExportRequested = (job) => {
    const options = job?.metadata?.queue_options;
    return !!options && typeof options === 'object' && truthyOption(options.export_to_lark);
};

export const larkExportSettled = (result) => !!(result?.lark_response || result?.lark_error);

// Whether a poll answer means the export is still to come.
export const autoExportPending = (job) => autoExportRequested(job) && !larkExportSettled(job?.result);

// What the regenerate dialog says about who will write the new note, and what
// the user loses by it.
// What 写笔记 will do for this task, said before it does it. The dialog opens
// only for a task without a note (decision 6.33), so there is nothing to
// replace. Claude writes from the cut file's frames when the task has them;
// when the service cannot say, both ways are offered; when the frames are out
// of reach the text model writes instead, and the dialog says why.
//
//   primary      'claude' | 'text': what the confirm button runs
//   alternative  {label, disabled, hint} | null: the second choice
export const TEXT_WRITE_CHOICE_ZH = '改用文本模型按文字写（笔记里不带截图）';
const TEXT_WRITE_CHOICE_EN = 'Write from the text with the text model instead (no screenshots in the note)';

export const regenerateDialogCopy = ({
    writerKind,
    writerLabel,
    frameNote = null,
    textWriterLabel = '',
    textWriterReady = true,
}, lang = 'zh') => {
    const zh = lang === 'zh';
    const noTextKey = zh
        ? '还没有填文本模型的 Key，没法改用文本模型。'
        : 'No text-model key yet, so the text model cannot be used.';
    if (frameNote?.running) {
        // A note is already being written. Starting another, by either
        // writer, would race it for the same note.
        return {
            desc: zh
                ? '这个任务的笔记正在写，写好会自动出现在右侧。'
                : 'This note is already being written and will appear on the right when it is done.',
            warning: '',
            primary: writerKind === 'claude' ? 'claude' : 'text',
            confirmDisabled: true,
            alternative: null,
        };
    }
    if (writerKind === 'claude' && frameNote?.available === false) {
        const reason = String(frameNote.reason || '').trim().replace(/[。.]+$/, '')
            || (zh ? '找不到可用的剪后视频' : 'no usable cut video was found');
        const label = textWriterLabel || (zh ? '文本模型' : 'the text model');
        return {
            desc: zh
                ? `这个任务没法让 Claude 结合画面写：${reason}。这次改用 ${label} 按当前转录写，笔记里不带截图。`
                : `Claude cannot write this note from the frames: ${reason}. ${label} will write it from the current transcript instead, without screenshots.`,
            warning: textWriterReady ? '' : (zh
                ? '还没有填文本模型的 Key，这个任务现在写不了笔记。去设置填写后再试。'
                : 'No text-model key yet, so this note cannot be written now. Add one in Settings and try again.'),
            primary: 'text',
            confirmDisabled: !textWriterReady,
            alternative: null,
        };
    }
    if (writerKind === 'claude') {
        return {
            desc: zh
                ? `将由 ${writerLabel} 结合剪后视频的画面和当前转录写笔记，写好会出现在右侧。需要几分钟，期间可以离开这个页面。`
                : `${writerLabel} will write the note from the cut video's frames and the current transcript; it appears on the right when done. This takes a few minutes; you can leave this page meanwhile.`,
            warning: '',
            primary: 'claude',
            confirmDisabled: false,
            alternative: {
                label: zh ? TEXT_WRITE_CHOICE_ZH : TEXT_WRITE_CHOICE_EN,
                disabled: !textWriterReady,
                hint: textWriterReady ? '' : noTextKey,
            },
        };
    }
    return {
        desc: zh
            ? `将由 ${writerLabel} 按当前转录写笔记；不会重新转录音频。`
            : `${writerLabel} will write the note from the current transcript. The audio is not transcribed again.`,
        warning: '',
        primary: 'text',
        confirmDisabled: false,
        alternative: null,
    };
};

export const visualNoteStartErrorMessage = (err, lang = 'zh') => {
    const zh = lang === 'zh';
    const detail = String(err?.message || '').trim();
    if (err?.status === 409) {
        if (!detail || detail.includes('队列')) {
            return zh
                ? '队列里还有任务在处理，等它处理完再写笔记。'
                : 'Another task is still being processed. Write the note once the queue is empty.';
        }
        return zh ? `这次没法用 Claude 写笔记：${detail}` : `Claude cannot write this note: ${detail}`;
    }
    return detail || (zh ? '写笔记没能开始' : 'Writing the note did not start');
};
