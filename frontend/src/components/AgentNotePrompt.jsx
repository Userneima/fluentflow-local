import {useState} from 'react';
import {agentNotePrompt} from '../lib/noteWriter.js';

// Shown where a finished task has no note. With no model key configured this is
// the normal state, not a failure: the person's own agent writes the note from
// the same material Claude would get, so the page hands them the one sentence
// to paste into it.
export default function AgentNotePrompt({result, lang = 'zh'}) {
    const [copied, setCopied] = useState(false);
    const zh = lang === 'zh';
    const prompt = agentNotePrompt(result, lang);
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
        <div className="mt-3 space-y-2 rounded-[14px] border border-[#e4e0e0] bg-[#fbfbfb] px-3 py-3 text-sm not-italic text-[#333] dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-white/80">
            <p className="text-[13px] font-semibold">
                {zh ? '这份笔记可以交给你的 agent 来写。把下面这句发给 Claude Code 或 Codex：' : 'Your agent can write this note. Send this to Claude Code or Codex:'}
            </p>
            <p className="select-all rounded-[10px] bg-white px-3 py-2 text-[12px] leading-relaxed dark:bg-white/[0.06]">{prompt}</p>
            <button
                type="button"
                onClick={copy}
                className="inline-flex h-8 items-center justify-center rounded-[12px] border border-[#dedada] bg-white px-3 text-[12px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]"
            >
                {copied ? (zh ? '已复制' : 'Copied') : (zh ? '复制这句' : 'Copy')}
            </button>
        </div>
    );
}
