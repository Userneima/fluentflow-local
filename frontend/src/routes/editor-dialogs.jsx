// Presentational dialog/modal components extracted from editor.jsx.
// Each component renders the modal body only; the caller keeps the open-state
// toggle ({flag && <Dialog .../>}) so state stays in the Editor component.
import SvgIcon from '../components/SvgIcon.jsx';
import {fmtTime, useI18n} from '../app/shared.jsx';
import {useDismissable} from '../lib/useDismissable.js';
import {CutReport} from '../components/CutReport.jsx';
import {timestampButton, button, iconButton, floatingLayer} from '../lib/ui.js';

const modalOptions = {closeOnOutsideClick: false, trapFocus: true};

export const RegenerateConfirmDialog = ({transcriptTitle, description, warning, confirmDisabled = false, alternative = null, onAlternative, onCancel, onConfirm}) => {
    const {t, lang} = useI18n();
    const dialogRef = useDismissable(true, onCancel, modalOptions);
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="regenerate-confirm-title" className={`w-full max-w-lg overflow-hidden ${floatingLayer}`}>
                <div className="flex items-start gap-4 border-b border-[#e4e0e0] px-6 py-5 dark:border-white/[0.12]">
                    <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[12px] bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white">
                        <SvgIcon name="refresh" className=""/>
                    </div>
                    <div className="min-w-0">
                        <h2 id="regenerate-confirm-title" className="font-headline text-[16px] font-extrabold text-[#111111] dark:text-white">
                            {t('edit.regenerateConfirmTitle')}
                        </h2>
                        <p className="mt-2 text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">
                            {description || t('edit.regenerateConfirmDesc')}
                        </p>
                        {warning && (
                            <p data-testid="regenerate-warning" className="mt-3 rounded-[12px] border border-error/20 bg-error-container px-3 py-2 text-xs font-semibold leading-relaxed text-on-error-container">
                                {warning}
                            </p>
                        )}
                        <div className="mt-4 rounded-[12px] border border-[#e4e0e0] bg-[#f8f7fb] p-3 dark:border-white/[0.12] dark:bg-white/[0.06]">
                            <p className="mb-1 text-[11px] font-bold uppercase tracking-wider text-[#777] dark:text-white/45">{lang === 'zh' ? '当前转录' : 'Current transcript'}</p>
                            <p className="truncate text-sm font-bold text-[#111111] dark:text-white">{transcriptTitle}</p>
                        </div>
                        {alternative && (
                            <div className="mt-3">
                                <button
                                    type="button"
                                    data-testid="regenerate-alternative"
                                    onClick={onAlternative}
                                    disabled={alternative.disabled}
                                    className={`${button('secondary', 'md')} w-full justify-start`}
                                >
                                    {alternative.label}
                                </button>
                                {alternative.hint && (
                                    <p className="mt-1 text-xs font-medium text-[#777] dark:text-white/50">{alternative.hint}</p>
                                )}
                            </div>
                        )}
                    </div>
                </div>
                <div className="flex flex-col-reverse gap-3 px-6 py-4 sm:flex-row sm:justify-end">
                    <button
                        type="button"
                        onClick={onCancel}
                        className={button('secondary', 'md')}
                    >
                        {t('edit.cancel')}
                    </button>
                    <button
                        type="button"
                        onClick={onConfirm}
                        disabled={confirmDisabled}
                        className={button('primary', 'md')}
                    >
                        <SvgIcon name="refresh" className="text-base"/>
                        {t('edit.regenerateConfirmAction')}
                    </button>
                </div>
            </div>
        </div>
    );
};

export const RetranscribeConfirmDialog = ({canRetranscribe, sourceLabel, sourceName, onCancel, onConfirm}) => {
    const {t} = useI18n();
    const dialogRef = useDismissable(true, onCancel, modalOptions);
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="retranscribe-confirm-title" className={`w-full max-w-lg overflow-hidden ${floatingLayer}`}>
                <div className="flex items-start gap-4 border-b border-[#e4e0e0] px-6 py-5 dark:border-white/[0.12]">
                    <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[12px] bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white">
                        <SvgIcon name="record_voice_over" className=""/>
                    </div>
                    <div className="min-w-0">
                        <h2 id="retranscribe-confirm-title" className="font-headline text-[16px] font-extrabold text-[#111111] dark:text-white">
                            {canRetranscribe ? t('edit.retranscribeConfirmTitle') : t('edit.retranscribeUnavailableTitle')}
                        </h2>
                        <p className="mt-2 text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">
                            {canRetranscribe ? t('edit.retranscribeConfirmDesc') : t('edit.retranscribeUnavailableDesc')}
                        </p>
                        <div className="mt-4 rounded-[12px] border border-[#e4e0e0] bg-[#f8f7fb] p-3 dark:border-white/[0.12] dark:bg-white/[0.06]">
                            <p className="mb-1 text-[11px] font-bold uppercase tracking-wider text-[#777] dark:text-white/45">{sourceLabel}</p>
                            <p className="truncate text-sm font-bold text-[#111111] dark:text-white">{sourceName}</p>
                        </div>
                    </div>
                </div>
                <div className="flex flex-col-reverse gap-3 px-6 py-4 sm:flex-row sm:justify-end">
                    <button
                        type="button"
                        onClick={onCancel}
                        className={button('secondary', 'md')}
                    >
                        {t('edit.cancel')}
                    </button>
                    <button
                        type="button"
                        onClick={onConfirm}
                        className={button('primary', 'md')}
                    >
                        <SvgIcon name={canRetranscribe ? 'sync' : 'upload_file'} className="text-base"/>
                        {canRetranscribe ? t('edit.retranscribeConfirmAction') : t('edit.retranscribeChooseAction')}
                    </button>
                </div>
            </div>
        </div>
    );
};

export const EditRecordsDialog = ({records, onClose, onSeek}) => {
    const {t} = useI18n();
    const dialogRef = useDismissable(true, onClose, modalOptions);
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="edit-records-title" className={`flex max-h-[82vh] w-full max-w-3xl flex-col overflow-hidden ${floatingLayer}`}>
                <div className="flex items-start justify-between gap-4 border-b border-[#e4e0e0] px-6 py-5 dark:border-white/[0.12]">
                    <div className="min-w-0">
                        <h2 id="edit-records-title" className="flex items-center gap-2 font-headline text-[16px] font-extrabold text-[#111111] dark:text-white">
                            <SvgIcon name="edit_note" className="text-primary"/>
                            {t('edit.editRecordsTitle')}
                            <span className="rounded-full bg-[#eef2ff] px-2 py-0.5 text-xs font-bold text-primary dark:bg-white/[0.08] dark:text-white">{records.length}</span>
                        </h2>
                        <p className="mt-2 text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">{t('edit.editRecordsDesc')}</p>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className={iconButton('quiet', 'md')}
                    >
                        <SvgIcon name="close" className="text-lg"/>
                    </button>
                </div>
                <div className="flex-1 space-y-4 overflow-y-auto p-5">
                    {records.length === 0 ? (
                        <div className="rounded-[12px] bg-[#f8f7fb] px-5 py-8 text-center text-sm font-medium text-[#666] dark:bg-white/[0.06] dark:text-white/60">
                            {t('edit.editRecordsEmpty')}
                        </div>
                    ) : records.map((record, idx) => (
                        <article key={`${record.index}-${record.start}-${idx}`} className="overflow-hidden rounded-[12px] border border-[#e4e0e0] bg-white dark:border-white/[0.12] dark:bg-white/[0.04]">
                            <div className="flex items-center gap-3 bg-[#f8f7fb] px-4 py-3 dark:bg-white/[0.04]">
                                <button
                                    type="button"
                                    onClick={()=>onSeek(record)}
                                    className={timestampButton(true)}
                                >
                                    {fmtTime(record.start || 0)}
                                </button>
                                <span className="text-xs font-semibold text-[#777] dark:text-white/45">#{record.index + 1}</span>
                            </div>
                            <div className="space-y-3 p-4">
                                <div className="grid gap-3 md:grid-cols-2">
                                    <div className="rounded-[12px] border border-red-500/10 bg-red-50/70 p-3 dark:bg-red-500/10">
                                        <p className="mb-1 text-[11px] font-bold text-red-600 dark:text-red-300">{t('edit.before')}</p>
                                        <p className="whitespace-pre-wrap text-sm leading-relaxed text-[#111111] dark:text-white">{record.before}</p>
                                    </div>
                                    <div className="rounded-[12px] border border-green-500/10 bg-green-50/80 p-3 dark:bg-green-500/10">
                                        <p className="mb-1 text-[11px] font-bold text-green-700 dark:text-green-300">{t('edit.after')}</p>
                                        <p className="whitespace-pre-wrap text-sm leading-relaxed text-[#111111] dark:text-white">{record.after}</p>
                                    </div>
                                </div>
                                <div className="grid gap-3 text-xs text-[#666] dark:text-white/60 md:grid-cols-2">
                                    <div className="rounded-[12px] bg-[#f8f7fb] p-3 dark:bg-white/[0.06]">
                                        <p className="mb-1 font-bold text-[#111111] dark:text-white">{t('edit.previousSentence')}</p>
                                        <p className="whitespace-pre-wrap leading-relaxed">{record.previous_before || record.previous_after || '-'}</p>
                                    </div>
                                    <div className="rounded-[12px] bg-[#f8f7fb] p-3 dark:bg-white/[0.06]">
                                        <p className="mb-1 font-bold text-[#111111] dark:text-white">{t('edit.nextSentence')}</p>
                                        <p className="whitespace-pre-wrap leading-relaxed">{record.next_before || record.next_after || '-'}</p>
                                    </div>
                                </div>
                            </div>
                        </article>
                    ))}
                </div>
            </div>
        </div>
    );
};

// The cut's processing report for this task, opened from the editor so the
// places waiting for a judgement can be answered without leaving the result.
export const CutReviewDialog = ({result, onClose}) => {
    const {lang} = useI18n();
    const dialogRef = useDismissable(true, onClose, modalOptions);
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="cut-review-title" className={`flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden ${floatingLayer}`}>
                <div className="flex items-center justify-between gap-4 border-b border-[#e4e0e0] px-6 py-4 dark:border-white/[0.12]">
                    <h2 id="cut-review-title" className="font-headline text-[16px] font-extrabold text-[#111111] dark:text-white">
                        {lang === 'zh' ? '剪辑处理说明' : 'What the cut did'}
                    </h2>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label={lang === 'zh' ? '关闭' : 'Close'}
                        className={iconButton('quiet', 'md')}
                    >
                        <SvgIcon name="close" className="text-lg"/>
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-4">
                    <CutReport job={{task_id: result?.task_id, result}} embedded/>
                </div>
            </div>
        </div>
    );
};
