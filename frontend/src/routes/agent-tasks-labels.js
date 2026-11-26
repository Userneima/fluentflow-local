// The words on a record card: its time, the stage it is at, why it is waiting,
// the cut and note blocks, and where the cut file went. Plain functions of a
// task row, so the card, the tests and the list read the same answer.
import {fmtCharCount, fmtDownloadProgress, fmtDuration, progressMessageOf, videoSourceProgressOf} from '../lib/format.js';
import {noteProgressStatus} from '../lib/noteProgress.js';
import {fmtFileSize} from '../app/shared.jsx';
import {
    normalizeTaskState,
    stageName,
    TASK_STATE_QUEUED,
    taskStatusLabel,
    noteCharsOf,
    noteProgressT,
    noteWritingLabel,
    noteWritingSentence,
} from '../lib/taskState.js';

export const formatTaskDateTime = (value, lang) => {
    const date = new Date(value || '');
    if (Number.isNaN(date.getTime())) return lang === 'zh' ? '时间未知' : 'Unknown time';
    try {
        return new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en-US', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        }).format(date).replace(/\//g, '-').replace(',', '');
    } catch (_) {
        return lang === 'zh' ? '时间未知' : 'Unknown time';
    }
};

const statusLabel = taskStatusLabel;

// Whether this task's material has no picture. Copy that says "提取音频" or "录像"
// about an uploaded .m4a is not a translation slip — it tells the user the product
// misunderstood what they gave it.
export const isAudioOnlySource = (job) => {
    const type = String(job?.source_type || job?.result?.source || '').toLowerCase();
    if (type.startsWith('video') || type === 'queue_upload') return false;
    if (type === 'audio' || type === 'audio_file') return true;
    const name = String(job?.source_filename || job?.result?.filename || '');
    if (/\.(mp4|mov|avi|mkv|webm|m4v|flv|wmv|mpe?g)$/i.test(name)) return false;
    return /\.(mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i.test(name);
};

// The steps the service says are still to do or running (task_activity).
const openSteps = (job) => (job?.activity?.busy && Array.isArray(job.activity.open_steps) ? job.activity.open_steps : []);

// FluentFlow's own writer on the note says how far it has got
// (backend note_progress); the stage the row was last written at does not.
const noteProgressOf = (job) => job?.activity?.note_progress || null;

export const stageLabel = (job, lang) => {
    const writing = noteWritingLabel(job, lang);
    if (writing) return writing;
    return stageName(job?.stage, lang, {audioOnly: isAudioOnlySource(job)}) || statusLabel(job, lang);
};

// Which task this one is behind, and how long it has been behind it.
//
// Recordings are processed one at a time, so most of "queued" is an ordinary
// wait — but the card said the same words whether the queue was moving or not,
// and a row of them on an idle machine was indistinguishable from a row of them
// on a working one. Naming the task ahead and the time waited is what separates
// them. It has to be the time, not a threshold: a lecture legitimately holds the
// queue for hours, so nothing here can decide on the reader's behalf that a wait
// has gone on too long.
const queueWaitDetail = (job, lang, aheadName = '') => {
    const wait = job?.metadata?.queue_wait;
    const waitingFor = String(wait?.waiting_for || '').trim();
    if (!waitingFor) return '';
    const since = Date.parse(wait?.since || '');
    const waited = Number.isFinite(since) ? fmtDuration((Date.now() - since) / 1000, lang) : '';
    // The recording's own name, because that is what the reader recognises on
    // this page. A task id identifies the right row to a developer and nothing
    // at all to the person who queued five lectures; it is the fallback only
    // when the task ahead is not one of the rows on screen.
    const ahead = aheadName || waitingFor;
    if (lang === 'zh') {
        return `在等「${ahead}」处理完${waited ? `，已经等了 ${waited}` : ''}。一次只处理一个；前面那个要先剪掉气口再转录，素材长的话这一步就要几分钟。`;
    }
    return `Waiting for "${ahead}"${waited ? `, ${waited} so far` : ''}. One at a time; that task removes its breath gaps before transcribing, which takes minutes on long material.`;
};

// The steps that wait on a model after the transcription, said with how long
// they have waited: the word-list correction was measured waiting 319 seconds
// with the card at 60% and nothing else on it (2026-11-21). The correction
// against the on-screen subtitles first decodes the whole video to read them
// (4 minutes for a 3.5-hour interview, decision 4.13) and asks the model only
// after: until its first request is out it says so (`readingZh`), not a wait.
const MODEL_WAIT_LINES = {
    subtitle_correction: {
        zh: '正在按画面字幕校正转录稿', en: 'Checking the transcript against the on-screen text', doneZh: '已校正', doneEn: 'checked',
        readingZh: '正在读画面字幕', readingEn: 'Reading the on-screen subtitles',
    },
    glossary_correction: {zh: '正在按词表校正转录稿', en: 'Correcting the transcript from the word list', doneZh: '已校正', doneEn: 'corrected'},
    translation: {zh: '正在把转录稿翻译成中文字幕', en: 'Translating the transcript into Chinese subtitles', doneZh: '已翻译', doneEn: 'translated'},
};
// One read of a model answer may take 300 seconds (backend ai_client
// REQUEST_TIMEOUT); a minute before that runs out the wait is said to be long.
const SLOW_MODEL_SECONDS = 240;
const parsedTime = (value) => {
    const time = Date.parse(value || '');
    return Number.isFinite(time) ? time : null;
};
// The model requests of this stage (backend model_usage.MODEL_BATCHES), when
// this run of the stage wrote them: how many batches have answered, the last
// answer, and when the oldest request still out was sent. These steps send
// many requests, four at a time, so the time since the stage began says
// nothing about any one of them: a lecture's correction passed four minutes
// with every answer on time (review, 2026-11-26). Nor the time since the last
// answer: one batch stuck while the others kept answering was hidden that way
// (second review, 2026-11-26). The wait that can be slow is the oldest one out.
const modelBatchesOf = (job, since) => {
    const batches = job?.metadata?.model_batches;
    if (!batches || batches.stage !== job?.stage) return null;
    const total = Number(batches.total);
    const done = Number(batches.done);
    if (!Number.isFinite(total) || total < 1 || !Number.isFinite(done)) return null;
    // Left by an earlier run of the same stage (carried on after a stop).
    if (!(Date.parse(batches.updated_at || '') >= since)) return null;
    const lastAnswer = done > 0 ? parsedTime(batches.last_answer_at) : null;
    // Written before the oldest-request time was recorded: the last answer
    // (or the first request) stands in for it.
    const waitingSince = parsedTime(batches.waiting_since) ?? lastAnswer ?? parsedTime(batches.updated_at);
    return {done, total, lastAnswer, waitingSince};
};
const modelWaitDetail = (job, lang) => {
    const words = MODEL_WAIT_LINES[job?.stage];
    if (!words) return '';
    const zh = lang === 'zh';
    const since = Date.parse(job?.metadata?.stage_started_at || '');
    if (!Number.isFinite(since)) return zh ? words.zh : words.en;
    const batches = modelBatchesOf(job, since);
    const ago = (moment) => fmtDuration(Math.max(0, (Date.now() - moment) / 1000), lang);
    if (!batches && words.readingZh) {
        return zh ? `${words.readingZh}（已用 ${ago(since)}）` : `${words.readingEn} (${ago(since)} so far)`;
    }
    const waitingSince = batches?.waitingSince ?? since;
    let line;
    if (!batches || batches.total < 2) {
        line = zh ? `${words.zh}（已等 ${ago(waitingSince)}）` : `${words.en} (${ago(waitingSince)} so far)`;
    } else if (batches.lastAnswer !== null) {
        // 「批」, not 「段」: a batch is many transcript segments (100 in the
        // subtitle correction), and 「段」 read as the segments themselves.
        line = zh
            ? `${words.zh}（${words.doneZh} ${batches.done}/${batches.total} 批，上一批 ${ago(batches.lastAnswer)}前返回）`
            : `${words.en} (${batches.done}/${batches.total} batches ${words.doneEn}, last one ${ago(batches.lastAnswer)} ago)`;
    } else {
        line = zh
            ? `${words.zh}（${words.doneZh} 0/${batches.total} 批，已等 ${ago(waitingSince)}）`
            : `${words.en} (0/${batches.total} batches ${words.doneEn}, ${ago(waitingSince)} so far)`;
    }
    if ((Date.now() - waitingSince) / 1000 < SLOW_MODEL_SECONDS) return line;
    // One batch sent before the last answer is still out: said with its own
    // wait, which the line above does not show.
    if (batches?.lastAnswer != null && waitingSince < batches.lastAnswer) {
        return zh
            ? `${line}。有一批已等 ${ago(waitingSince)}还没返回，模型响应较慢，仍在等待`
            : `${line}. One batch has waited ${ago(waitingSince)}; the model is slow to answer, still waiting`;
    }
    return zh ? `${line}。模型响应较慢，仍在等待` : `${line}. The model is slow to answer; still waiting`;
};

// Said ahead of the line while a task the service carried on after a restart
// is at work again (backend task_actions.resume_after_restart).
const restartResumedNote = (job, lang) => {
    if (!job?.metadata?.restart_resumed) return '';
    return lang === 'zh' ? '服务重启后已从断点恢复。' : 'Carried on from where it stopped after the service restarted. ';
};

export const liveStageDetail = (job, lang, aheadName = '') => {
    // Ahead of the snapshot's own line: the snapshot describes the step this task
    // will run, which for a task that has not started is the least useful true
    // thing on the card.
    const waiting = queueWaitDetail(job, lang, aheadName);
    if (waiting) return waiting;
    const line = currentStageDetail(job, lang);
    return `${restartResumedNote(job, lang)}${line}`;
};

const currentStageDetail = (job, lang) => {
    const writing = noteWritingSentence(job, lang);
    if (writing) return writing;
    if (noteProgressOf(job)) return noteProgressStatus(noteProgressOf(job), noteProgressT(lang), lang);
    const modelWait = modelWaitDetail(job, lang);
    if (modelWait) return modelWait;
    // A link task's own line while it downloads ("正在下载视频"), only while
    // the stage that wrote it lasts (progressMessageOf).
    const progressMessage = progressMessageOf(job);
    if (progressMessage) {
        const download = fmtDownloadProgress(videoSourceProgressOf(job), lang);
        return download ? `${progressMessage} · ${download}` : progressMessage;
    }
    const snapshotStep = Array.isArray(job?.task_snapshot?.steps)
        ? job.task_snapshot.steps.find((step) => step?.id === job.task_snapshot?.current_step)
        : null;
    if (snapshotStep?.detail) return snapshotStep.detail;
    if (normalizeTaskState(job) === TASK_STATE_QUEUED) {
        // Say why the wait can be long. One task runs at a time, and the task
        // ahead now starts by re-encoding its whole recording to remove the
        // breath gaps — minutes on a long video. "Queued" alone made a working
        // queue look stalled.
        return lang === 'zh'
            ? '已经加入队列，会按顺序开始处理。前面那个任务要先剪掉气口再转录，素材长的话这一步要几分钟。'
            : 'Queued and waiting for its turn. The task ahead removes its breath gaps before transcribing, which takes minutes on long material.';
    }
    if (job?.stage === 'prepare_media') {
        const material = isAudioOnlySource(job)
            ? (lang === 'zh' ? '录音' : 'recording')
            : (lang === 'zh' ? '录像' : 'video');
        return lang === 'zh'
            ? `正在把${material}里的空白剪掉，之后的转录和笔记都基于剪好的那份。素材长的话要几分钟。`
            : `Removing the quiet stretches from the ${material}; the transcript and note will both come from the shortened file. Minutes on long material.`;
    }
    return lang === 'zh' ? '进度会在这里持续更新，离开本页也不会中断任务。' : 'Progress keeps updating here; leaving this page will not stop the task.';
};

const sourceLabel = (job, lang) => {
    const isZh = lang === 'zh';
    const sourceType = job?.source_type || job?.result?.source || '';
    const metadata = job?.metadata || {};
    const videoSource = metadata.video_source || {};
    const url = String(videoSource.url || videoSource.webpage_url || metadata.video_source_input_preview || '').trim();
    let host = '';
    try {
        host = new URL(url).hostname.replace(/^www\./, '').toLowerCase();
    } catch (_) {}
    if (sourceType === 'video_link') {
        if (host.includes('bilibili.com') || host === 'b23.tv') return 'Bilibili';
        if (host.includes('youtube.com') || host === 'youtu.be') return 'YouTube';
        if (host.includes('douyin.com')) return isZh ? '抖音' : 'Douyin';
        return isZh ? '视频平台链接' : 'Video platform link';
    }
    if (sourceType === 'transcript_file') return isZh ? '本机字幕文件' : 'Local subtitle file';
    if (sourceType === 'queue_upload' || sourceType === 'video_file') return isZh ? '本机视频文件' : 'Local video file';
    if (sourceType === 'audio_file') return isZh ? '本机音频文件' : 'Local audio file';
    const filename = String(job?.source_filename || job?.result?.filename || '').toLowerCase();
    if (/\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(filename)) return isZh ? '本机视频文件' : 'Local video file';
    if (/\.(mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(filename)) return isZh ? '本机音频文件' : 'Local audio file';
    if (/\.(srt|vtt|txt|md)$/i.test(filename)) return isZh ? '本机字幕文件' : 'Local subtitle file';
    return isZh ? '本机文件' : 'Local file';
};

// What the cut actually did to this recording, or why it did not.
//
// This replaced the "处理路线" tile, which said "本机转录" on every card in an
// edition that only transcribes locally — a column of identical words. The cut is
// the opposite: it is different per recording, it is the reason the result is
// shorter than the upload, and it is the number worth seeing at a glance.
// Where the cut file ended up, but only when that is not the ordinary outcome.
//
// Three states, and two of them are worth a sentence:
//
//   null  — an upload. There is no "beside the original" to deliver to, because
//           the original FluentFlow holds *is* the copy. Worth saying once,
//           because the owner's folder does not get the file and they will go
//           looking for it there.
//   true  — the in-place entry did what it always does. Silent: a line repeating
//           it on every card is a column of the same words, and the name is the
//           recording's own with a suffix. Nothing there to learn.
//   false — it tried and could not. Loud, and with the reason, because a file the
//           owner expected in their folder is not in it.
//
// What the earlier version got wrong was not "no line per card" — it was that
// nothing anywhere said this happens at all, so the owner never knew to expect a
// file. That belongs where they choose the entry, not on a hundred records.
export const cutFileDelivery = (job, lang) => {
    const isZh = lang === 'zh';
    const state = job?.result?.debreath;
    if (!state || typeof state !== 'object') return null;
    // Nothing was rendered, so there was nothing to put anywhere.
    if (state.used_for_transcription === false || state.already_cut || state.not_worth_rendering) return null;
    if (!state.rendered && state.status !== 'completed') return null;
    if (state.delivered === false) {
        return {
            tone: 'error',
            text: state.delivery_error || (isZh
                ? '剪后文件没能存到原文件旁边。任务里这份还在，可以下载。'
                : 'The cut file could not be saved next to the original. The task still has it, and it can be downloaded.'),
        };
    }
    // Positive evidence, not a missing field. An in-place task always records the
    // outcome either way, so absence could equally mean "recorded before this was
    // built" — and telling the owner an old record was an upload when it was not
    // sends them looking in the wrong place. A task with no folder behind it is
    // the one that really has nowhere to deliver to.
    if (state.delivered === undefined || state.delivered === null) {
        if (job?.metadata?.folder_intake) return null;
        // A link's cut is filed in its folder under Videos, recorded on the
        // task's output folder rather than on the cut.
        if (job?.result?.output_folder?.cut_path) return null;
        return {
            tone: 'note',
            text: isZh
                ? '这份是上传处理的，剪后文件留在应用里，没有进你的文件夹——可以从这里下载。'
                : 'This one was uploaded, so the cut file stayed in the app rather than going into your folder — download it here.',
        };
    }
    return null;
};

export const debreathTile = (job, lang) => {
    const isZh = lang === 'zh';
    const state = job?.result?.debreath;
    if (job?.result?.agent_cut?.status === 'waiting') {
        return {value: isZh ? '等你的 agent 判断' : 'Waiting for your agent'};
    }
    // Absence means never cut. It has to stay distinguishable from "cut and found
    // nothing", which is why the list payload carries this block at all: without it
    // a freshly cut task reported "未剪" on this very card.
    if (!state || typeof state !== 'object') {
        // The transcript is stored (completed) and the cut step is open: it is
        // being judged and has written nothing yet. It read "older task" here.
        if (openSteps(job).includes('cut')) return {value: isZh ? '正在剪…' : 'Cutting…'};
        // A task still running has not reached the cut yet: the recording is
        // transcribed whole first, so a fresh task read "older task" until then.
        if (!['completed', 'failed', 'cancelled'].includes(job?.status)) {
            return {value: isZh ? '转录完再剪' : 'After transcription'};
        }
        return {value: isZh ? '未剪（旧任务）' : 'Not cut (older task)'};
    }
    const plan = state.plan || {};
    const cuts = Number(plan.cut_count) || 0;
    const removed = Number(plan.removed_seconds) || 0;
    // Only the time removed. The engine's cut count counts every breath gap
    // one by one (1887 on a long interview), while the processing report
    // groups them into a few dozen entries, so the two numbers never agreed.
    if (state.status === 'running') return {value: isZh ? '正在剪…' : 'Cutting…'};
    if (state.status === 'failed') return {value: isZh ? '没成功' : 'Failed'};
    if (state.not_worth_rendering) {
        // Measured, not declined: there was almost nothing to remove, so the file was
        // left alone rather than re-exported whole for a fraction of a second.
        return {value: isZh ? '几乎没空白可剪' : 'Almost nothing to cut'};
    }
    if (state.already_cut) {
        // Not the same answer as declining. Declining is bad news — the note is about
        // the recording. This is not: the file arrived already shortened, so the pass
        // was skipped to save a pointless re-encode.
        return {value: isZh ? '本来就是剪后版本' : 'Already cut'};
    }
    if (state.used_for_transcription === false && state.not_used_reason) {
        // The cut declined and the recording was transcribed. Saying "cut" here
        // would be the one wrong answer, because the note is not about a cut file.
        // Why it declined is a sentence, and a sentence truncated into a tile is
        // worth less than nothing — it lives on the task's own page.
        return {value: isZh ? '按原片处理' : 'Used the original'};
    }
    if (!cuts) return {value: isZh ? '没有可剪的空白' : 'Nothing to cut'};
    const removedText = fmtDuration(removed, lang);
    const value = isZh ? `剪掉 ${removedText}` : `${removedText} removed`;
    // No line for the file landing beside the original: that is the normal outcome,
    // and a card that narrates the normal outcome is noise. The hint below is kept
    // for the outcomes that are *not* obvious from the numbers.
    if (state.render_verified === false) {
        return {value, hint: isZh ? '没通过自检' : 'Failed its own check'};
    }
    return {value};
};

// The note, in one phrase. It is what the task exists to produce, so its state
// belongs on the card — the tile it replaced ("判断材料类型") reported an internal
// classification that changes nothing the user does next.
export const noteTileValue = (job, lang) => {
    const isZh = lang === 'zh';
    const result = job?.result || {};
    // The job list carries only the first 240 characters of the note, so its
    // length is sent separately; the full result has no such field and is counted.
    const chars = noteCharsOf(job);
    if (chars) {
        const fromCut = result.summary_written_from === 'debreath_media_note';
        const base = fmtCharCount(chars, lang);
        return fromCut
            ? (isZh ? `${base}（据剪后版本）` : `${base} (from the cut version)`)
            : base;
    }
    if (result.summary_status === 'failed' || result.summary_error) return isZh ? '没生成' : 'Not written';
    // The note step is still to come (it follows the cut): the pipeline's
    // "skipped" on the result only means it left the note to that step.
    if (openSteps(job).includes('note')) {
        return job?.activity?.current_step === 'note' ? (isZh ? '正在写…' : 'Being written…') : (isZh ? '剪完再写' : 'After the cut');
    }
    if (result.summary_skipped_reason === 'no_model_key') return isZh ? '没填 Key，只出转录稿' : 'No key: transcript only';
    if (result.summary_skipped) return isZh ? '已跳过' : 'Skipped';
    if (result.summary_status === 'pending') return isZh ? '正在写…' : 'Being written…';
    return isZh ? '等处理' : 'Pending';
};

// Length and size, and for a link the platform it came from: where a downloaded
// video came from is not visible anywhere else on the row.
export const sizeAndLengthLabel = (job, lang) => {
    const sizeMb = Number(job?.source_file_size_mb || job?.metadata?.file_size_mb || 0) || 0;
    const durationSec = Number(job?.result?.audio_duration_seconds || job?.source_duration_seconds || job?.metadata?.duration_seconds || 0) || 0;
    const parts = [];
    if (durationSec) parts.push(fmtDuration(durationSec, lang));
    if (sizeMb) parts.push(fmtFileSize(sizeMb));
    if ((job?.source_type || job?.result?.source || '') === 'video_link') parts.push(sourceLabel(job, lang));
    return parts.join(' · ');
};
