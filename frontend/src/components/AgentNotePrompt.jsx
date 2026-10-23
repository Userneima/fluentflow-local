import {useState} from 'react';
import {agentNotePrompt} from '../lib/noteWriter.js';
import {useApp} from '../app/AppContext.jsx';
import {button} from '../lib/ui.js';

// Shown where a finished task has no note. With no model key configured this is
// the normal state, not a failure: the person's own agent writes the note from
// the same material Claude would get, so the page hands them the one sentence
// to paste into it.
export default function AgentNotePrompt({result, lang = 'zh'}) {
    const [copied, setCopied] = useState(false);
    const zh = lang === 'zh';
    const {runtimeConfig} = useApp() || {};
    const prompt = agentNotePrompt(result, lang, runtimeConfig || {});
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(prompt);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            setCopied(false);
        }
    };
    return (
        <div className="mt-3 space-y-2 rounded-[12px] border border-[#e4e0e0] bg-[#fbfbfb] px-3 py-3 text-sm not-italic text-[#333] dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-white/80">
            <p className="text-[13px] font-semibold">
                {zh ? '这份笔记可以交给你的 agent（Claude Code、Codex、WorkBuddy 等）用它自己的订阅来写。把下面这段发给它，没接入过 FluentFlow 也没关系，它会自己接上：' : 'Your agent (Claude Code, Codex, WorkBuddy, ...) can write this note on its own subscription. Send it this; it connects to FluentFlow itself if it has not yet:'}
            </p>
            <p className="select-all whitespace-pre-line break-all rounded-[10px] bg-white px-3 py-2 text-[12px] leading-relaxed dark:bg-white/[0.06]">{prompt}</p>
            <button
                type="button"
                onClick={copy}
                className={button('secondary', 'sm')}
            >
                {copied ? (zh ? '已复制' : 'Copied') : (zh ? '复制这句' : 'Copy')}
            </button>
        </div>
    );
}
