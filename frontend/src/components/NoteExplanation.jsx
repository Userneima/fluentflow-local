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
                            {explanation.agentPrompt && (
                                <div data-testid="note-explanation-agent" className="space-y-2 rounded-[12px] border border-[#dfe7ff] bg-[#f5f8ff] p-3 dark:border-white/[0.12] dark:bg-white/[0.05]">
                                    <p className="font-bold text-[#111111] dark:text-white">
                                        {zh ? '推荐：交给你自己的 agent 结合画面重写' : 'Recommended: have your own agent rewrite it from the frames'}
                                    </p>
                                    <p className="text-[#444] dark:text-white/70">
                                        {zh
                                            ? '把下面这段发给 Claude Code、Codex、WorkBuddy 等，它用你已有的订阅来写，不用申请 Key；没接入过 FluentFlow 也能照着自己接上。'
                                            : 'Send this to Claude Code, Codex, WorkBuddy or similar. It writes on the subscription you already have, no key needed, and connects to FluentFlow itself if it has not yet.'}
                                    </p>
                                    <p className="max-h-40 select-text overflow-y-auto whitespace-pre-line break-all rounded-[8px] bg-white p-2 text-[11px] text-[#555] dark:bg-white/[0.06] dark:text-white/60">{explanation.agentPrompt}</p>
                                    <button type="button" onClick={copyPrompt} className="inline-flex h-8 w-full items-center justify-center rounded-[10px] bg-[#111111] text-xs font-bold text-white transition hover:bg-black dark:bg-white dark:text-[#111111]">
                                        {copied ? (zh ? '已复制，去发给你的 agent' : 'Copied — send it to your agent') : (zh ? '复制这段话' : 'Copy')}
                                    </button>
                                </div>
                            )}
                            {explanation.keyOption && (
                                <p className="text-[11px] text-[#888] dark:text-white/50">
                                    {explanation.keyOption}{' '}
                                    <Link to="/settings#notes" onClick={() => setOpen(false)} className="font-semibold underline underline-offset-2">
                                        {zh ? '去设置' : 'Settings'}
                                    </Link>
                                </p>
                            )}
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
