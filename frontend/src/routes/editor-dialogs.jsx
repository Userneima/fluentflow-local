// Presentational dialog/modal components extracted from editor.jsx.
// Each component renders the modal body only; the caller keeps the open-state
// toggle ({flag && <Dialog .../>}) so state stays in the Editor component.
import SvgIcon from '../components/SvgIcon.jsx';
import {fmtTime, useI18n} from '../app/shared.jsx';

export const FeishuExportPrompt = ({onCancel, onConnect, connecting, reconnect = false}) => {
    const {t, lang} = useI18n();
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="feishuExportPromptTitle">
            <div className="w-full max-w-lg overflow-hidden rounded-[24px] border border-[#e4e0e0] bg-white shadow-[0_24px_70px_-35px_rgba(17,17,17,.65)] dark:border-white/[0.12] dark:bg-[#151515]">
                <div className="flex items-start gap-4 border-b border-[#e4e0e0] px-6 py-5 dark:border-white/[0.12]">
                    <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[14px] bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white">
                        <SvgIcon name="cloud_done" className=""/>
                    </div>
                    <div className="min-w-0">
                        <h2 id="feishuExportPromptTitle" className="font-headline text-xl font-extrabold text-[#111111] dark:text-white">
                            {reconnect
                                ? (lang === 'zh' ? '重新连接飞书账号' : 'Reconnect Feishu')
                                : (lang === 'zh' ? '先连接飞书账号' : 'Connect Feishu first')}
                        </h2>
                        <p className="mt-2 text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">
                            {reconnect
                                ? (lang === 'zh'
                                    ? '飞书应用已更新导出权限。重新确认一次授权后，之后导出仍会直接写入你的飞书空间。'
                                    : 'Feishu export permissions were updated. Approve once more, then future exports will continue going straight to your space.')
                                : (lang === 'zh'
                                ? '当前导出路线会写入你自己的飞书空间。连接一次后，之后导出不需要填写 App ID、Secret 或 token。'
                                : 'This export route writes to your own Feishu space. Connect once; future exports do not ask for app IDs, secrets, or tokens.')}
                        </p>
                        <div className="mt-4 rounded-[16px] border border-[#e4e0e0] bg-[#f8f7fb] p-3 text-xs font-semibold leading-relaxed text-on-surface-variant dark:border-white/[0.12] dark:bg-white/[0.06]">
                            {reconnect
                                ? (lang === 'zh'
                                    ? '确认后会跳转到飞书；完成授权后回到这里，再点击“导出到飞书”。'
                                    : 'You will be sent to Feishu. After approval, return here and select Export to Lark again.')
                                : (lang === 'zh'
                                ? '连接完成后回到编辑器，再点击“导出到飞书”。'
                                : 'After connecting, return to the editor and click Export to Lark again.')}
                        </div>
                    </div>
                </div>
                <div className="flex flex-col-reverse gap-3 px-6 py-4 sm:flex-row sm:justify-end">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="rounded-[13px] bg-[#efeeee] px-4 py-2 text-sm font-bold text-[#111111] transition hover:bg-[#e4e0e0] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]"
                    >
                        {t('edit.cancel')}
                    </button>
                    <button
                        type="button"
                        onClick={onConnect}
                        disabled={connecting}
                        className="inline-flex items-center justify-center gap-2 rounded-[13px] bg-[#111111] px-4 py-2 text-sm font-bold text-white transition hover:bg-[#2a2a2a] disabled:opacity-40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/85"
                    >
                        <SvgIcon name={connecting ? 'sync' : 'cloud_done'} className={`text-base ${connecting ? 'animate-spin' : ''}`}/>
                        {reconnect
                            ? (lang === 'zh' ? '重新连接飞书' : 'Reconnect Feishu')
                            : (lang === 'zh' ? '连接飞书' : 'Connect Feishu')}
                    </button>
                </div>
            </div>
        </div>
    );
};

export const RegenerateConfirmDialog = ({transcriptTitle, onCancel, onConfirm}) => {
    const {t, lang} = useI18n();
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div className="w-full max-w-lg overflow-hidden rounded-[24px] border border-[#e4e0e0] bg-white shadow-[0_24px_70px_-35px_rgba(17,17,17,.65)] dark:border-white/[0.12] dark:bg-[#151515]">
                <div className="flex items-start gap-4 border-b border-[#e4e0e0] px-6 py-5 dark:border-white/[0.12]">
                    <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[14px] bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white">
                        <SvgIcon name="refresh" className=""/>
                    </div>
                    <div className="min-w-0">
                        <h2 className="font-headline text-xl font-extrabold text-[#111111] dark:text-white">
                            {t('edit.regenerateConfirmTitle')}
                        </h2>
                        <p className="mt-2 text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">
                            {t('edit.regenerateConfirmDesc')}
                        </p>
                        <div className="mt-4 rounded-[16px] border border-[#e4e0e0] bg-[#f8f7fb] p-3 dark:border-white/[0.12] dark:bg-white/[0.06]">
                            <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-[#777] dark:text-white/45">{lang === 'zh' ? '当前转录' : 'Current transcript'}</p>
                            <p className="truncate text-sm font-bold text-[#111111] dark:text-white">{transcriptTitle}</p>
                        </div>
                    </div>
                </div>
                <div className="flex flex-col-reverse gap-3 px-6 py-4 sm:flex-row sm:justify-end">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="rounded-[13px] bg-[#efeeee] px-4 py-2 text-sm font-bold text-[#111111] transition hover:bg-[#e4e0e0] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]"
                    >
                        {t('edit.cancel')}
                    </button>
                    <button
                        type="button"
                        onClick={onConfirm}
                        className="inline-flex items-center justify-center gap-2 rounded-[13px] bg-[#111111] px-4 py-2 text-sm font-bold text-white transition hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85"
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
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div className="w-full max-w-lg overflow-hidden rounded-[24px] border border-[#e4e0e0] bg-white shadow-[0_24px_70px_-35px_rgba(17,17,17,.65)] dark:border-white/[0.12] dark:bg-[#151515]">
                <div className="flex items-start gap-4 border-b border-[#e4e0e0] px-6 py-5 dark:border-white/[0.12]">
                    <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[14px] bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white">
                        <SvgIcon name="record_voice_over" className=""/>
                    </div>
                    <div className="min-w-0">
                        <h2 className="font-headline text-xl font-extrabold text-[#111111] dark:text-white">
                            {canRetranscribe ? t('edit.retranscribeConfirmTitle') : t('edit.retranscribeUnavailableTitle')}
                        </h2>
                        <p className="mt-2 text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">
                            {canRetranscribe ? t('edit.retranscribeConfirmDesc') : t('edit.retranscribeUnavailableDesc')}
                        </p>
                        <div className="mt-4 rounded-[16px] border border-[#e4e0e0] bg-[#f8f7fb] p-3 dark:border-white/[0.12] dark:bg-white/[0.06]">
                            <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-[#777] dark:text-white/45">{sourceLabel}</p>
                            <p className="truncate text-sm font-bold text-[#111111] dark:text-white">{sourceName}</p>
                        </div>
                    </div>
                </div>
                <div className="flex flex-col-reverse gap-3 px-6 py-4 sm:flex-row sm:justify-end">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="rounded-[13px] bg-[#efeeee] px-4 py-2 text-sm font-bold text-[#111111] transition hover:bg-[#e4e0e0] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]"
                    >
                        {t('edit.cancel')}
                    </button>
                    <button
                        type="button"
                        onClick={onConfirm}
                        className="inline-flex items-center justify-center gap-2 rounded-[13px] bg-[#111111] px-4 py-2 text-sm font-bold text-white transition hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85"
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
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div className="flex max-h-[82vh] w-full max-w-3xl flex-col overflow-hidden rounded-[24px] border border-[#e4e0e0] bg-white shadow-[0_24px_70px_-35px_rgba(17,17,17,.65)] dark:border-white/[0.12] dark:bg-[#151515]">
                <div className="flex items-start justify-between gap-4 border-b border-[#e4e0e0] px-6 py-5 dark:border-white/[0.12]">
                    <div className="min-w-0">
                        <h2 className="flex items-center gap-2 font-headline text-xl font-extrabold text-[#111111] dark:text-white">
                            <SvgIcon name="edit_note" className="text-primary"/>
                            {t('edit.editRecordsTitle')}
                            <span className="rounded-full bg-[#eef2ff] px-2 py-0.5 text-xs font-bold text-primary dark:bg-white/[0.08] dark:text-white">{records.length}</span>
                        </h2>
                        <p className="mt-2 text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">{t('edit.editRecordsDesc')}</p>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="flex h-9 w-9 items-center justify-center rounded-[13px] bg-[#efeeee] text-[#666] transition hover:bg-[#e4e0e0] hover:text-[#111111] dark:bg-white/[0.08] dark:text-white/60 dark:hover:bg-white/[0.12] dark:hover:text-white"
                    >
                        <SvgIcon name="close" className="text-lg"/>
                    </button>
                </div>
                <div className="flex-1 space-y-4 overflow-y-auto p-5">
                    {records.length === 0 ? (
                        <div className="rounded-[18px] bg-[#f8f7fb] px-5 py-8 text-center text-sm font-medium text-[#666] dark:bg-white/[0.06] dark:text-white/60">
                            {t('edit.editRecordsEmpty')}
                        </div>
                    ) : records.map((record, idx) => (
                        <article key={`${record.index}-${record.start}-${idx}`} className="overflow-hidden rounded-[18px] border border-[#e4e0e0] bg-white dark:border-white/[0.12] dark:bg-white/[0.04]">
                            <div className="flex items-center gap-3 bg-[#f8f7fb] px-4 py-3 dark:bg-white/[0.04]">
                                <button
                                    type="button"
                                    onClick={()=>onSeek(record)}
                                    className="font-mono text-xs font-bold text-primary hover:underline"
                                >
                                    {fmtTime(record.start || 0)}
                                </button>
                                <span className="text-xs font-semibold text-[#777] dark:text-white/45">#{record.index + 1}</span>
                            </div>
                            <div className="space-y-3 p-4">
                                <div className="grid gap-3 md:grid-cols-2">
                                    <div className="rounded-[14px] border border-red-500/10 bg-red-50/70 p-3 dark:bg-red-500/10">
                                        <p className="mb-1 text-[10px] font-bold text-red-600 dark:text-red-300">{t('edit.before')}</p>
                                        <p className="whitespace-pre-wrap text-sm leading-relaxed text-[#111111] dark:text-white">{record.before}</p>
                                    </div>
                                    <div className="rounded-[14px] border border-green-500/10 bg-green-50/80 p-3 dark:bg-green-500/10">
                                        <p className="mb-1 text-[10px] font-bold text-green-700 dark:text-green-300">{t('edit.after')}</p>
                                        <p className="whitespace-pre-wrap text-sm leading-relaxed text-[#111111] dark:text-white">{record.after}</p>
                                    </div>
                                </div>
                                <div className="grid gap-3 text-xs text-[#666] dark:text-white/60 md:grid-cols-2">
                                    <div className="rounded-[14px] bg-[#f8f7fb] p-3 dark:bg-white/[0.06]">
                                        <p className="mb-1 font-bold text-[#111111] dark:text-white">{t('edit.previousSentence')}</p>
                                        <p className="whitespace-pre-wrap leading-relaxed">{record.previous_before || record.previous_after || '-'}</p>
                                    </div>
                                    <div className="rounded-[14px] bg-[#f8f7fb] p-3 dark:bg-white/[0.06]">
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
