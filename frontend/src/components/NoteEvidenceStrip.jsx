import {useState} from 'react';
import SvgIcon from './SvgIcon.jsx';

// What the note was actually written from.
//
// The owner's question: the de-breath is mechanical and its counts are enough, but
// Claude reading frames and writing a note is a black box — should the page open it?
// The answer taken here is that there are two different black boxes and only one of
// them is worth opening:
//
//   - *how it reasoned* — a chain of thought nobody can verify, which reads
//     persuasively and therefore makes an unchecked note feel checked. Not shown.
//   - *what it read* — which file, which frames went, which ones the note cites.
//     Verifiable in seconds by looking, and that is the point.
//
// The most useful thing here is the part that is otherwise invisible: the frames it
// was given and did **not** use. If the one slide that mattered is sitting in those,
// nothing else on the page would ever tell you. Cited frames are already visible
// inline in the note; the uncited ones only exist here.
//
// Deliberately one line until asked. The owner had just removed a bar that narrated
// the normal outcome, and this must not become another one.

const BASIS_LABELS = {
    transcript_and_frames: {zh: '画面 + 字幕', en: 'frames and subtitles'},
    transcript_only: {zh: '只用了字幕', en: 'subtitles only'},
};

// Which file the note actually read. Not decoration: the cut is skipped or declined
// in four ordinary cases (unsafe separation, nothing worth rendering, a file that
// arrived already cut, an unsupported container), and in every one of them the note
// is written from the recording. Calling those frames "剪后画面" would be the
// product claiming to have watched a file it never opened.
const readTheCutFile = (result) => result?.transcript_media === 'debreath_media';
const mediaWord = (result, zh) => {
    if (!zh) return readTheCutFile(result) ? 'cut ' : '';
    return readTheCutFile(result) ? '剪后' : '原片';
};

const clock = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
};

const NoteEvidenceStrip = ({result, lang = 'zh', onSeek}) => {
    const [open, setOpen] = useState(false);
    const zh = lang === 'zh';
    const state = result?.visual_note && typeof result.visual_note === 'object' ? result.visual_note : null;

    // Only for a note this flow wrote. A note typed by hand, or one from the older
    // transcript pipeline, has no frames to account for and gets no line.
    if (!state || result?.summary_written_from !== 'debreath_media_note') return null;

    const sent = Array.isArray(state.frames_sent) ? state.frames_sent : [];
    const cited = Array.isArray(state.frames_cited) ? state.frames_cited : [];
    const citedNames = new Set(cited.map((frame) => frame?.filename));
    const basis = BASIS_LABELS[state.basis];
    const timeline = state.subtitle_timeline && typeof state.subtitle_timeline === 'object'
        ? state.subtitle_timeline
        : null;
    const dropped = Number(timeline?.dropped_segments) || 0;
    // The talk that did not fit in the request. This is the one line here that
    // reports a note being wrong rather than a note being explained: a recording
    // past the limit produces a note that reads like a finished one and covers
    // the first part of the lecture, and nothing else on the page differs.
    const unreadChars = Number(state.transcript_chars_dropped) || 0;
    const coveredUntil = String(state.transcript_covered_until || '').trim();

    return (
        <div className="border-t border-[#e4e0e0] px-4 py-2.5 dark:border-white/[0.12]">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <span className="text-[11px] font-bold text-[#999] dark:text-white/40">
                    {zh ? '依据' : 'Based on'}
                </span>
                <span className="text-[11px] font-semibold text-[#666] dark:text-white/60">
                    {sent.length > 0
                        ? (zh
                            ? `${mediaWord(result, true)}画面 ${cited.length}/${sent.length} 张 + 字幕`
                            : `${cited.length} of ${sent.length} ${mediaWord(result, false)}frames, plus subtitles`)
                        : (basis ? basis[zh ? 'zh' : 'en'] : (zh ? '字幕' : 'subtitles'))}
                </span>
                {/* Measured from the note's own citations, never from what the model
                    says it did — so this sentence can contradict a confident note. */}
                {state.basis === 'transcript_only' && sent.length > 0 && (
                    <span className="text-[11px] font-semibold text-[#b54708] dark:text-[#fdb022]">
                        {zh ? '正文没有引用任何画面' : 'the note cites no frame'}
                    </span>
                )}
                {unreadChars > 0 && (
                    <span className="text-[11px] font-bold text-[#b42318] dark:text-[#f97066]">
                        {zh
                            ? `这份笔记${coveredUntil ? `只讲到 ${coveredUntil}` : '没有覆盖整段录音'}——后面的讲话一次请求装不下。把录像切成几段分别处理才完整。`
                            : `This note ${coveredUntil ? `stops at ${coveredUntil}` : 'does not cover the whole recording'} — the rest of the talk did not fit in one request. Split the recording to cover all of it.`}
                    </span>
                )}
                {dropped > 0 && (
                    <span className="text-[11px] font-medium text-[#999] dark:text-white/40">
                        {zh ? `${dropped} 句字幕的声音被剪掉了` : `${dropped} subtitle lines lost their audio`}
                    </span>
                )}
                {sent.length > 0 && (
                    <button
                        type="button"
                        onClick={() => setOpen((value) => !value)}
                        className="ml-auto inline-flex items-center gap-1 text-[11px] font-bold text-[#666] transition hover:text-[#111111] dark:text-white/60 dark:hover:text-white"
                    >
                        {open
                            ? (zh ? '收起画面' : 'Hide frames')
                            : (zh ? '看它读了哪些画面' : 'See the frames it read')}
                        <SvgIcon name={open ? 'expand_less' : 'expand_more'} className="text-[14px]"/>
                    </button>
                )}
            </div>

            {open && sent.length > 0 && (
                <div className="mt-2.5 space-y-2">
                    <p className="text-[11px] font-medium leading-relaxed text-[#999] dark:text-white/40">
                        {zh
                            ? `这些是从${mediaWord(result, true)}视频里按画面变化抽出的截图，全部发给了 Claude。亮的是正文引用了的；暗的它看过但没用——如果有一张其实很关键，就是在这里发现的。点任意一张跳到播放器对应的时间。`
                            : `These stills were sampled from the ${mediaWord(result, false)}video where the picture changed, and all of them were sent. The bright ones are cited in the note; the dim ones it saw and did not use — that is where a missed slide shows up. Click one to jump to that moment in the player.`}
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {sent.map((frame) => {
                            const used = citedNames.has(frame?.filename);
                            return (
                                <button
                                    key={frame?.filename || frame?.url}
                                    type="button"
                                    onClick={() => onSeek?.(Number(frame?.timestamp_seconds) || 0)}
                                    title={`${clock(frame?.timestamp_seconds)}　${used ? (zh ? '正文引用了' : 'cited') : (zh ? '没有引用' : 'not cited')}`}
                                    className={`group relative overflow-hidden rounded-[10px] border transition ${
                                        used
                                            ? 'border-primary/60 opacity-100'
                                            : 'border-[#e4e0e0] opacity-45 hover:opacity-90 dark:border-white/[0.12]'
                                    }`}
                                >
                                    <img
                                        src={frame?.url}
                                        alt={clock(frame?.timestamp_seconds)}
                                        loading="lazy"
                                        className="h-14 w-24 object-cover"
                                    />
                                    <span className="absolute bottom-0 right-0 bg-black/60 px-1 text-[10px] font-bold tabular-nums text-white">
                                        {clock(frame?.timestamp_seconds)}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                    {/* The model's own account of what it saw. Last, small, and
                        labelled as its own words: the product refuses to let this set
                        `basis`, so it must not read as the evidence either. */}
                    {state.basis_note && (
                        <p className="text-[11px] font-medium leading-relaxed text-[#aaa] dark:text-white/35">
                            {zh ? 'Claude 自述（不作为判据）：' : 'Claude says (not the evidence): '}{state.basis_note}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
};

export default NoteEvidenceStrip;
