import {useLayoutEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {Link} from 'react-router-dom';
import SvgIcon from './SvgIcon.jsx';
import {useDismissable} from '../lib/useDismissable.js';
import {noteExplanation} from '../lib/noteWriter.js';
import {useApp} from '../app/AppContext.jsx';

// 笔记说明: who wrote the note and from what, asked for instead of shown. The
// paragraph it replaces sat above every fallback note and took a third of a
// small window (owner, 2026-10-18). When the note is not the one the person
// expected (the text model wrote it because Claude could not), the button says
// so with its colour and a dot, so the reason is still one click away.
const NoteExplanation = ({result, lang}) => {
    const zh = lang === 'zh';
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);
    const wrapperRef = useRef(null);
    const buttonRef = useRef(null);
    const panelRef = useDismissable(open, () => setOpen(false), {closeOnOutsideClick: true, outsideOf: wrapperRef});
    const [position, setPosition] = useState(null);
    const {runtimeConfig} = useApp() || {};
    const explanation = noteExplanation(result, lang, runtimeConfig || {});

    // The note panel clips what overflows it (overflow: hidden) and the editor
    // inside it stacks over its toolbar, so the panel is drawn on the page
    // itself, under the button, and kept inside the window.
    useLayoutEffect(() => {
        if (!open) return undefined;
        const place = () => {
            const rect = buttonRef.current?.getBoundingClientRect();
            if (!rect) return;
            const width = Math.min(PANEL_WIDTH, window.innerWidth - 2 * EDGE);
            const left = Math.min(Math.max(EDGE, rect.right - width), window.innerWidth - width - EDGE);
            setPosition({top: rect.bottom + 8, left, width});
        };
        place();
        window.addEventListener('resize', place);
        window.addEventListener('scroll', place, true);
        return () => {
            window.removeEventListener('resize', place);
            window.removeEventListener('scroll', place, true);
        };
    }, [open]);

    const copyPrompt = async () => {
        try {
            await navigator.clipboard.writeText(explanation.agentPrompt);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch (_) {
            setCopied(false);
        }
    };

    const tone = explanation.attention
        ? 'border-[#f5c86b] bg-[#fffaf0] text-[#8a5a00] hover:bg-[#fff3d6] dark:border-[#fdb022]/40 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]'
        : 'border-[#e4e0e0] bg-white text-[#111111] hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.1]';

    return (
        <div className="relative" ref={wrapperRef}>
            <button
                ref={buttonRef}
                type="button"
                data-testid="note-explanation-button"
                aria-haspopup="dialog"
                aria-expanded={open}
                onClick={() => setOpen((value) => !value)}
                className={`relative inline-flex h-8 items-center justify-center gap-1.5 rounded-[13px] border px-3 text-xs font-bold transition ${tone}`}
            >
                <SvgIcon name={explanation.attention ? 'warning' : 'info'} className="text-sm"/>
                {zh ? '笔记说明' : 'About this note'}
                {explanation.attention && (
                    <span data-testid="note-explanation-dot" className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-[#f79009]" aria-hidden="true"/>
                )}
            </button>
            {open && createPortal(
                <div
                    ref={panelRef}
                    role="dialog"
                    aria-label={zh ? '笔记说明' : 'About this note'}
                    data-testid="note-explanation-panel"
                    style={position ? {top: position.top, left: position.left, width: position.width} : {visibility: 'hidden'}}
                    className="fixed z-[100] max-h-[70vh] overflow-y-auto rounded-[16px] border border-[#e4e0e0] bg-white p-4 text-left shadow-xl animate-[fadeIn_0.15s_ease-out] dark:border-white/[0.12] dark:bg-[#1c1c1c]"
                >
                    <div className="mb-3 flex items-center justify-between gap-2">
                        <p className="text-sm font-bold text-[#111111] dark:text-white">{zh ? '这篇笔记是怎么写的' : 'How this note was written'}</p>
                        <button type="button" onClick={() => setOpen(false)} aria-label={zh ? '关闭' : 'Close'} className="rounded-full p-1 text-[#666] hover:bg-[#efeeee] dark:text-white/60 dark:hover:bg-white/[0.1]">
                            <SvgIcon name="close" className="text-sm"/>
                        </button>
                    </div>
                    <dl className="space-y-2 text-xs">
                        {explanation.rows.map((row) => (
                            <div key={row.label} className="grid grid-cols-[64px_1fr] gap-2">
                                <dt className="font-semibold text-[#888] dark:text-white/50">{row.label}</dt>
                                <dd className="font-semibold leading-relaxed text-[#111111] dark:text-white">{row.value}</dd>
                            </div>
                        ))}
                    </dl>
                    {explanation.attention && (
                        <div className="mt-3 space-y-2 border-t border-[#f0eded] pt-3 text-xs leading-relaxed dark:border-white/[0.08]">
                            <p className="font-bold text-[#8a5a00] dark:text-[#fdb022]">{zh ? '为什么不是 Claude 结合画面写的' : 'Why Claude did not write it from the frames'}</p>
                            <p className="text-[#444] dark:text-white/70">{explanation.why}</p>
                            <p className="pt-1 font-bold text-[#111111] dark:text-white">{zh ? '想换成结合画面的笔记' : 'To get a note from the frames'}</p>
                            <ol className="list-decimal space-y-1 pl-4 text-[#444] dark:text-white/70">
                                {explanation.fixes.map((fix) => <li key={fix}>{fix}</li>)}
                            </ol>
                            {explanation.agentPrompt && (
                                <div className="flex items-start gap-2 rounded-[10px] bg-[#f6f5f5] p-2 dark:bg-white/[0.06]">
                                    <p className="flex-1 select-text whitespace-pre-line break-all text-[11px] text-[#555] dark:text-white/60">{explanation.agentPrompt}</p>
                                    <button type="button" onClick={copyPrompt} className="shrink-0 rounded-[8px] border border-[#e4e0e0] bg-white px-2 py-1 text-[11px] font-bold text-[#111111] hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white">
                                        {copied ? (zh ? '已复制' : 'Copied') : (zh ? '复制' : 'Copy')}
                                    </button>
                                </div>
                            )}
                            <Link to="/settings#notes" onClick={() => setOpen(false)} className="inline-flex items-center gap-1 pt-1 font-bold text-[#111111] underline underline-offset-2 dark:text-white">
                                {zh ? '去设置填 Key' : 'Open Settings'}
                                <SvgIcon name="open_in_new" className="text-xs"/>
                            </Link>
                        </div>
                    )}
                </div>,
                document.body,
            )}
        </div>
    );
};

const PANEL_WIDTH = 360;
const EDGE = 16;

export default NoteExplanation;
