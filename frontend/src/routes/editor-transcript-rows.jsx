// The transcript's rows, moved out of editor.jsx: a sentence per line
// (TranscriptLine, for video review), a sentence with its translation
// (BilingualLine) and a paragraph of sentences edited in place
// (TranscriptParagraph, the text view). The list they make up is
// editor-transcript-pane.jsx.
import {memo, useLayoutEffect, useRef} from 'react';
import {autoSizeTextarea, fmtTime} from '../app/shared.jsx';
import {textareaSizesItself} from './editor-helpers.js';
import {timestampButton} from '../lib/ui.js';

// The transcript can hold thousands of sentences (7104 on a 3.5-hour
// interview), so each one is its own memoised row: typing in one sentence or
// the player moving to the next re-renders that row, not the list. Callbacks
// passed in are stable, and a row's sentence object only changes when that
// sentence is edited. Boxes size themselves with CSS (field-sizing); where the
// browser cannot, the page sizes them all in one pass (sizeTextareas).
export const SIZES_ITSELF = textareaSizesItself();
// Rows off screen are skipped in layout and paint until scrolled near
// (content-visibility); the browser's find-in-page still searches them. Laying
// out every one of 7104 rows made switching modes and resizing the pane slow.
// The rows have no CSS transitions: a mode switch restyles every row, and
// transitions there fired about 28,000 transitionend events per switch.
const OFFSCREEN_SKIP = {contentVisibility: 'auto', containIntrinsicSize: 'auto 3rem'};

const SentenceTime = memo(({seg, active, layout = '', onSeek, children}) => (
    <button type="button" onClick={() => onSeek(seg)} className={`${layout} ${timestampButton(active)}`}>
        {children}
    </button>
));

// One sentence per line, for video review next to the player.
export const TranscriptLine = memo(({index, seg, active, onSeek, onEdit, onFocusLine, registerNode}) => (
    <div
        ref={(node) => registerNode(index, node)}
        style={OFFSCREEN_SKIP}
        className={`grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 px-1 py-2 ${active ? 'bg-[#eef2ff] dark:bg-white/[0.08]' : 'hover:bg-white/70 dark:hover:bg-white/[0.04]'}`}
    >
        <SentenceTime
            seg={seg}
            active={active}
            onSeek={onSeek}
            layout="pt-[1px] text-left"
        >
            {fmtTime(seg.start)}
        </SentenceTime>
        <div className="min-w-0 flex-1">
            <textarea
                data-transcript-segment="true"
                value={seg.text || ''}
                onChange={(e) => { if (!SIZES_ITSELF) autoSizeTextarea(e.target); onEdit(index, e.target.value); }}
                onFocus={onFocusLine}
                rows={1}
                className="min-h-[1.45rem] w-full resize-none overflow-hidden border-none bg-transparent p-0 text-sm font-semibold leading-snug text-[#111111] [field-sizing:content] focus:ring-0 dark:text-white"
            />
        </div>
    </div>
));

export const BilingualLine = memo(({index, seg, active, review, onSeek, registerNode}) => (
    <div
        ref={(node) => registerNode(index, node)}
        style={OFFSCREEN_SKIP}
        className={review
            ? `grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 px-1 py-2 ${active ? 'bg-[#eef2ff] dark:bg-white/[0.08]' : 'hover:bg-white/70 dark:hover:bg-white/[0.04]'}`
            : `grid grid-cols-[64px_minmax(0,1fr)] items-start gap-3 rounded-[12px] px-3 py-2.5 ${active ? 'bg-[#eef2ff] dark:bg-white/[0.1]' : 'hover:bg-[#f8f7fb] dark:hover:bg-white/[0.06]'}`}
    >
        <SentenceTime
            seg={seg}
            active={active}
            onSeek={onSeek}
            layout={review ? 'pt-[1px] text-left' : 'flex flex-col items-start justify-start pt-[1px]'}
        >
            {review ? fmtTime(seg.start) : (
                <>
                    <span className="block">{fmtTime(seg.start)}</span>
                    <span className="mt-0.5 block text-[11px] opacity-70">{fmtTime(seg.end)}</span>
                </>
            )}
        </SentenceTime>
        <div className="min-w-0 flex-1">
            <p className={`whitespace-pre-wrap text-sm leading-snug text-[#111111] dark:text-white ${review ? 'font-semibold' : 'font-medium'}`}>
                {seg.text}
            </p>
            <p className={`mt-1.5 border-l-2 border-primary/25 pl-3 text-sm leading-snug text-[#666] ${review ? 'font-semibold dark:text-white/70' : 'font-medium dark:text-white/65'}`}>
                {seg.text_zh}
            </p>
        </div>
    </div>
));

// The text view reads as paragraphs (decision 6.35): a new one where the
// speaker changes or once it is long enough (groupTranscriptParagraphs), the
// speaker named where a new one takes over. Each recognised sentence stays the
// unit of editing, saving, edit records and subtitles.

// One sentence inside a paragraph, edited in place. Its text is put in the DOM
// by hand, so a re-render never moves the caret; Enter does not split it, and
// pasted lines join with spaces. While it is being typed in, the page hands
// back what was typed (a keystroke or two behind) and that is left alone; any
// other text for this sentence came from elsewhere (the version the service
// holds, after a save was refused) and replaces what is shown, and the sentence
// is left, so the old text cannot be typed on and saved over it.
const EditableSegment = memo(({index, text, active, onEdit, onFocusSegment, registerNode}) => {
    const nodeRef = useRef(null);
    const typedRef = useRef([]);
    useLayoutEffect(() => {
        const node = nodeRef.current;
        if (!node || node.textContent === text) return;
        if (document.activeElement === node) {
            if (typedRef.current.includes(text)) return;
            node.textContent = text;
            node.blur();
            return;
        }
        node.textContent = text;
    }, [text]);
    const report = (node) => {
        let value = node.textContent || '';
        if (/[\r\n]/.test(value)) {
            value = value.replace(/\s*[\r\n]+\s*/g, ' ');
            node.textContent = value;
            const range = document.createRange();
            range.selectNodeContents(node);
            range.collapse(false);
            const selection = window.getSelection?.();
            selection?.removeAllRanges();
            selection?.addRange(range);
        }
        typedRef.current = [...typedRef.current.slice(-19), value];
        onEdit(index, value);
    };
    return (
        <span
            ref={(node) => { nodeRef.current = node; registerNode(index, node); }}
            role="textbox"
            aria-label={`第 ${index + 1} 句`}
            contentEditable="plaintext-only"
            suppressContentEditableWarning
            spellCheck={false}
            data-transcript-segment="true"
            onInput={(e) => report(e.currentTarget)}
            onFocus={() => { typedRef.current = []; onFocusSegment(index); }}
            onKeyDown={(e) => { if (e.key === 'Enter') e.preventDefault(); }}
            className={`rounded-[4px] px-[1px] outline-none empty:inline-block empty:min-w-[1em] empty:bg-[#f3f2f6] focus:bg-[#eef2ff] dark:focus:bg-white/[0.1] ${active ? 'bg-[#e3e9ff] dark:bg-white/[0.14]' : 'hover:bg-[#f3f2f6] dark:hover:bg-white/[0.06]'}`}
        />
    );
});

// Re-rendered only when one of its sentences changes or the playing sentence
// enters or leaves it.
const sameParagraph = (prev, next) => (
    prev.activeIndex === next.activeIndex
    && prev.paragraph.label === next.paragraph.label
    && prev.paragraph.start === next.paragraph.start
    && prev.paragraph.indices.length === next.paragraph.indices.length
    && prev.paragraph.indices.every((index, k) => index === next.paragraph.indices[k] && prev.segments[index] === next.segments[index])
);
export const TranscriptParagraph = memo(({paragraph, segments, activeIndex, onSeek, onEdit, onFocusSegment, registerNode}) => (
    <div data-testid="transcript-paragraph" style={OFFSCREEN_SKIP} className="rounded-[12px] px-3 py-2.5 hover:bg-[#fbfbfd] dark:hover:bg-white/[0.03]">
        <div className="mb-1 flex items-baseline gap-2">
            {paragraph.label && (
                <span className="text-xs font-extrabold text-[#111111] dark:text-white">{paragraph.label}</span>
            )}
            <button
                type="button"
                onClick={() => onSeek(paragraph)}
                className={timestampButton(activeIndex >= 0)}
            >
                {fmtTime(paragraph.start)}
            </button>
        </div>
        <p className="text-[15px] font-medium leading-[1.85] text-[#111111] dark:text-white">
            {paragraph.indices.map((index, k) => (
                <span key={index}>
                    {k > 0 ? ' ' : null}
                    <EditableSegment
                        index={index}
                        text={segments[index]?.text || ''}
                        active={index === activeIndex}
                        onEdit={onEdit}
                        onFocusSegment={onFocusSegment}
                        registerNode={registerNode}
                    />
                </span>
            ))}
        </p>
    </div>
), sameParagraph);
