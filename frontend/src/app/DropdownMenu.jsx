import { useState, useRef, useEffect } from 'react';
import {Download, ExternalLink, FileText, Trash2} from 'lucide-react';

const dropdownIconMap = {
    download: Download,
    open_in_new: ExternalLink,
    description: FileText,
    delete: Trash2,
};

export const DropdownMenu = ({trigger, items, align='right'}) => {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    useEffect(() => {
        const handler = (e) => { if(ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);
    return (
        <div className="relative" ref={ref}>
            <div onClick={()=>setOpen(!open)}>{trigger}</div>
            {open && (
                <div className={`absolute top-full mt-1 ${align==='right'?'right-0':'left-0'} z-50 bg-white rounded-lg shadow-xl border border-slate-200 py-1 min-w-[180px] animate-[fadeIn_0.15s_ease-out]`}>
                    {items.map((it,i) => it.divider ? (
                        <div key={i} className="border-t border-slate-100 my-1"/>
                    ) : (
                        <button key={i} onClick={()=>{setOpen(false); it.onClick?.();}} disabled={it.disabled} className="w-full text-left px-4 py-2.5 text-sm hover:bg-slate-50 flex items-center gap-3 text-on-surface disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            {it.icon && (() => {
                                const Icon = dropdownIconMap[it.icon] || FileText;
                                return <Icon className="size-4 text-slate-400" strokeWidth={2.15}/>;
                            })()}
                            <span className="flex-1">{it.label}</span>
                            {it.badge && <span className="text-[10px] text-slate-400 font-medium">{it.badge}</span>}
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
};
