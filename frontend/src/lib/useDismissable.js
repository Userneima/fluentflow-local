import {useEffect, useRef} from 'react';

// The keyboard and pointer contract every popup shares: Escape closes it, a
// click outside closes it, focus moves into it when it opens and goes back to
// whatever had it when it closes. Menus and dialogs used to implement one or
// two of these each, and none of them returned focus.
//
//   open     — whether the popup is showing
//   onClose  — called to close it
//   options.closeOnOutsideClick (default true) — a pointer press outside
//              the popup closes it; menus want this, modal dialogs with a
//              backdrop usually handle it themselves.
//   options.outsideOf — a ref to the element that counts as "inside" for the
//              outside-click test when it is wider than the popup (a menu's
//              wrapper that also holds its trigger).
//   options.trapFocus (default false) — Tab cycles inside the popup, for
//              role="dialog" surfaces.
//
// Returns the ref to put on the popup's root element.
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export const useDismissable = (open, onClose, options = {}) => {
    const {closeOnOutsideClick = true, trapFocus = false, outsideOf = null} = options;
    const containerRef = useRef(null);
    const onCloseRef = useRef(onClose);
    onCloseRef.current = onClose;

    useEffect(() => {
        if (!open) return undefined;
        const container = containerRef.current;
        const previouslyFocused = document.activeElement;
        // Move focus in. The first focusable control, or the container itself
        // (made focusable so Escape is heard even when there is no control).
        if (container) {
            const first = container.querySelector(FOCUSABLE);
            if (first) first.focus({preventScroll: true});
            else {
                if (!container.hasAttribute('tabindex')) container.setAttribute('tabindex', '-1');
                container.focus({preventScroll: true});
            }
        }
        const onKeyDown = (event) => {
            // A popup nested in another (a submenu) hears the key first through
            // its own element and marks it handled, so the outer one leaves it.
            if (event.defaultPrevented) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                onCloseRef.current?.();
                return;
            }
            if (!trapFocus || event.key !== 'Tab' || !containerRef.current) return;
            const items = Array.from(containerRef.current.querySelectorAll(FOCUSABLE))
                .filter((node) => node.offsetParent !== null || node === document.activeElement);
            if (!items.length) {
                event.preventDefault();
                return;
            }
            const firstItem = items[0];
            const lastItem = items[items.length - 1];
            if (event.shiftKey && document.activeElement === firstItem) {
                event.preventDefault();
                lastItem.focus();
            } else if (!event.shiftKey && document.activeElement === lastItem) {
                event.preventDefault();
                firstItem.focus();
            }
        };
        const onPointerDown = (event) => {
            if (!closeOnOutsideClick) return;
            const node = outsideOf?.current || containerRef.current;
            if (node && !node.contains(event.target)) onCloseRef.current?.();
        };
        container?.addEventListener('keydown', onKeyDown);
        document.addEventListener('keydown', onKeyDown);
        document.addEventListener('mousedown', onPointerDown);
        return () => {
            container?.removeEventListener('keydown', onKeyDown);
            document.removeEventListener('keydown', onKeyDown);
            document.removeEventListener('mousedown', onPointerDown);
            // Give focus back to the trigger, unless the page moved it elsewhere
            // in the meantime (a route change, for example). `container` is the
            // node captured on open; by now React may have detached it.
            const stillInside = !!container && container.contains(document.activeElement);
            if (previouslyFocused && typeof previouslyFocused.focus === 'function'
                && (stillInside || document.activeElement === document.body)
                && document.contains(previouslyFocused)) {
                previouslyFocused.focus({preventScroll: true});
            }
        };
    }, [open, closeOnOutsideClick, trapFocus, outsideOf]);

    return containerRef;
};
