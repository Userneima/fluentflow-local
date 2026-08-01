import {forwardRef, useImperativeHandle, useRef} from 'react';
import {useVirtualizer} from '@tanstack/react-virtual';

const VirtualTranscriptList = forwardRef(function VirtualTranscriptList({
    activeIndex = -1,
    bilingual = false,
    className = '',
    formatTime = defaultFormatTime,
    highlightActive = false,
    onFocusSegment,
    onSeek,
    onSegmentChange,
    readOnly = false,
    resizeTextarea,
    segments = [],
    variant = 'text',
}, ref) {
    const parentRef = useRef(null);
    const videoReview = variant === 'video';
    const virtualizer = useVirtualizer({
        count: segments.length,
        estimateSize: () => (bilingual ? 92 : videoReview ? 54 : 66),
        getScrollElement: () => parentRef.current,
        overscan: 10,
    });

    useImperativeHandle(ref, () => ({
        scrollToIndex: (index) => {
            if (Number.isInteger(index) && index >= 0 && index < segments.length) {
                virtualizer.scrollToIndex(index, {align: 'center'});
            }
        },
    }), [segments.length, virtualizer]);

    const rootClass = videoReview
        ? `hide-scrollbar min-h-[10rem] flex-1 overflow-y-auto rounded-[18px] border border-[#e4e0e0] bg-[#fbfbfb] px-4 py-2 dark:border-white/[0.12] dark:bg-white/[0.05] ${className}`
        : `hide-scrollbar min-h-0 flex-1 overflow-y-auto p-3 ${className}`;

    return (
        <div ref={parentRef} className={rootClass}>
            <div style={{height: `${virtualizer.getTotalSize()}px`, position: 'relative', width: '100%'}}>
                {virtualizer.getVirtualItems().map((virtualRow) => {
                    const index = virtualRow.index;
                    const segment = segments[index];
                    const active = index === activeIndex && (videoReview || highlightActive);
                    const rowClass = videoReview
                        ? `grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 px-1 py-2 transition-colors border-b border-[#e4e0e0] last:border-b-0 dark:border-white/[0.08] ${active ? 'bg-[#eef2ff] dark:bg-white/[0.08]' : 'hover:bg-white/70 dark:hover:bg-white/[0.04]'}`
                        : `group grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 rounded-[16px] px-3 py-2.5 transition-colors ${active ? 'bg-[#eef2ff] dark:bg-white/[0.1]' : 'hover:bg-[#f8f7fb] dark:hover:bg-white/[0.06]'}`;

                    return (
                        <div
                            key={index}
                            data-index={index}
                            ref={virtualizer.measureElement}
                            className={rowClass}
                            style={{
                                left: 0,
                                position: 'absolute',
                                top: 0,
                                transform: `translateY(${virtualRow.start}px)`,
                                width: '100%',
                            }}
                        >
                            <button
                                type="button"
                                onClick={() => onSeek?.(segment)}
                                className={videoReview
                                    ? `pt-[1px] text-left font-mono text-xs font-bold tabular-nums transition ${active ? 'text-primary dark:text-white' : 'text-[#8a8a8a] hover:text-primary dark:text-white/42 dark:hover:text-white'}`
                                    : `pt-[1px] text-left font-mono text-xs tabular-nums transition ${active ? 'font-bold text-primary' : 'text-[#8a8a8a] hover:text-primary dark:text-white/40'}`}
                            >
                                {bilingual && !videoReview && <span className="block">{formatTime(segment.start)}</span>}
                                {(!bilingual || videoReview) && formatTime(segment.start)}
                                {bilingual && !videoReview && <span className="mt-0.5 block text-[10px] opacity-70">{formatTime(segment.end)}</span>}
                            </button>
                            {bilingual ? (
                                <div className="min-w-0">
                                    <p className={`whitespace-pre-wrap text-sm ${videoReview ? 'font-semibold' : 'font-medium'} leading-snug text-[#111111] dark:text-white`}>{segment.text}</p>
                                    <p className={`mt-1.5 border-l-2 border-primary/25 pl-3 text-sm ${videoReview ? 'font-semibold dark:text-white/68' : 'font-medium dark:text-white/65'} leading-snug text-[#666]`}>{segment.text_zh}</p>
                                </div>
                            ) : (
                                <div className="min-w-0 flex-1">
                                    <textarea
                                        data-transcript-segment="true"
                                        value={segment.text || ''}
                                        ref={(node) => { if (node) resizeTextarea?.(node); }}
                                        onChange={(event) => {
                                            resizeTextarea?.(event.target);
                                            onSegmentChange?.(index, event.target.value);
                                        }}
                                        readOnly={readOnly}
                                        onFocus={() => onFocusSegment?.()}
                                        rows={1}
                                        className={`w-full resize-none overflow-hidden border-none bg-transparent p-0 text-sm leading-snug text-[#111111] focus:ring-0 dark:text-white ${videoReview ? 'min-h-[1.45rem] font-semibold' : 'min-h-[1.75rem] font-medium'}`}
                                    />
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
});

const defaultFormatTime = (value) => {
    const seconds = Math.max(0, Number(value) || 0);
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.floor(seconds % 60);
    return `${minutes}:${String(remainder).padStart(2, '0')}`;
};

export default VirtualTranscriptList;
