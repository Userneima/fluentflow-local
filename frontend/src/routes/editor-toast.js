// The editor page's toast, and the timers that must die with the page: a
// toast closing itself, the "done" card clearing itself (useRetranscribe).
// A toast shown twice in a row used to be closed by the first one's timer.
import {useCallback, useEffect, useRef, useState} from 'react';

export const useEditorToast = () => {
    const [toast, setToast] = useState(null);
    const toastTimerRef = useRef(null);
    const doneJobTimerRef = useRef(null);
    useEffect(() => () => {
        if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
        if (doneJobTimerRef.current) clearTimeout(doneJobTimerRef.current);
    }, []);

    const showToast = useCallback((msg, ok=true) => {
        setToast({msg, ok});
        if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
        toastTimerRef.current = setTimeout(() => {
            toastTimerRef.current = null;
            setToast(null);
        }, 3000);
    }, []);

    return {toast, showToast, doneJobTimerRef};
};
