// The editor's transcript pane, moved out of editor.jsx: its title and save
// state, the follow switch, speed, review mode and view switches, the player
// (shown in video review), the sentence list and the play bar under it. The
// state it shows is held by the page: editor-transcript-state.js (the
// working copy), editor-playback.js (playback and following) and
// editor-media.js (which file plays).
import SvgIcon from '../components/SvgIcon.jsx';
import PlaybackSpeedMenu from '../components/PlaybackSpeedMenu.jsx';
import {FeatureHint} from '../components/FeatureHint.jsx';
import {fmtTime} from '../app/shared.jsx';
import {usePlaybackTime} from '../lib/playbackClock.js';
import {button, iconButton, segmentGroup, segmentItem, chip, surfaceCard} from '../lib/ui.js';
import {BilingualLine, TranscriptLine, TranscriptParagraph} from './editor-transcript-rows.jsx';

// The clock and the progress bar: the only parts of the page that follow the
// player every tick.
const PlaybackBar = ({clock, duration, onSeek}) => {
    const time = usePlaybackTime(clock);
    const progress = duration > 0 ? Math.min(100, Math.max(0, time / duration * 100)) : 0;
    return (
        <>
            <span className="text-xs font-mono text-on-surface-variant ml-auto">{fmtTime(time)} / {fmtTime(duration || time)}</span>
            <input
                type="range"
                min="0"
                max={Math.max(1, duration)}
                step="0.1"
                value={Math.min(time, Math.max(1, duration))}
                onChange={(e) => onSeek(Number(e.target.value) || 0)}
                className="order-last w-full basis-full accent-primary"
                style={{background: `linear-gradient(90deg, #3B82F6 ${progress}%, var(--c-surface-container-highest) ${progress}%)`}}
            />
        </>
    );
};

const TranscriptPane = ({
    lang,
    t,
    paneStyles,
    transcriptDraft,
    playback,
    media,
    canShowVideoReview,
    canUseVideoReview,
    playingCutVersion,
    durSec,
    mediaInputRef,
    setEditRecordsOpen,
}) => {
    const {
        segments,
        transcript,
        transcriptDirty,
        transcriptSaveStatus,
        transcriptComposingRef,
        handlePlainTranscriptChange,
        visibleEditRecords,
        transcriptParagraphs,
        bilingualTranscriptSegments,
        hasBilingualTranscript,
        visibleTranscriptView,
        setTranscriptView,
        isTranscriptHydrationPending,
        isTranscriptHydrationFailed,
    } = transcriptDraft;
    const {
        mediaRef,
        playbackClock,
        playbackDuration,
        mediaPlaying,
        setMediaPlaying,
        followPlayback,
        setFollowPlayback,
        playbackRate,
        setPlaybackRate,
        activeReviewMode,
        shouldShowVideoReview,
        activeSegmentIndex,
        transcriptScrollRef,
        updateMediaCurrentTime,
        restoreMediaPosition,
        seekMediaTo,
        pauseFollowing,
        resumeFollowing,
        switchReviewMode,
        seekRow,
        editRow,
        stopFollowing,
        focusSentence,
        registerRow,
        togglePlayback,
        leaveSentence,
        sentenceKeys,
    } = playback;
    const {mediaUrl, mediaLoading, mediaError, cutFileMissing, playbackProblem} = media;
    const followSwitch = (
        <button type="button" data-testid="transcript-follow-switch" onClick={followPlayback ? pauseFollowing : resumeFollowing} aria-pressed={followPlayback} className={chip(followPlayback)}>
            {/* The note follows too, so each switch names what it moves; once
                stopped it says where clicking takes the reader. */}
            {followPlayback ? t('edit.followPlayback') : t('edit.backToPlayback')}
        </button>
    );
    return (
        <section style={paneStyles.left} className={`flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden ${surfaceCard}`}>
            <div className="border-b border-[#e4e0e0] px-4 py-3 dark:border-white/[0.12]">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                    <div className="min-w-0 shrink-0">
                        <div className="flex items-center gap-2">
                            <SvgIcon name="subject" className="text-[18px] text-[#111111] dark:text-white"/>
                            <FeatureHint id="transcript-edit" when={segments.length > 0 && visibleTranscriptView !== 'bilingual'} lang={lang}>
                                <h2 className="truncate font-headline text-[15px] font-extrabold text-[#111111] dark:text-white">
                                    {lang === 'zh' ? '转录原文' : 'Transcript'}
                                </h2>
                            </FeatureHint>
                            {(transcriptDirty || transcriptSaveStatus !== 'idle') && (
                                <span className={`inline-flex h-5 items-center gap-1 rounded-[8px] px-1.5 text-[11px] font-bold leading-none ${
                                    transcriptSaveStatus === 'failed'
                                        ? 'bg-error-container text-error'
                                        : transcriptSaveStatus === 'saving'
                                            ? 'bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white'
                                            : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                                }`}>
                                    <SvgIcon
                                        name={transcriptSaveStatus === 'saving'
                                            ? 'sync'
                                            : transcriptSaveStatus === 'failed'
                                            ? 'error'
                                            : 'check_circle'}
                                        className={`text-[12px] ${transcriptSaveStatus === 'saving' ? 'animate-spin' : ''}`}
                                    />
                                    {transcriptSaveStatus === 'saving'
                                        ? t('edit.transcriptSaving')
                                        : transcriptSaveStatus === 'failed'
                                            ? t('edit.transcriptSaveFailed')
                                            : (lang === 'zh' ? '转录已保存' : 'Transcript saved')}
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="ml-auto flex shrink-0 flex-wrap items-center justify-end gap-2">
                        {/* The follow switch and the speed sit here in both review
                            modes, beside the switch between them. */}
                        {mediaUrl && (
                            <>
                                <FeatureHint id="transcript-follow" align="right" lang={lang}>{followSwitch}</FeatureHint>
                                <PlaybackSpeedMenu rate={playbackRate} onChange={setPlaybackRate} lang={lang} align="right"/>
                            </>
                        )}
                        {canShowVideoReview && segments.length > 0 && (
                            <div className={segmentGroup}>
                                <button
                                    type="button"
                                    onClick={()=>switchReviewMode('text')}
                                    className={segmentItem(activeReviewMode === 'text', 'sm')}
                                >
                                    {lang === 'zh' ? '文本校对' : 'Text review'}
                                </button>
                                <button
                                    type="button"
                                    onClick={()=>{ if (canUseVideoReview) switchReviewMode('video'); }}
                                    disabled={!canUseVideoReview}
                                    title={!canUseVideoReview ? (lang === 'zh' ? '选择原视频并保留时间戳后可用' : 'Available after choosing source video with timestamps') : undefined}
                                    className={segmentItem(activeReviewMode === 'video', 'sm')}
                                >
                                    {lang === 'zh' ? '视频复查' : 'Video review'}
                                </button>
                            </div>
                        )}
                        {hasBilingualTranscript && segments.length > 0 && (
                            <div className={segmentGroup}>
                                <button
                                    type="button"
                                    onClick={()=>setTranscriptView('bilingual')}
                                    className={segmentItem(visibleTranscriptView === 'bilingual', 'sm')}
                                >
                                    {lang === 'zh' ? '中英对照' : 'Bilingual'}
                                </button>
                                <button
                                    type="button"
                                    onClick={()=>setTranscriptView('raw')}
                                    className={segmentItem(visibleTranscriptView === 'raw', 'sm')}
                                >
                                    {lang === 'zh' ? '原始字幕' : 'Original'}
                                </button>
                            </div>
                        )}
                        {visibleEditRecords.length > 0 && (
                            <button
                                type="button"
                                onClick={()=>setEditRecordsOpen(true)}
                                className={button('secondary', 'sm')}
                            >
                                <SvgIcon name="edit_note" className="text-[17px]"/>
                                <span>{t('edit.editRecords')}</span>
                                <span className="tabular-nums text-primary">{visibleEditRecords.length}</span>
                            </button>
                        )}
                    </div>
                </div>
            </div>
    {/* One list and one player for both modes; switching only
        changes the layout. Rebuilding them made a switch take
        half a second on a 7104-sentence transcript, stopped
        playback while the page still showed it playing, and
        lost the reading position. */}
    {/* Video review: the video takes all the height it can use and
        the sentence list below keeps about three lines (the one
        playing, one before, one after: decision 6.66); a narrow
        column, where the video is held by its width, leaves the
        list the rest. */}
    <div className={shouldShowVideoReview ? 'flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-4' : 'flex min-h-0 flex-1 flex-col overflow-hidden'}>
        <video
            ref={mediaRef}
            src={mediaUrl || undefined}
            controls={shouldShowVideoReview}
            controlsList="noplaybackrate"
            className={shouldShowVideoReview ? 'min-h-0 w-full shrink rounded-[12px] bg-black object-contain' : 'hidden'}
            onTimeUpdate={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration})}
            onLoadedMetadata={(e)=>{ e.currentTarget.playbackRate = playbackRate; restoreMediaPosition(e.currentTarget, e.currentTarget.duration || durSec || 0); }}
            onSeeked={(e)=>updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true})}
            onPlay={()=>setMediaPlaying(true)}
            onPause={(e)=>{ updateMediaCurrentTime(e.currentTarget.currentTime || 0, {duration: e.currentTarget.duration || playbackDuration, force: true}); setMediaPlaying(false); }}
            onEnded={()=>setMediaPlaying(false)}
        />
        <div
            ref={transcriptScrollRef}
            data-testid="transcript-scroll"
            onWheel={pauseFollowing}
            onTouchMove={pauseFollowing}
            onBlur={leaveSentence}
            onKeyDown={sentenceKeys}
            onCompositionStart={() => { transcriptComposingRef.current = true; }}
            onCompositionEnd={() => { transcriptComposingRef.current = false; }}
            className={shouldShowVideoReview
                ? 'hide-scrollbar min-h-[9.75rem] flex-1 overflow-y-auto rounded-[12px] border border-[#e4e0e0] bg-[#fbfbfb] px-4 py-2 dark:border-white/[0.12] dark:bg-white/[0.05]'
                : 'hide-scrollbar min-h-0 flex-1 overflow-y-auto p-3'}
        >
            <div className={shouldShowVideoReview ? 'divide-y divide-[#e4e0e0] dark:divide-white/[0.08]' : 'space-y-1.5'}>
        {visibleTranscriptView === 'bilingual' && bilingualTranscriptSegments.length > 0 ? bilingualTranscriptSegments.map((seg,i) => (
            <BilingualLine key={`bilingual-${i}`} index={i} seg={seg} active={i===activeSegmentIndex && !!mediaUrl} review={shouldShowVideoReview} onSeek={seekRow} registerNode={registerRow}/>
        )) : segments.length > 0 && !shouldShowVideoReview ? transcriptParagraphs.map((paragraph) => (
            <TranscriptParagraph
                key={paragraph.indices[0]}
                paragraph={paragraph}
                segments={segments}
                activeIndex={mediaUrl && paragraph.indices.includes(activeSegmentIndex) ? activeSegmentIndex : -1}
                onSeek={seekRow}
                onEdit={editRow}
                onFocusSegment={focusSentence}
                registerNode={registerRow}
            />
        )) : segments.length > 0 ? segments.map((seg,i) => (
            <TranscriptLine key={i} index={i} seg={seg} active={i===activeSegmentIndex && !!mediaUrl} onSeek={seekRow} onEdit={editRow} onFocusLine={stopFollowing} registerNode={registerRow}/>
            )) : isTranscriptHydrationPending ? (
                <div className="flex min-h-[320px] items-center justify-center rounded-[12px] border border-dashed border-outline-variant bg-surface-container-low px-4 py-8 text-center">
                    <div className="max-w-[28rem]">
                        <SvgIcon name="sync" className="mx-auto mb-3 h-5 w-5 animate-spin text-primary"/>
                        <p className="text-sm font-bold text-on-surface">
                            {lang === 'zh' ? '正在加载逐段转录' : 'Loading timestamped transcript'}
                        </p>
                        <p className="mt-1 text-xs font-semibold leading-relaxed text-on-surface-variant">
                            {lang === 'zh'
                                ? '刚从最近活动打开时会先读取轻量缓存，完整时间戳分段正在从处理记录补全。'
                                : 'FluentFlow opened a lightweight cached result first and is restoring the full timestamped segments from the processing record.'}
                        </p>
                    </div>
                </div>
            ) : isTranscriptHydrationFailed ? (
                <div className="flex min-h-[320px] items-center justify-center rounded-[12px] border border-amber-200 bg-amber-50 px-4 py-8 text-center text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-100">
                    <div className="max-w-[30rem]">
                        <SvgIcon name="info" className="mx-auto mb-3 h-5 w-5 text-amber-600 dark:text-amber-200"/>
                        <p className="text-sm font-bold">
                            {lang === 'zh' ? '完整逐段转录还没加载出来' : 'Full timestamped transcript did not load'}
                        </p>
                        <p className="mt-1 text-xs font-semibold leading-relaxed text-amber-800 dark:text-amber-100/75">
                            {lang === 'zh'
                                ? '当前只拿到了浏览器缓存预览，不能当作完整转录编辑。请回到处理记录刷新，或稍后重新打开结果。'
                                : 'Only a cached preview is available, so this is not safe to edit as the full transcript. Refresh processing records or reopen the result later.'}
                        </p>
                    </div>
                </div>
            ) : (
                <div className="min-h-full flex flex-col gap-2">
                    <div className="flex items-center gap-2 rounded-[12px] border border-[#d6dcff] bg-[#eef2ff] px-3 py-2 dark:border-white/[0.12] dark:bg-white/[0.08]">
                        <SvgIcon name="info" className="text-primary text-[15px] flex-shrink-0"/>
                        <p className="truncate text-xs font-semibold text-on-surface-variant" title={lang==='zh'
                            ? '当前结果没有时间戳分段。在首页把这条录像重新处理一遍，就能逐段校对。'
                            : 'This result has no timestamped segments. Process the recording again from the start page to review it segment by segment.'}>
                            {lang==='zh'
                                ? '当前结果没有时间戳分段。在首页把这条录像重新处理一遍，就能逐段校对。'
                                : 'No timestamped segments. Process the recording again from the start page to review it segment by segment.'}
                        </p>
                    </div>
                <textarea
                    value={transcript}
                    onChange={(e)=>handlePlainTranscriptChange(e.target.value)}
                                                            onFocus={()=>setFollowPlayback(false)}
                    className="min-h-[320px] w-full flex-1 resize-none whitespace-pre-wrap border-none bg-transparent p-0 text-sm font-medium leading-relaxed text-[#111111] focus:ring-0 dark:text-white"
                />
                </div>
            )}
            </div>
        </div>
        {!shouldShowVideoReview && (
            <div className="border-t border-[#e4e0e0] bg-[#fbfbfb]/90 p-4 dark:border-white/[0.12] dark:bg-white/[0.04]">
                {mediaUrl ? (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-3">
                            <button type="button" onClick={togglePlayback} aria-label={mediaPlaying ? (lang === 'zh' ? '暂停' : 'Pause') : (lang === 'zh' ? '播放' : 'Play')} title={mediaPlaying ? (lang === 'zh' ? '暂停' : 'Pause') : (lang === 'zh' ? '播放' : 'Play')} className={iconButton('primary', 'md')}>
                                <SvgIcon name={mediaPlaying ? 'pause' : 'play_arrow'} className="text-lg"/>
                            </button>
                            {/* Which of the two files is playing. Both exist for a
                                cut task and they are different lengths, so leaving
                                this out makes the timeline unexplainable. */}
                            {playingCutVersion && (
                                <span className="inline-flex items-center gap-1 rounded-[12px] bg-[#eef2ff] px-2 py-1 text-[11px] font-bold text-primary dark:bg-white/[0.1] dark:text-white">
                                    <SvgIcon name="content_cut" className="text-[13px]"/>
                                    {lang === 'zh' ? '剪后的版本' : 'Cut version'}
                                </span>
                            )}
                            <PlaybackBar clock={playbackClock} duration={playbackDuration} onSeek={seekMediaTo}/>
                    </div>
                ) : (
                    // Wraps: in a narrow pane the button goes under the line
                    // instead of squeezing it into a column of two-word rows.
                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                        <p className="min-w-0 flex-1 basis-48 text-xs font-semibold text-[#666] dark:text-white/60">{mediaLoading ? t('edit.sourceLoading') : playbackProblem ? (lang === 'zh' ? playbackProblem : t('edit.playbackAudioFailed')) : cutFileMissing ? t('edit.cutFileMissing') : mediaError ? t('edit.mediaOpenFailed') : t('edit.audioUnavailable')}</p>
                        <button type="button" onClick={()=>mediaInputRef.current?.click()} className={button('secondary', 'sm')}>
                            <SvgIcon name="audio_file" className="text-sm"/>{t('edit.chooseAudio')}
                        </button>
                    </div>
                )}
            </div>
        )}
    </div>
    {/* No de-breath entry here any more. Removing the breath gaps
        is what an upload does to itself, so a panel asking the user
        to run it described a flow that no longer exists — the owner
        saw it on a finished task and asked what it was for. The
        record of the automatic step is the bar under the title.
        Tasks processed before the automatic flow simply do not
        mention it; re-uploading is the way to give one the
        treatment, and the Agent API still exposes the action for a
        script that wants it. */}
        </section>
    );
};

export default TranscriptPane;
