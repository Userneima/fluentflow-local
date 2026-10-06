import {cloneElement, isValidElement, useRef, useState} from 'react';
import {Download, ExternalLink, FileText, Trash2} from 'lucide-react';
import {useDismissable} from '../lib/useDismissable.js';

const dropdownIconMap = {
    download: Download,
    open_in_new: ExternalLink,
    description: FileText,
    delete: Trash2,
};

// The trigger is a real <button> so the menu opens from the keyboard and
// announces itself. A trigger that already is a <button> is reused with the
// menu attributes added (its own `disabled` and classes stay); anything else
// is wrapped in one.
export const DropdownMenu = ({trigger, items, align='right'}) => {
    const [open, setOpen] = useState(false);
    const close = () => setOpen(false);
    const wrapperRef = useRef(null);
    const menuRef = useDismissable(open, close, {closeOnOutsideClick: true, outsideOf: wrapperRef});
    const toggle = () => setOpen((value) => !value);
    const menuAttrs = {'aria-haspopup': 'menu', 'aria-expanded': open};
    const triggerNode = isValidElement(trigger) && trigger.type === 'button'
        ? cloneElement(trigger, {
            type: trigger.props.type || 'button',
            ...menuAttrs,
            onClick: (event) => { trigger.props.onClick?.(event); if (!event.defaultPrevented) toggle(); },
        })
        : (
            <button type="button" onClick={toggle} {...menuAttrs} className="block appearance-none border-0 bg-transparent p-0 text-left">
                {trigger}
            </button>
        );
    return (
        <div className="relative" ref={wrapperRef}>
            {triggerNode}
            {open && (
                <div ref={menuRef} role="menu" className={`absolute top-full mt-1 ${align==='right'?'right-0':'left-0'} z-50 bg-white rounded-lg shadow-xl border border-slate-200 py-1 min-w-[180px] animate-[fadeIn_0.15s_ease-out]`}>
                    {items.map((it,i) => it.divider ? (
                        <div key={i} role="separator" className="border-t border-slate-100 my-1"/>
                    ) : (
                        <button key={i} type="button" role="menuitem" onClick={()=>{close(); it.onClick?.();}} disabled={it.disabled} className="w-full text-left px-4 py-2.5 text-sm hover:bg-slate-50 flex items-center gap-3 text-on-surface disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
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
