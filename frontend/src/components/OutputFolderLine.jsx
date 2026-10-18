import {useState} from 'react';
import SvgIcon from './SvgIcon.jsx';
import {useApi} from '../app/shared.jsx';

// Where this task's outputs went, and a way there. A link video's folder sits
// under the Videos folder and a recording's beside it, but the page only said
// "download", which read as "it is only in the app" (owner, 2026-10-18).
export const outputFolderPath = (result) => {
    const folder = result?.output_folder;
    return folder && typeof folder === 'object' ? String(folder.path || '').trim() : '';
};

export default function OutputFolderLine({taskId, result, lang = 'zh', className = ''}) {
    const zh = lang === 'zh';
    const {openOutputFolder} = useApi();
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const path = outputFolderPath(result);
    if (!path || !taskId) return null;

    const open = async (event) => {
        event.stopPropagation();
        setBusy(true);
        setError('');
        try {
            await openOutputFolder(taskId);
        } catch (failure) {
            setError(String(failure?.message || (zh ? '没能打开文件夹' : 'Could not open the folder')));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div data-testid="output-folder-line" className={`flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs ${className}`}>
            <SvgIcon name="folder_open" className="shrink-0 text-sm text-[#888] dark:text-white/50"/>
            <span className="shrink-0 font-semibold text-[#666] dark:text-white/60">{zh ? '产出在' : 'Outputs in'}</span>
            <span className="min-w-0 select-all truncate font-semibold text-[#111111] dark:text-white" title={path}>{path}</span>
            <button
                type="button"
                onClick={open}
                disabled={busy}
                className="inline-flex h-7 shrink-0 items-center gap-1 rounded-[10px] border border-[#e4e0e0] bg-white px-2.5 text-[11px] font-bold text-[#111111] transition hover:bg-[#efeeee] disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.1]"
            >
                {zh ? '打开文件夹' : 'Open folder'}
            </button>
            {error && <span className="basis-full text-[11px] font-semibold text-error">{error}</span>}
        </div>
    );
}
