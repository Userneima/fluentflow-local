import {useCallback, useEffect, useRef, useState} from 'react';
import {inputClass, cellBase} from './settingsPrimitives.jsx';
import {useApi} from '../app/shared.jsx';
import {button, linkButton} from '../lib/ui.js';

// Viewing and undo only. Preference changes are proposed and confirmed in
// the user's agent; opening settings never starts model work.
export const NoteStylePanel = () => {
    const {noteStyle} = useApi();
    const api = useRef(noteStyle);
    api.current = noteStyle;
    const [state, setState] = useState(null);
    const [error, setError] = useState('');
    const [showSkill, setShowSkill] = useState(false);
    const [selection, setSelection] = useState('');
    const [confirm, setConfirm] = useState(null);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState('');
    const load = useCallback(async () => {
        try {
            const data = await api.current.read();
            setState(data);
            setError('');
        } catch (_) {
            setError('笔记写法没有读出来，请重试。');
        }
    }, []);
    useEffect(() => {
        load();
        // Returning from the agent may bring a new preference version. Refresh
        // quietly; collapsing the section itself does not discard its data.
        window.addEventListener('focus', load);
        return () => window.removeEventListener('focus', load);
    }, [load]);

    const restore = async () => {
        setBusy(true);
        setError('');
        setMessage('');
        try {
            const data = await api.current.restore({...confirm, base_version: state.skill.version});
            setState(data);
            setSelection('');
            setMessage('已撤回，下次写笔记时生效。');
        } catch (err) {
            // A changed version must be read again before another confirmation.
            try { setState(await api.current.read()); } catch (_) { /* keep the last known view */ }
            setError(err.message || '没能确认撤回结果，请重新读取后再试。');
        } finally {
            setBusy(false);
            setConfirm(null);
        }
    };

    if (!state) return (
        <div className="grid gap-2 p-5 text-xs text-on-surface-variant">
            <p role={error ? 'alert' : undefined}>{error || '正在读取笔记写法…'}</p>
            {error && <button type="button" className={`justify-self-start ${button('secondary', 'sm')}`} onClick={load}>重新读取</button>}
        </div>
    );

    const {skill, history = [], rules_log: rulesLog = []} = state;
    return (
        <div className="grid gap-4 p-5">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>现在用的是：<b>{skill.is_default ? 'FluentFlow 默认写法' : '你自己的写法'}</b></span>
                <button type="button" className={linkButton('blue', 'sm')} onClick={() => setShowSkill((open) => !open)}>
                    {showSkill ? '收起写法全文' : '查看写法全文'}
                </button>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">想调整以后的写法，把想法或改过的笔记告诉你的 agent，确认后会保存为你的个人写法。</p>
            {showSkill && <pre className={`${cellBase} max-h-[40vh] overflow-auto whitespace-pre-wrap text-xs leading-relaxed`}>{skill.text}</pre>}
            {rulesLog.length > 0 && (
                <details className="text-xs leading-relaxed">
                    <summary className="cursor-pointer font-semibold">修改记录和来源</summary>
                    <p className="my-2 text-on-surface-variant">当前生效的内容见写法全文；这里也保留已撤回的修改记录。</p>
                    <ul className="grid gap-2">
                        {rulesLog.map((entry) => (
                            <li key={entry.id} className={`${cellBase} grid gap-1`}>
                                <span className="text-sm">{entry.rule}</span>
                                {entry.feedback?.map((item) => <span key={item.id} className="text-on-surface-variant">来自你的反馈：{item.problem}{item.title ? `（${item.title}）` : ''}</span>)}
                                {entry.evidence && <span className="text-on-surface-variant">核对：{entry.evidence}</span>}
                            </li>
                        ))}
                    </ul>
                </details>
            )}
            {(history.length > 0 || !skill.is_default) && (
                <div className="grid gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                        {history.length > 0 && <>
                            <label className="text-xs font-semibold" htmlFor="note-style-history">撤回到</label>
                            <select id="note-style-history" className={`${inputClass} w-auto max-w-full`} value={selection} disabled={busy} onChange={(event) => { setSelection(event.target.value); setConfirm(null); }}>
                                <option value="">选择以前的写法…</option>
                                {history.map((item) => <option key={item.name} value={item.name}>{item.saved_at}</option>)}
                            </select>
                            <button type="button" className={button('secondary', 'sm')} disabled={busy || !selection} onClick={() => setConfirm({name: selection})}>撤回修改</button>
                        </>}
                        {!skill.is_default && <button type="button" className={button('secondary', 'sm')} disabled={busy} onClick={() => setConfirm({use_default: true})}>恢复默认写法</button>}
                    </div>
                    {confirm && <div className={`${cellBase} grid gap-2`}>
                        <p className="text-xs">{confirm.use_default ? '恢复 FluentFlow 默认写法？' : `切回 ${history.find((item) => item.name === confirm.name)?.saved_at} 保存的写法？`}只影响之后生成的笔记，当前写法会保留在历史里。</p>
                        <div className="flex flex-wrap gap-2">
                            <button type="button" className={button('secondary', 'sm')} disabled={busy} onClick={restore}>{busy ? '正在撤回…' : '确认撤回'}</button>
                            <button type="button" className={button('quiet', 'sm')} disabled={busy} onClick={() => setConfirm(null)}>取消</button>
                        </div>
                    </div>}
                </div>
            )}
            {message && <p role="status" className="text-xs text-on-surface-variant">{message}</p>}
            {error && <p role="alert" className="text-xs font-semibold text-red-600">{error}</p>}
        </div>
    );
};

export default NoteStylePanel;
