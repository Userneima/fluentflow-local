import {useCallback, useEffect, useRef, useState} from 'react';
import {inputClass, saveButtonClass, cellBase} from './settingsPrimitives.jsx';
import {useApi} from '../app/shared.jsx';
import {simpleMd} from '../lib/markdown.js';

// 「我的笔记风格」 on the settings page.
//
// The user's own note skill grows from their corrections: edited notes become
// candidate rules, the user picks some, and before anything changes the same
// recording is written with the current and the new version side by side.
// Only "用新写法" changes the skill. See docs/plans/2026-10-09-note-skills.md.

const POLL_MS = 5000;
const EXAMPLE_SEPARATOR = /\n\s*={3,}\s*\n/;

const ghostButton = 'rounded-[12px] border border-[#dedada] px-3 py-2 text-xs font-bold text-[#333] transition hover:bg-[#f2f1f1] disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/[0.15] dark:text-white/80 dark:hover:bg-white/[0.08]';

const NotePreview = ({title, markdown}) => (
    <div className="min-w-0 rounded-[14px] border border-[#ece8e8] bg-white dark:border-white/[0.1] dark:bg-white/[0.03]">
        <div className="border-b border-[#ece8e8] px-4 py-2 text-xs font-extrabold dark:border-white/[0.1]">{title}</div>
        <div
            className="note-preview max-h-[60vh] overflow-auto px-4 py-3 text-sm leading-relaxed"
            dangerouslySetInnerHTML={{__html: simpleMd(markdown || '', {renderImages: false})}}
        />
    </div>
);

export const NoteStylePanel = () => {
    const {noteStyle} = useApi();
    const [state, setState] = useState(null);
    const [tasks, setTasks] = useState([]);
    const [taskId, setTaskId] = useState('');
    const [picked, setPicked] = useState([]);
    const [busy, setBusy] = useState('');
    const [error, setError] = useState('');
    const [showSkill, setShowSkill] = useState(false);
    const [examplesOpen, setExamplesOpen] = useState(false);
    const [examples, setExamples] = useState('');
    const [restore, setRestore] = useState('');
    const poll = useRef(null);

    const load = useCallback(async () => {
        try {
            const data = await noteStyle.read();
            setState(data);
            return data;
        } catch (err) {
            setError(err.message);
            return null;
        }
    }, [noteStyle]);

    useEffect(() => {
        load();
        noteStyle.previewTasks().then((data) => {
            const list = data.tasks || [];
            setTasks(list);
            if (list.length) setTaskId((current) => current || list[0].task_id);
        }).catch(() => setTasks([]));
    }, [load, noteStyle]);

    // While a comparison is being written, look again every few seconds.
    const running = state?.proposal?.status === 'running';
    useEffect(() => {
        if (!running) return undefined;
        poll.current = setInterval(load, POLL_MS);
        return () => clearInterval(poll.current);
    }, [running, load]);

    const act = async (label, fn) => {
        setBusy(label);
        setError('');
        try {
            await fn();
            await load();
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy('');
        }
    };

    if (!state) {
        return <p className="px-5 py-4 text-xs text-on-surface-variant">{error || '正在读取…'}</p>;
    }

    const {skill, candidates = [], unread_edits: unreadEdits = 0, proposal, history = []} = state;
    const togglePick = (id) => setPicked((list) => (list.includes(id) ? list.filter((item) => item !== id) : [...list, id]));
    const propose = (payload) => act('propose', async () => {
        await noteStyle.propose({task_id: taskId, ...payload});
        setPicked([]);
        setRestore('');
    });
    const noTask = !tasks.length;

    return (
        <div className="grid gap-4 p-5">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                    现在用的是：<b>{skill.is_default ? 'FluentFlow 默认写法' : '你自己的写法'}</b>
                </span>
                <button type="button" className="text-xs font-semibold text-primary underline" onClick={() => setShowSkill((open) => !open)}>
                    {showSkill ? '收起写法全文' : '查看写法全文'}
                </button>
            </div>
            {showSkill && (
                <pre className={`${cellBase} max-h-[40vh] overflow-auto whitespace-pre-wrap text-xs leading-relaxed`}>{skill.text}</pre>
            )}

            {proposal ? (
                <div className="grid gap-3">
                    {proposal.status === 'running' && (
                        <p className="text-sm">正在用现在的写法和改动后的写法各写一份同一段录像的笔记，一般要 5 到 10 分钟。写好之前写法不会变。</p>
                    )}
                    {proposal.status === 'failed' && (
                        <p className="text-sm text-red-600">这次对比没写成：{proposal.error}</p>
                    )}
                    {proposal.status === 'ready' && (
                        <>
                            {!!proposal.rules?.length && (
                                <ul className="list-disc pl-5 text-sm">
                                    {proposal.rules.map((rule) => <li key={rule}>{rule}</li>)}
                                </ul>
                            )}
                            <div className="grid gap-3 md:grid-cols-2">
                                <NotePreview title="现在的写法" markdown={proposal.old?.markdown}/>
                                <NotePreview title="改动后的写法" markdown={proposal.new?.markdown}/>
                            </div>
                        </>
                    )}
                    <div className="flex flex-wrap gap-2">
                        {proposal.status === 'ready' && (
                            <button type="button" className={saveButtonClass} disabled={!!busy} onClick={() => act('apply', noteStyle.apply)}>
                                用新写法
                            </button>
                        )}
                        {proposal.status !== 'running' && (
                            <button type="button" className={ghostButton} disabled={!!busy} onClick={() => act('discard', noteStyle.discard)}>
                                {proposal.status === 'ready' ? '不用，保持现在的写法' : '关掉'}
                            </button>
                        )}
                    </div>
                </div>
            ) : (
                <>
                    <div className="grid gap-2">
                        {unreadEdits > 0 ? (
                            <button type="button" className={`${saveButtonClass} justify-self-start`} disabled={!!busy} onClick={() => act('edits', noteStyle.fromEdits)}>
                                {busy === 'edits' ? '正在读你改过的笔记…' : `从我改过的 ${unreadEdits} 份笔记里找规则`}
                            </button>
                        ) : (
                            <p className="text-xs leading-relaxed text-on-surface-variant">
                                在编辑器里改过笔记并保存后，这里会从你的改动里找出写法规则。
                            </p>
                        )}
                        {!examplesOpen ? (
                            <button type="button" className="justify-self-start text-xs font-semibold text-primary underline" onClick={() => setExamplesOpen(true)}>
                                用几份我满意的笔记起步
                            </button>
                        ) : (
                            <div className="grid gap-2">
                                <textarea
                                    className={`${inputClass} min-h-[140px] font-normal`}
                                    placeholder={'粘贴你满意的笔记，Markdown 或纯文本都行。多份之间单独一行写 ===。'}
                                    value={examples}
                                    onChange={(event) => setExamples(event.target.value)}
                                />
                                <div className="flex gap-2">
                                    <button
                                        type="button"
                                        className={saveButtonClass}
                                        disabled={!!busy || !examples.trim()}
                                        onClick={() => act('examples', async () => {
                                            await noteStyle.fromExamples(examples.split(EXAMPLE_SEPARATOR));
                                            setExamples('');
                                            setExamplesOpen(false);
                                        })}
                                    >
                                        {busy === 'examples' ? '正在提炼…' : '提炼写法规则'}
                                    </button>
                                    <button type="button" className={ghostButton} onClick={() => setExamplesOpen(false)}>取消</button>
                                </div>
                            </div>
                        )}
                    </div>

                    {candidates.length > 0 && (
                        <div className="grid gap-2">
                            <p className="text-xs font-extrabold">提出的规则：勾选想加的，先看对比再决定</p>
                            {candidates.map((item) => (
                                <label key={item.id} className={`${cellBase} flex cursor-pointer items-start gap-3`}>
                                    <input type="checkbox" className="mt-1" checked={picked.includes(item.id)} onChange={() => togglePick(item.id)}/>
                                    <span className="min-w-0 flex-1">
                                        <span className="block text-sm font-semibold">{item.rule}</span>
                                        {item.evidence && <span className="mt-1 block text-xs text-on-surface-variant">依据：{item.evidence}</span>}
                                        {item.replaces && <span className="mt-1 block text-xs text-on-surface-variant">会替换：{item.replaces}</span>}
                                    </span>
                                    <button
                                        type="button"
                                        className="text-xs font-semibold text-on-surface-variant underline"
                                        onClick={(event) => { event.preventDefault(); act('dismiss', () => noteStyle.dismiss(item.id)); }}
                                    >
                                        不要
                                    </button>
                                </label>
                            ))}
                        </div>
                    )}

                    {(candidates.length > 0 || history.length > 0 || !skill.is_default) && (
                        <div className="grid gap-2">
                            <label className="text-xs font-extrabold" htmlFor="note-style-task">用哪段录像来对比</label>
                            {noTask ? (
                                <p className="text-xs text-on-surface-variant">还没有能用来对比的录像：需要一个 Claude 结合画面写过笔记、录像文件还在的任务。</p>
                            ) : (
                                <select id="note-style-task" className={inputClass} value={taskId} onChange={(event) => setTaskId(event.target.value)}>
                                    {tasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.title}</option>)}
                                </select>
                            )}
                            <div className="flex flex-wrap gap-2">
                                {candidates.length > 0 && (
                                    <button
                                        type="button"
                                        className={saveButtonClass}
                                        disabled={!!busy || noTask || !picked.length}
                                        onClick={() => propose({candidate_ids: picked})}
                                    >
                                        用选中的 {picked.length} 条规则写一份对比
                                    </button>
                                )}
                                {history.length > 0 && (
                                    <>
                                        <select className={`${inputClass} w-auto`} value={restore} onChange={(event) => setRestore(event.target.value)}>
                                            <option value="">退回到以前的写法…</option>
                                            {history.map((item) => <option key={item.name} value={item.name}>{item.saved_at}</option>)}
                                        </select>
                                        <button type="button" className={ghostButton} disabled={!!busy || noTask || !restore} onClick={() => propose({restore})}>
                                            对比这个旧版本
                                        </button>
                                    </>
                                )}
                                {!skill.is_default && (
                                    <button type="button" className={ghostButton} disabled={!!busy || noTask} onClick={() => propose({use_default: true})}>
                                        对比默认写法
                                    </button>
                                )}
                            </div>
                        </div>
                    )}
                </>
            )}
            {error && <p className="text-xs font-semibold text-red-600">{error}</p>}
        </div>
    );
};

export default NoteStylePanel;
