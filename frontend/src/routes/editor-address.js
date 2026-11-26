// The editor's address names its task (?task=<id>), so a refresh, the back
// button or a bookmark reopens the same result. ?review=cut also opens the
// cut's processing report, for the records page's "有 N 处需要你判断"
// (useCutReview). This hook opens the task the address names and writes the
// open result back into the address.
import {useEffect, useRef, useState} from 'react';
import {useSearchParams} from 'react-router-dom';
import {markBackendJob} from '../lib/taskState.js';

export const useEditorAddress = ({lastResult, getJob, setLastResult, setNoteReadFor, ingestJobs}) => {
    const [searchParams, setSearchParams] = useSearchParams();
    const urlTaskId = searchParams.get('task') || '';
    const urlWantsCutReview = searchParams.get('review') === 'cut';
    const [urlTask, setUrlTask] = useState({id: '', state: 'idle'});
    const urlTaskIdRef = useRef(urlTaskId);
    urlTaskIdRef.current = urlTaskId;
    useEffect(() => {
        if (!urlTaskId) return;
        // The address and the open result agree: nothing to open, and a later
        // switch to another result moves the address instead of reopening this.
        if (lastResult?.task_id === urlTaskId) {
            if (urlTask.id !== urlTaskId || urlTask.state !== 'loaded') setUrlTask({id: urlTaskId, state: 'loaded'});
            return;
        }
        if (urlTask.id === urlTaskId) return;
        setUrlTask({id: urlTaskId, state: 'loading'});
        getJob(urlTaskId)
            .then((job) => {
                if (urlTaskIdRef.current !== urlTaskId) return;
                if (!job?.result) {
                    setUrlTask({id: urlTaskId, state: 'missing'});
                    return;
                }
                setLastResult({...job.result, task_id: job.result.task_id || urlTaskId});
                // The header's 置顶 and 学完了 read the shared list, which on a
                // fresh load holds the browser's cached copy until the whole
                // list arrives; this task's own record says them now.
                if (job.task_id) ingestJobs?.([markBackendJob(job)]);
                setNoteReadFor(urlTaskId);
                setUrlTask({id: urlTaskId, state: 'loaded'});
            })
            .catch(() => {
                if (urlTaskIdRef.current === urlTaskId) setUrlTask({id: urlTaskId, state: 'missing'});
            });
    }, [urlTaskId, lastResult?.task_id, urlTask, getJob, setLastResult, setNoteReadFor, ingestJobs]);
    const openingUrlTask = !!urlTaskId && lastResult?.task_id !== urlTaskId
        && (urlTask.id !== urlTaskId || urlTask.state === 'loading');
    const urlTaskMissing = !!urlTaskId && lastResult?.task_id !== urlTaskId && urlTask.id === urlTaskId && urlTask.state === 'missing';
    // The open result writes itself into the address, unless the address names
    // a task that is still being opened or could not be found.
    const openTaskId = lastResult?.task_id || '';
    useEffect(() => {
        if (!openTaskId || openTaskId === urlTaskId) return;
        if (urlTaskId && (urlTask.id !== urlTaskId || urlTask.state !== 'loaded')) return;
        setSearchParams((prev) => { const next = new URLSearchParams(prev); next.set('task', openTaskId); return next; }, {replace: true});
    }, [openTaskId, urlTaskId, urlTask, setSearchParams]);

    return {urlTaskId, urlWantsCutReview, setSearchParams, openingUrlTask, urlTaskMissing};
};
