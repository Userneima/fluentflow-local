import {useState} from 'react';
import {Bot, LoaderCircle} from 'lucide-react';
import {API_BASE, apiFetch} from '../app/shared.jsx';
import {button} from '../lib/ui.js';

// The task stopped after transcription for the user's own agent to judge
// what to cut (backend/core/agent_cut.py). The page says so, and offers to
// let FluentFlow judge instead: the agent's conversation may be gone, and the
// user should not have to wait for the time limit to find that out.
//
// After the waiting is over, a task FluentFlow judged because the agent did
// not answer says why; the processing report below says the rest.

const AgentCutWait = ({job}) => {
    const taskId = job?.task_id || job?.id;
    const state = job?.result?.agent_cut;
    const [busy, setBusy] = useState(false);
    const [asked, setAsked] = useState(false);
    const [error, setError] = useState('');
    if (!taskId || !state?.status) return null;

    if (state.status === 'fallback' && state.fallback_reason) {
        // One sentence of state. The reason the person pressed the button
        // themselves needs no echo; the others say what happened to the agent.
        const reason = String(state.fallback_reason).replace(/[。.]$/, '');
        const chosenHere = /自己判断/.test(reason);
        return (
            <p data-testid="agent-cut-fallback" className="mt-3 text-[12px] font-semibold leading-5 text-[#676970] dark:text-white/60">
                {chosenHere ? '剪辑由 FluentFlow 判断（你选的）。' : `${reason}，剪辑改由 FluentFlow 判断。`}
            </p>
        );
    }
    if (state.status !== 'waiting') return null;

    const selfJudge = async () => {
        setBusy(true);
        setError('');
        try {
            const response = await apiFetch(`${API_BASE}/jobs/${encodeURIComponent(taskId)}/agent-cut/self-judge`, {method: 'POST'});
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data?.detail || `没成功（${response.status}）`);
            }
            setAsked(true);
        } catch (exc) {
            setError(exc.message);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div data-testid="agent-cut-wait" className="mt-3 flex flex-wrap items-center gap-2 rounded-[12px] border border-[#d6dcff] bg-[#eef2ff] px-3 py-2 text-[12px] font-semibold leading-5 text-[#46536f] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white/75">
            <Bot className="size-4 shrink-0 text-primary" strokeWidth={2.15}/>
            <span className="min-w-0 flex-1">
                {asked ? '好的，马上由 FluentFlow 自己判断剪哪里。' : '转录好了，在等你的 agent 判断剪哪里。agent 的对话关掉或太久没回，会自动改由 FluentFlow 判断。'}
            </span>
            {!asked ? (
                <button
                    type="button"
                    disabled={busy}
                    onClick={selfJudge}
                    className={button('secondary', 'sm')}
                >
                    {busy ? <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.15}/> : null}
                    让 FluentFlow 自己判断
                </button>
            ) : null}
            {error ? <span className="w-full text-red-700 dark:text-red-300">{error}</span> : null}
        </div>
    );
};

export default AgentCutWait;
