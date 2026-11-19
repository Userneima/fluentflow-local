// The records page's two blocks outside the list: the upload batch still being
// received (above the list) and the Feishu documents this machine made (below).
import {useState} from 'react';
import {ChevronDown, LoaderCircle, XCircle} from 'lucide-react';
import {fmtFileSize} from '../app/shared.jsx';
import {button, chip, linkButton} from '../lib/ui.js';

export const QueueUploadBanner = ({upload, lang, onCancel}) => {
    if (!upload?.queueUpload || upload?.queueSubmitted) return null;
    const count = Number(upload.queueTotal || 0) || 1;
    const progress = Math.max(0, Math.min(100, Number(upload.progress) || 2));
    const totalSize = upload.fileSizeMb ? fmtFileSize(upload.fileSizeMb) : '';
    return (
        <section className="rounded-[16px] border border-blue-200 bg-blue-50/80 p-4 dark:border-blue-400/20 dark:bg-blue-400/10">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                    <p className="inline-flex items-center gap-2 text-[12px] font-extrabold text-blue-700 dark:text-blue-200">
                        <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/>
                        {lang === 'zh' ? '正在接收上传批次' : 'Receiving upload batch'}
                    </p>
                    <h2 className="mt-2 font-headline text-[15px] font-extrabold text-[#111111] dark:text-white">
                        {lang === 'zh' ? `${count} 个文件正在上传` : `${count} files are uploading`}
                    </h2>
                    <p className="mt-1 text-[13px] font-semibold leading-5 text-[#57585d] dark:text-white/65">
                        {lang === 'zh'
                            ? '每个文件的进程卡已显示在下方，上传提交完成后会切换为真实任务记录。'
                            : 'Each file is shown below; after upload is submitted, the cards switch to real task records.'}
                    </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {totalSize ? (
                        <span className="rounded-full border border-blue-200 bg-white/70 px-3 py-1 text-[12px] font-extrabold text-blue-700 dark:border-blue-400/20 dark:bg-white/[0.08] dark:text-blue-100">
                            {totalSize}
                        </span>
                    ) : null}
                    <span className="rounded-full border border-blue-200 bg-white/70 px-3 py-1 text-[12px] font-extrabold tabular-nums text-blue-700 dark:border-blue-400/20 dark:bg-white/[0.08] dark:text-blue-100">
                        {progress}%
                    </span>
                    <button type="button" onClick={onCancel} className={button('danger', 'sm')}>
                        <XCircle className="size-3.5" strokeWidth={2.15}/>
                        {lang === 'zh' ? '取消上传' : 'Cancel upload'}
                    </button>
                </div>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-blue-100 dark:bg-white/[0.12]">
                <div className="h-full rounded-full bg-blue-700 transition-all duration-500 dark:bg-blue-200" style={{width: `${progress}%`}}/>
            </div>
        </section>
    );
};

// The Feishu documents this machine has made (automatic exports included),
// newest task first. No time beside them: no task records when it was
// exported, and its last change is not that (Codex push review). Moved here from the settings page, where history did not
// belong (decision 6.21); folded, since it is looked at now and then.
export const LarkExportList = ({exports, lang}) => {
    const [open, setOpen] = useState(false);
    if (!exports?.length) return null;
    const zh = lang === 'zh';
    return (
        <section data-testid="lark-export-list" className="space-y-2">
            <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)} className={chip(open)}>
                {zh ? '飞书导出记录' : 'Feishu exports'}
                <span className="tabular-nums opacity-70">{exports.length}</span>
                <ChevronDown className={`size-3.5 transition ${open ? 'rotate-180' : ''}`} strokeWidth={2.15}/>
            </button>
            {open ? (
                <ul className="space-y-1">
                    {exports.map((item) => (
                        <li key={item.url} className="text-[13px] font-semibold">
                            <a href={item.url} target="_blank" rel="noopener noreferrer" className={`block min-w-0 truncate ${linkButton('blue')}`}>
                                {item.title}
                            </a>
                        </li>
                    ))}
                </ul>
            ) : null}
        </section>
    );
};
