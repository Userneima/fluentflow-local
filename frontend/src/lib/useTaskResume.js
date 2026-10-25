import {useCallback, useEffect, useRef, useState} from 'react';
import {resumeOffer} from './taskState.js';

// The editor's view of whether its task stopped or finished with a step that
// failed, and the one action that carries it on (backend/core/task_actions.py).
// Reads the task's activity when the task changes, and again every few seconds
// while a step is still working, so the offer appears once it has stopped.
// The callers' functions are read through a ref: a page that passes new ones
// on every render must not read the task again on every render.
const POLL_MS = 5000;

export const useTaskResume = ({taskId, getJobActivity, resumeJob, jobOptions, lang, onResumed, onError}) => {
    const [job, setJob] = useState(null);
    const [resuming, setResuming] = useState(false);
    const deps = useRef({});
    deps.current = {getJobActivity, resumeJob, jobOptions, onResumed, onError};
    const latest = useRef(taskId);
    latest.current = taskId;

    const read = useCallback(async () => {
        if (!taskId || typeof deps.current.getJobActivity !== 'function') return null;
        try {
            const fresh = await deps.current.getJobActivity(taskId, deps.current.jobOptions);
            if (latest.current === taskId) setJob(fresh || null);
            return fresh;
        } catch {
            return null;
        }
    }, [taskId]);

    useEffect(() => {
        setJob(null);
        read();
    }, [read]);

    const busy = Boolean(job?.activity?.busy);
    useEffect(() => {
        if (!busy) return undefined;
        const timer = setInterval(read, POLL_MS);
        return () => clearInterval(timer);
    }, [busy, read]);

    const offer = job && job.task_id === taskId ? resumeOffer(job, lang) : null;

    const resume = useCallback(async () => {
        if (!taskId || resuming) return;
        setResuming(true);
        try {
            await deps.current.resumeJob(taskId, deps.current.jobOptions);
            await read();
            deps.current.onResumed?.();
        } catch (err) {
            deps.current.onError?.(err);
        } finally {
            setResuming(false);
        }
    }, [taskId, resuming, read]);

    return {offer, resume, resuming};
};
