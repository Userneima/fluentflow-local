import {BUILTIN_EXTRA_PROMPT_KEYS, editorPresetKeyOrder} from '../lib/promptPresets.js';
import SvgIcon from '../components/SvgIcon.jsx';

const builtinPromptValue = ({
    promptKey,
    autoTranscriptNotesEdit,
    meetingEdit,
    researchEdit,
    quickBulletsEdit,
}) => {
    if (promptKey === 'autoTranscriptNotes') return autoTranscriptNotesEdit;
    if (promptKey === 'meeting') return meetingEdit;
    if (promptKey === 'research') return researchEdit;
    return quickBulletsEdit;
};

export default function PromptTemplateDialog({
    open,
    onClose,
    t,
    lang,
    settings,
    promptKey,
    presetLabel,
    handlePromptKeyChange,
    handleDeleteUserPreset,
    resetBuiltinExtra,
    defaultPromptEdit,
    handleDefaultPromptChange,
    userPresetEdit,
    handleUserPresetChange,
    customText,
    handleCustomTextChange,
    presetNameInput,
    setPresetNameInput,
    saveCustomAsPresetFromEditor,
    autoTranscriptNotesEdit,
    meetingEdit,
    researchEdit,
    quickBulletsEdit,
    handleBuiltinExtraChange,
}) {
    if (!open) return null;

    const presetRowClass = (active) => [
        'group flex w-full items-center gap-1 rounded-[14px] border p-1 text-left transition',
        active
            ? 'border-[#111111] bg-[#111111] text-white dark:border-white/[0.18] dark:bg-white/[0.12] dark:text-white'
            : 'border-transparent bg-transparent text-[#676970] hover:border-[#dedada] hover:bg-[#f4f3f3] hover:text-[#111111] dark:text-white/58 dark:hover:border-white/[0.12] dark:hover:bg-white/[0.07] dark:hover:text-white',
    ].join(' ');
    const presetNameClass = (hasDelete = false) => [
        'min-w-0 flex-1 rounded-[11px] px-3 py-2.5 text-left text-[13px] font-bold leading-5 transition active:translate-y-px',
        hasDelete ? 'pr-2' : '',
    ].join(' ');
    const deletePresetButtonClass = (active) => [
        'flex size-8 flex-shrink-0 items-center justify-center rounded-[10px] transition active:translate-y-px',
        active
            ? 'text-white/72 hover:bg-white/[0.12] hover:text-white dark:text-white/65 dark:hover:bg-white/[0.10]'
            : 'text-[#9a5555] hover:bg-red-50 hover:text-red-700 dark:text-red-300/72 dark:hover:bg-red-500/10 dark:hover:text-red-200',
    ].join(' ');
    const fieldLabelClass = 'text-[12px] font-bold text-[#676970] dark:text-white/62';
    const editorPaneClass = 'flex h-full min-h-0 flex-col gap-3';
    const textAreaClass = 'min-h-[220px] flex-1 resize-none rounded-[18px] border border-[#dedada] bg-[#fbfbfb] px-4 py-3 font-mono text-[13px] leading-6 text-[#111111] outline-none transition placeholder:text-[#aaa] focus:border-[#111111] focus:bg-white focus:ring-2 focus:ring-[#111111]/10 dark:border-white/[0.12] dark:bg-[#161719] dark:text-white/88 dark:placeholder:text-white/28 dark:focus:border-white/36 dark:focus:bg-[#18191b] dark:focus:ring-white/[0.08]';
    const inputClass = 'min-w-[180px] flex-1 rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-3 py-2.5 text-sm font-semibold text-[#111111] outline-none transition placeholder:text-[#85868c] focus:border-[#111111] focus:bg-white focus:ring-2 focus:ring-[#111111]/10 dark:border-white/[0.12] dark:bg-[#161719] dark:text-white/88 dark:placeholder:text-white/36 dark:focus:border-white/36 dark:focus:bg-[#18191b] dark:focus:ring-white/[0.08]';
    const renderPresetItem = (key) => {
        const active = promptKey === key;
        const deletableUser = key.startsWith('user_');
        const deletableBuiltin = BUILTIN_EXTRA_PROMPT_KEYS.includes(key);
        const hasDelete = deletableUser || deletableBuiltin;
        const onDelete = deletableUser
            ? (e) => handleDeleteUserPreset(key, e)
            : (e) => { e.stopPropagation(); e.preventDefault(); resetBuiltinExtra(key); };
        return (
            <div key={key} className={presetRowClass(active)}>
                <button
                    type="button"
                    onClick={()=>handlePromptKeyChange(key)}
                    className={presetNameClass(hasDelete)}
                >
                    <span className="block truncate">{presetLabel(key)}</span>
                </button>
                {hasDelete ? (
                    <button
                        type="button"
                        title={deletableUser ? t('set.deletePreset') : t('set.deleteBuiltinPrompt')}
                        onClick={onDelete}
                        className={deletePresetButtonClass(active)}
                    >
                        <SvgIcon name="close" className="text-[15px] leading-none"/>
                    </button>
                ) : null}
            </div>
        );
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-5 py-8 backdrop-blur-sm animate-[fadeIn_0.16s_ease-out]" role="dialog" aria-modal="true" onClick={onClose}>
            <div className="flex h-[min(760px,88vh)] w-full max-w-5xl flex-col overflow-hidden rounded-[24px] border border-[#dedada] bg-white shadow-[0_28px_90px_-52px_rgba(17,17,17,.72)] dark:border-white/[0.12] dark:bg-[#1d1f22]" onClick={(e)=>e.stopPropagation()}>
                <div className="flex items-start justify-between gap-4 border-b border-[#ece8e8] px-5 py-4 dark:border-white/[0.10]">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <span className="flex size-9 items-center justify-center rounded-[13px] border border-[#dedada] bg-[#fbfbfb] text-[#111111] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white">
                                <SvgIcon name="auto_fix_high" className="text-lg"/>
                            </span>
                            <span className="font-headline text-[18px] font-extrabold text-[#111111] dark:text-white">{t('prompt.label')}</span>
                        </div>
                        <p className="mt-2 text-[13px] font-semibold text-[#676970] dark:text-white/58">{t('prompt.editHint')}</p>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[13px] border border-[#dedada] bg-[#fbfbfb] text-[#676970] transition hover:bg-[#efeeee] hover:text-[#111111] active:translate-y-px dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/58 dark:hover:bg-white/[0.10] dark:hover:text-white"
                        aria-label={lang === 'zh' ? '关闭' : 'Close'}
                    >
                        <SvgIcon name="close" className="text-lg"/>
                    </button>
                </div>
                <div className="grid min-h-0 flex-1 overflow-hidden md:grid-cols-[230px_minmax(0,1fr)]">
                    <aside className="min-h-0 overflow-y-auto border-b border-[#ece8e8] bg-[#f7f6f6] p-4 dark:border-white/[0.10] dark:bg-white/[0.035] md:border-b-0 md:border-r">
                        <p className="px-2 text-[11px] font-extrabold uppercase tracking-[0.12em] text-[#85868c] dark:text-white/40">
                            {t('prompt.select')}
                        </p>
                        <div className="mt-3 space-y-1.5">
                            {editorPresetKeyOrder(settings).map(renderPresetItem)}
                        </div>
                    </aside>
                    <div className="min-h-0 flex-1 overflow-hidden p-5">
                    {promptKey === 'default' ? (
                        <div className={editorPaneClass}>
                            <label className={fieldLabelClass}>{t('set.editCoursePrompt')}</label>
                            <textarea
                                className={textAreaClass}
                                value={defaultPromptEdit}
                                onChange={(e)=>handleDefaultPromptChange(e.target.value)}
                            />
                        </div>
                    ) : promptKey.startsWith('user_') ? (
                        <div className={editorPaneClass}>
                            <label className={fieldLabelClass}>{lang==='zh'?'编辑该预设':'Edit this preset'}</label>
                            <textarea
                                className={textAreaClass}
                                value={userPresetEdit}
                                onChange={(e)=>handleUserPresetChange(e.target.value)}
                            />
                        </div>
                    ) : promptKey === 'custom' ? (
                        <div className={editorPaneClass}>
                            <label className={fieldLabelClass}>{lang === 'zh' ? '编写自定义提示词' : 'Write a custom prompt'}</label>
                            <textarea
                                className={textAreaClass}
                                placeholder={t('prompt.customPlaceholder')}
                                value={customText}
                                onChange={e=>handleCustomTextChange(e.target.value)}
                            />
                            <div className="flex flex-wrap items-center gap-3 rounded-[18px] border border-[#dedada] bg-[#f7f6f6] p-3 dark:border-white/[0.10] dark:bg-white/[0.045]">
                                <input type="text" className={inputClass} placeholder={t('set.presetNamePh')} value={presetNameInput} onChange={e=>setPresetNameInput(e.target.value)} />
                                <button type="button" onClick={saveCustomAsPresetFromEditor} className="h-10 rounded-[14px] bg-[#111111] px-4 text-sm font-bold text-white transition hover:bg-[#2a2a2a] active:translate-y-px dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                                    {t('prompt.saveAsPreset')}
                                </button>
                            </div>
                        </div>
                    ) : BUILTIN_EXTRA_PROMPT_KEYS.includes(promptKey) ? (
                        <div className={editorPaneClass}>
                            <div className="flex flex-wrap justify-between items-center gap-2">
                                <label className={fieldLabelClass}>{t('set.editBuiltinTemplate')}</label>
                                <button type="button" onClick={()=>resetBuiltinExtra(promptKey)} className="rounded-[10px] px-2 py-1 text-xs font-bold text-[#676970] transition hover:bg-[#efeeee] hover:text-[#111111] dark:text-white/58 dark:hover:bg-white/[0.08] dark:hover:text-white">{t('set.deleteBuiltinPrompt')}</button>
                            </div>
                            <textarea
                                className={textAreaClass}
                                value={builtinPromptValue({promptKey, autoTranscriptNotesEdit, meetingEdit, researchEdit, quickBulletsEdit})}
                                onChange={(e)=>handleBuiltinExtraChange(promptKey, e.target.value)}
                            />
                        </div>
                    ) : null}
                    </div>
                </div>
            </div>
        </div>
    );
}
