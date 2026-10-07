import {useState} from 'react';
import SvgIcon from './SvgIcon.jsx';
import {fmtDurationCompact} from '../lib/format.js';

// One line saying what already happened to this recording.
//
// It replaces two panels rather than adding to them. An upload removes its own
// breath gaps and writes its own note, so the page has nothing to ask for: the
// "remove the silence" and "rewrite the note from the cut version" entries are
// steps that already ran, and an interface that still offers them is telling the
// user their finished work is unfinished.
//
// So this reports and hands over, and that is the whole contract:
//   - it happened, this much came out;
//   - the player, the subtitles and the note are all this one shortened file;
//   - here is the file.
// No directory, no filename, no next step — the owner could not tell where the cut
// file had gone precisely because the old copy described a filesystem instead of
// giving them the file.
//
// When the cut file cannot be read the bar says so instead of going quiet. That
// case has to be loud: the alternative is a page that plays the original while its
// transcript and note describe a shorter file, and nothing on screen would explain
// why the timings no longer line up.

const CutFlowBar = ({
    summary,
    lang = 'zh',
    unavailable = false,
    onDownload,
}) => {
    const [downloading, setDownloading] = useState(false);
    const zh = lang === 'zh';
    if (!summary) return null;

    const download = async () => {
        if (downloading || typeof onDownload !== 'function') return;
        setDownloading(true);
        try {
            await onDownload();
        } finally {
            setDownloading(false);
        }
    };

    if (unavailable) {
        return (
            <div
                data-testid="cut-flow-bar"
                className="mt-2 flex max-w-3xl items-start gap-2 rounded-[12px] border border-[#fda29b] bg-[#fef3f2] px-3 py-2 text-xs font-semibold leading-relaxed text-[#b42318] dark:border-[#f04438]/40 dark:bg-[#f04438]/[0.14] dark:text-[#f97066]"
            >
                <SvgIcon name="warning" className="mt-0.5 shrink-0 text-[15px]"/>
                <p>
                    {zh
                        ? '这个任务的剪后文件读不到了，所以下面没有可播放的画面。转录稿和笔记讲的都是那份剪后文件，不能拿原文件顶上——时间点对不上。重新处理一次可以恢复。'
                        : 'The cut file for this task cannot be read, so there is nothing to play. The transcript and note describe that file and the original cannot stand in for it — the timings would not line up. Processing it again restores it.'}
                </p>
            </div>
        );
    }

    const removed = fmtDurationCompact(summary.removedSeconds);
    const kept = fmtDurationCompact(summary.keptSeconds);

    return (
        <div
            data-testid="cut-flow-bar"
            className="mt-2 flex max-w-3xl flex-wrap items-center gap-x-3 gap-y-1.5 rounded-[12px] border border-[#d6dcff] bg-[#eef2ff] px-3 py-2 dark:border-white/[0.12] dark:bg-white/[0.08]"
        >
            <span className="inline-flex items-center gap-1.5 text-xs font-extrabold text-[#46536f] dark:text-white/80">
                <SvgIcon name="content_cut" className="text-[15px] text-primary"/>
                {zh ? '已自动去掉气口' : 'Breath gaps removed automatically'}
            </span>
            <span className="text-xs font-semibold tabular-nums text-[#46536f] dark:text-white/72">
                {zh
                    ? `剪掉 ${summary.cutCount} 处，删掉 ${removed}，剩 ${kept}`
                    : `${summary.cutCount} cuts, ${removed} removed, ${kept} left`}
            </span>
            {/* The sentence that ties the three surfaces together. Without it the
                player being shorter than the uploaded file is unexplainable. */}
            <span className="text-xs font-medium text-[#5b6785] dark:text-white/60">
                {zh
                    ? '播放、字幕、笔记都是这一份剪后版本；原文件没有被改动。'
                    : 'The player, subtitles and note are all this cut version; your original is untouched.'}
            </span>
            {!summary.renderVerified && (
                <span className="text-xs font-semibold text-[#b54708] dark:text-[#fdb022]">
                    {zh ? '（它没通过自检，先听一遍）' : '(it failed its own check — listen first)'}
                </span>
            )}
            {summary.mediaArtifact && (
                <button
                    type="button"
                    disabled={downloading}
                    onClick={download}
                    className="ml-auto inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-[11px] border border-[#c7d0ff] bg-white px-2.5 text-xs font-bold text-[#46536f] transition hover:bg-[#f5f7ff] disabled:opacity-40 dark:border-white/[0.14] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]"
                >
                    <SvgIcon name={downloading ? 'sync' : 'download'} className={`text-[14px] ${downloading ? 'animate-spin' : ''}`}/>
                    {zh ? '下载剪后文件' : 'Download the cut file'}
                </button>
            )}
        </div>
    );
};

export default CutFlowBar;
