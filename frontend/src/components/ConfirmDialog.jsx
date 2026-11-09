// The product's own confirmation dialog, in place of the browser's
// window.confirm. That one is drawn by the browser at the top of the window,
// titled with the page's address ("127.0.0.1:8000 says"), says OK instead of
// the action, and looks like no part of FluentFlow (user report, 2026-11-09).
//
//   const [confirm, confirmDialog] = useConfirm();
//   ...
//   if (!(await confirm({title, message, confirmLabel, tone: 'danger'}))) return;
//   ...
//   return <>{...}{confirmDialog}</>;
//
// The dialog is centred over a dimmed page. Escape, a click on the dimmed
// page and the cancel button all answer false; focus starts on the cancel
// button, so an Enter pressed out of habit does not delete anything.
//
// Each component that asks owns its dialog (the hook returns the element to
// render), so a component works without any provider around it, in the app
// and in a test alike.
import {useCallback, useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import SvgIcon from './SvgIcon.jsx';
import {useI18n} from '../app/shared.jsx';
import {useDismissable} from '../lib/useDismissable.js';
import {button, floatingLayer} from '../lib/ui.js';

const TONES = {
    // An action that cannot be taken back: delete, remove, stop a running task.
    danger: {
        icon: 'warning',
        iconClass: 'bg-[#fef2f2] text-[#dc2626] dark:bg-red-500/15 dark:text-red-300',
        confirmTone: 'destructive',
    },
    // An ordinary go-ahead: start a batch that spends a model allowance.
    primary: {
        icon: 'info',
        iconClass: 'bg-[#eef2ff] text-primary dark:bg-white/[0.08] dark:text-white',
        confirmTone: 'primary',
    },
};

export const ConfirmDialog = ({title, message, confirmLabel, cancelLabel, tone = 'danger', onConfirm, onCancel}) => {
    const {t} = useI18n();
    const look = TONES[tone] || TONES.danger;
    const dialogRef = useDismissable(true, onCancel, {closeOnOutsideClick: true, trapFocus: true});
    return (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
            <div
                ref={dialogRef}
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="confirm-dialog-title"
                aria-describedby={message ? 'confirm-dialog-message' : undefined}
                className={`w-full max-w-md overflow-hidden ${floatingLayer}`}
            >
                <div className="flex items-start gap-4 px-6 pb-4 pt-5">
                    <div className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[12px] ${look.iconClass}`}>
                        <SvgIcon name={look.icon} className="text-lg"/>
                    </div>
                    <div className="min-w-0 pt-0.5">
                        <h2 id="confirm-dialog-title" className="font-headline text-[16px] font-extrabold text-[#111111] dark:text-white">
                            {title}
                        </h2>
                        {message && (
                            <p id="confirm-dialog-message" className="mt-2 whitespace-pre-line text-sm font-medium leading-relaxed text-[#666] dark:text-white/60">
                                {message}
                            </p>
                        )}
                    </div>
                </div>
                <div className="flex flex-col-reverse gap-3 px-6 pb-5 pt-1 sm:flex-row sm:justify-end">
                    <button type="button" data-testid="confirm-dialog-cancel" onClick={onCancel} className={button('secondary', 'md')}>
                        {cancelLabel || t('confirm.cancel')}
                    </button>
                    <button type="button" data-testid="confirm-dialog-confirm" onClick={onConfirm} className={button(look.confirmTone, 'md')}>
                        {confirmLabel}
                    </button>
                </div>
            </div>
        </div>
    );
};

export const useConfirm = () => {
    const [request, setRequest] = useState(null);
    const resolveRef = useRef(null);

    const settle = useCallback((value) => {
        const resolve = resolveRef.current;
        resolveRef.current = null;
        setRequest(null);
        resolve?.(value);
    }, []);

    const confirm = useCallback((options) => new Promise((resolve) => {
        // A second question while one is open answers the first with "no".
        resolveRef.current?.(false);
        resolveRef.current = resolve;
        setRequest(options || {});
    }), []);

    // A component that goes away mid-question has its question answered "no",
    // so the caller's await does not hang.
    useEffect(() => () => {
        resolveRef.current?.(false);
        resolveRef.current = null;
    }, []);

    const dialog = request && typeof document !== 'undefined'
        ? createPortal(
            <ConfirmDialog
                {...request}
                onConfirm={() => settle(true)}
                onCancel={() => settle(false)}
            />,
            document.body,
        )
        : null;

    return [confirm, dialog];
};
