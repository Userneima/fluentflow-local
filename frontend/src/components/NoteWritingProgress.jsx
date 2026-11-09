import {useEffect, useRef, useState} from 'react';
import {useApi, useI18n} from '../app/shared.jsx';
import {draftBlocks, noteProgressDetail, noteProgressStatus} from '../lib/noteProgress.js';

// The note pane while FluentFlow writes the note: what the writer is doing,
// how long it has taken (and, for a note in parts, about how long the rest
// takes), the chapters and which are written, and the text written so far.
// Read every few seconds from GET /jobs/{id}/note-progress, its own request,
// so the editor's own polling of the task is left as it was. A writer that
// reports nothing (an older run, an agent) leaves `fallback`, the editor's
// one sentence.

export const NOTE_PROGRESS_POLL_MS = 2500;

const BLOCK_CLASS = {
    h1: 'mt-3 text-[16px] font-semibold text-[#333] dark:text-white/80',
    h2: 'mt-3 text-[15px] font-semibold text-[#333] dark:text-white/80',
    h3: 'mt-2 text-[14px] font-semibold text-[#444] dark:text-white/70',
    li: 'pl-3 text-[14px] leading-relaxed text-[#57585d] dark:text-white/60',
    p: 'text-[14px] leading-relaxed text-[#57585d] dark:text-white/60',
};

// Ticks once a second on its own, so only this line redraws.
const ElapsedLine = ({progress, fetchedAt}) => {
    const {t, lang} = useI18n();
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, []);
    const since = progress.stage === 'finishing' ? 0 : Math.max(0, (now - fetchedAt) / 1000);
    return (
        <p data-testid="note-progress-detail" className="text-[12px] text-[#666] dark:text-white/50">
            {noteProgressDetail(progress, t, lang, since)}
        </p>
    );
};

const NoteWritingProgress = ({taskId, fallback = '', requestOptions = {}, onWriterDone = null}) => {
    const {t, lang} = useI18n();
    const api = useApi();
    // Kept in refs: the api object and the options are new on every render.
    const apiRef = useRef(api);
    apiRef.current = api;
    const optionsRef = useRef(requestOptions);
    optionsRef.current = requestOptions;
    const doneRef = useRef(onWriterDone);
    doneRef.current = onWriterDone;
    const [state, setState] = useState({progress: null, draft: '', fetchedAt: 0});

    useEffect(() => {
        if (!taskId || typeof apiRef.current?.getNoteProgress !== 'function') return undefined;
        let stopped = false;
        let inFlight = false;
        let reported = false;
        let finished = false;
        const tick = async () => {
            if (stopped || inFlight) return;
            inFlight = true;
            try {
                const body = await apiRef.current.getNoteProgress(taskId, optionsRef.current);
                if (stopped) return;
                if (body?.progress) {
                    reported = true;
                    setState({progress: body.progress, draft: String(body.draft || ''), fetchedAt: Date.now()});
                } else if (reported && !finished) {
                    // Reported before and gone now: the writer is done. What it
                    // wrote stays, read as saving, until the page shows the note.
                    finished = true;
                    setState((prev) => ({...prev, progress: prev.progress && {...prev.progress, stage: 'finishing'}}));
                    doneRef.current?.();
                }
            } catch (_) {
                // A missed read is tried again on the next tick.
            } finally {
                inFlight = false;
            }
        };
        tick();
        const timer = setInterval(tick, NOTE_PROGRESS_POLL_MS);
        return () => {
            stopped = true;
            clearInterval(timer);
        };
    }, [taskId]);

    const {progress, draft, fetchedAt} = state;
    if (!progress) {
        return <p className="text-sm italic text-[#666] dark:text-white/60">{fallback}</p>;
    }
    const chapters = Array.isArray(progress.chapters) ? progress.chapters : [];
    const blocks = draftBlocks(draft);
    return (
        <div data-testid="note-writing-progress" className="space-y-3">
            <div className="space-y-1">
                <p data-testid="note-progress-status" className="flex items-center gap-2 text-[13px] font-semibold text-[#333] dark:text-white/80">
                    <span aria-hidden="true" className="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-primary"/>
                    {noteProgressStatus(progress, t, lang)}
                </p>
                <ElapsedLine progress={progress} fetchedAt={fetchedAt}/>
            </div>
            {chapters.length > 0 && (
                <ol data-testid="note-progress-chapters" className="space-y-1">
                    {chapters.map((chapter, index) => (
                        <li key={`${index}-${chapter.title}`} className={`flex items-start gap-2 text-[13px] ${chapter.done ? 'text-[#333] dark:text-white/80' : 'text-[#888] dark:text-white/40'}`}>
                            <span aria-hidden="true" className="w-4 shrink-0 text-center">{chapter.done ? '✓' : '·'}</span>
                            <span>{chapter.title || (lang === 'zh' ? `第 ${index + 1} 章` : `Chapter ${index + 1}`)}</span>
                        </li>
                    ))}
                </ol>
            )}
            {blocks.length > 0 && (
                <div data-testid="note-progress-draft" className="rounded-[12px] border border-dashed border-[#e4e1e1] px-4 py-3 dark:border-white/[0.10]">
                    <p className="text-[12px] font-semibold text-[#666] dark:text-white/50">
                        {t('noteProgress.draftTitle')}
                        <span className="font-normal">{lang === 'zh' ? '：' : ': '}{t('noteProgress.draftHint')}</span>
                    </p>
                    <div className="mt-1 space-y-1.5">
                        {blocks.map((block, index) => (
                            // Blocks only grow at the end while a draft streams, so
                            // their place is a stable key.
                            <p key={index} role={block.kind === 'li' ? 'listitem' : undefined} className={BLOCK_CLASS[block.kind]}>
                                {block.kind === 'li' ? `• ${block.text}` : block.text}
                            </p>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};

export default NoteWritingProgress;
