import SvgIcon from './SvgIcon.jsx';

// Shown only when this task's cut file cannot be read. The cut itself is not
// announced: the player, the subtitles and the note simply are the cut version,
// and the cut video is in the transcript's export menu (owner, 2026-10-22).
//
// The page must not play the original in its place, since the transcript and
// note describe the shorter file. So the bar says it in one sentence, with the
// way out beside it: processing the recording again. Why it has to be so is told
// in conversation, not printed here (owner, 2026-10-22: hints say the state and
// the next action only).

const CutFlowBar = ({
    summary,
    lang = 'zh',
    unavailable = false,
    onReprocess,
}) => {
    if (!summary || !unavailable) return null;
    const zh = lang === 'zh';
    return (
        <div
            data-testid="cut-flow-bar"
            className="mt-2 flex max-w-3xl flex-wrap items-center gap-x-3 gap-y-1.5 rounded-[12px] border border-[#fda29b] bg-[#fef3f2] px-3 py-2 text-xs font-semibold text-[#b42318] dark:border-[#f04438]/40 dark:bg-[#f04438]/[0.14] dark:text-[#f97066]"
        >
            <span className="inline-flex items-center gap-1.5">
                <SvgIcon name="warning" className="shrink-0 text-[15px]"/>
                {zh ? '剪后文件读不到了' : 'The cut file cannot be read'}
            </span>
            {typeof onReprocess === 'function' && (
                <button
                    type="button"
                    onClick={onReprocess}
                    className="ml-auto inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-[11px] border border-[#fda29b] bg-white px-2.5 text-xs font-bold text-[#b42318] transition hover:bg-[#fff6f5] dark:border-[#f04438]/40 dark:bg-white/[0.08] dark:text-[#f97066] dark:hover:bg-white/[0.12]"
                >
                    <SvgIcon name="refresh" className="text-[14px]"/>
                    {zh ? '重新处理' : 'Process again'}
                </button>
            )}
        </div>
    );
};

export default CutFlowBar;
