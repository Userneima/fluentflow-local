import {useEffect, useRef, useState} from 'react';
import SvgIcon from './SvgIcon.jsx';
import {inputClass, saveButtonClass} from './settingsPrimitives.jsx';
import {useApi, useSettings} from '../app/shared.jsx';
import {DEFAULT_DEEPSEEK_MODEL} from '../lib/settingsModel.js';
import {resolveNoteWriter} from '../lib/noteWriter.js';
import {NOTE_KEY_DONE, NOTE_KEY_INTRO, noteKeyChoices} from '../lib/noteKeyOnboarding.js';

// The first-run note setup (see lib/noteKeyOnboarding.js for when it opens).
//
//   runtimeConfig       — decides whether the Claude choice is offered
//   onClose             — closing by hand; the caller records the dismissal
//   onStatusChange      — the fresh /credentials/status after a save, so the
//                         page's banner and writer sentence follow at once
//   onUseTextProvider   — switches the stored text model to DeepSeek; the
//                         settings page passes its own updater so its state
//                         does not overwrite the change later
//   closeDelayMs        — how long the success line stays before closing
export const NoteKeyOnboarding = ({
    runtimeConfig = {},
    onClose,
    onStatusChange,
    onUseTextProvider,
    closeDelayMs = 2500,
}) => {
    const {saveCredentials, checkCredential, getCredentialsStatus} = useApi();
    const {loadSettings, saveSettings} = useSettings();
    const choices = noteKeyChoices({writesItsOwnNote: !!runtimeConfig?.writesItsOwnNote});
    const [choiceId, setChoiceId] = useState(choices[0].id);
    const choice = choices.find((item) => item.id === choiceId) || choices[0];
    const [draft, setDraft] = useState('');
    const [busy, setBusy] = useState(false);
    // {tone: 'ok' | 'warn' | 'error', text}
    const [feedback, setFeedback] = useState(null);
    const [done, setDone] = useState(false);
    const closeTimer = useRef(null);
    useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
    const canPaste = typeof navigator !== 'undefined' && typeof navigator.clipboard?.readText === 'function';

    const pickChoice = (id) => {
        setChoiceId(id);
        setDraft('');
        setFeedback(null);
    };

    const paste = async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (text) setDraft(text.trim());
        } catch (_) {
            setFeedback({tone: 'warn', text: '没能读取剪贴板，请在输入框里按 Ctrl+V 或 ⌘V 粘贴。'});
        }
    };

    const switchNotesToDeepSeek = () => {
        const patch = {aiProvider: 'deepseek', aiModel: DEFAULT_DEEPSEEK_MODEL};
        const current = loadSettings();
        if ((current.aiProvider || 'deepseek') === 'deepseek') {
            return current;
        }
        if (onUseTextProvider) onUseTextProvider(patch);
        else saveSettings({...current, ...patch});
        return {...current, ...patch};
    };

    const checkAndSave = async () => {
        const key = draft.trim();
        if (!key || busy) return;
        setBusy(true);
        setFeedback(null);
        try {
            await saveCredentials({[choice.secretKey]: key});
            let result;
            try {
                result = await checkCredential(choice.id);
            } catch (err) {
                result = {ok: false, reason: 'network', message: '没能检查这个 Key，请稍后再试。'};
            }
            if (result?.reason === 'invalid_key') {
                // A key the provider rejects would make every page say notes
                // are on; take it back out.
                await saveCredentials({[choice.secretKey]: ''}).catch(() => {});
            }
            const settings = result?.reason === 'invalid_key' || choice.id !== 'deepseek'
                ? loadSettings()
                : switchNotesToDeepSeek();
            const status = await getCredentialsStatus().catch(() => null);
            if (status) onStatusChange?.(status);
            if (result?.ok) {
                const writer = resolveNoteWriter({runtimeConfig, credentialStatus: status, settings});
                if (status && writer.kind === 'none') {
                    setFeedback({tone: 'warn', text: 'Key 可以用，已经保存，但笔记暂时还写不了。到「设置 → 笔记」看看原因。'});
                    return;
                }
                setDraft('');
                setDone(true);
                closeTimer.current = setTimeout(() => onClose?.({completed: true}), closeDelayMs);
                return;
            }
            const kept = result?.reason !== 'invalid_key';
            setFeedback({
                tone: kept ? 'warn' : 'error',
                text: `${result?.message || '检查没有通过。'}${kept ? 'Key 已经保存。' : ''}`,
            });
        } catch (err) {
            setFeedback({tone: 'error', text: '保存失败，请确认 FluentFlow 还在运行后再试。'});
        } finally {
            setBusy(false);
        }
    };

    const toneClass = {
        ok: 'text-[#1f6b46] dark:text-[#9fe0bd]',
        warn: 'text-[#7a5a12] dark:text-[#f0dfb0]',
        error: 'text-[#b3261e] dark:text-[#ffb4ab]',
    };

    return (
        <section
            data-testid="note-key-onboarding"
            aria-label="设置写笔记的模型"
            className="mb-5 rounded-[20px] border border-[#dedada] bg-white p-6 text-[#111111] dark:border-white/[0.12] dark:bg-[#1d1f22] dark:text-white/[0.92]"
        >
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 className="font-headline text-base font-extrabold">让它帮你写笔记</h2>
                    <p className="mt-2 max-w-[760px] text-sm leading-relaxed text-on-surface-variant">{NOTE_KEY_INTRO}</p>
                </div>
                {!done && (
                    <button
                        type="button"
                        onClick={() => onClose?.({completed: false})}
                        className="shrink-0 rounded-full p-1.5 text-on-surface-variant transition hover:bg-black/5 dark:hover:bg-white/10"
                        aria-label="关闭"
                    >
                        <SvgIcon name="close" className="text-lg"/>
                    </button>
                )}
            </div>

            {done ? (
                <p role="status" className={`mt-4 text-sm font-extrabold ${toneClass.ok}`}>{NOTE_KEY_DONE}</p>
            ) : (
                <>
                    <div role="radiogroup" aria-label="选一个写笔记的模型" className="mt-4 grid gap-3 md:grid-cols-2">
                        {choices.map((item) => {
                            const selected = item.id === choice.id;
                            return (
                                <div
                                    key={item.id}
                                    className={`rounded-[16px] border p-4 transition ${selected ? 'border-[#111111] bg-[#f8f7fb] dark:border-white/50 dark:bg-white/[0.08]' : 'border-[#e4e0e0] dark:border-white/[0.12]'}`}
                                >
                                    <button
                                        type="button"
                                        role="radio"
                                        aria-checked={selected}
                                        onClick={() => pickChoice(item.id)}
                                        className="w-full text-left"
                                    >
                                        <span className="text-sm font-extrabold">{item.title}</span>
                                        <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">{item.what}</span>
                                        <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">{item.cost}</span>
                                    </button>
                                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                                        <a className="font-semibold text-primary underline" href={item.keyUrl} target="_blank" rel="noreferrer">去申请 Key</a>
                                        <a className="font-semibold text-primary underline" href={item.pricingUrl} target="_blank" rel="noreferrer">官方价格</a>
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    <p className="mt-4 text-xs leading-relaxed text-on-surface-variant">
                        打开「去申请 Key」，登录后新建一个 Key 并复制，粘贴到下面。
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                        <input
                            className={`${inputClass} min-w-[220px] flex-1`}
                            type="password"
                            autoComplete="off"
                            aria-label={`${choice.id === 'anthropic' ? 'Anthropic' : 'DeepSeek'} 的 API Key`}
                            placeholder={`粘贴 ${choice.id === 'anthropic' ? 'Anthropic' : 'DeepSeek'} 的 API Key`}
                            value={draft}
                            onChange={(event) => setDraft(event.target.value)}
                            onKeyDown={(event) => { if (event.key === 'Enter') checkAndSave(); }}
                        />
                        {canPaste && (
                            <button
                                type="button"
                                onClick={paste}
                                className="shrink-0 rounded-[14px] border border-[#dedada] px-4 py-3 text-sm font-extrabold transition hover:bg-black/5 dark:border-white/[0.12] dark:hover:bg-white/10"
                            >
                                粘贴
                            </button>
                        )}
                        <button type="button" disabled={busy || !draft.trim()} onClick={checkAndSave} className={saveButtonClass}>
                            {busy ? '正在检查…' : '检查并保存'}
                        </button>
                    </div>
                    {feedback && (
                        <p role="status" className={`mt-2 text-xs font-semibold leading-relaxed ${toneClass[feedback.tone] || ''}`}>{feedback.text}</p>
                    )}
                    <button
                        type="button"
                        onClick={() => onClose?.({completed: false})}
                        className="mt-4 text-xs font-semibold text-on-surface-variant underline"
                    >
                        以后再说，先只要转录稿
                    </button>
                </>
            )}
        </section>
    );
};

export default NoteKeyOnboarding;
