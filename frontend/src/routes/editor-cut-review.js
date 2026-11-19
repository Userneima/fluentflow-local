// The cut's processing report on the editor page, moved out of editor.jsx:
// open or not, opened by ?review=cut (useEditorAddress), and the task's cut
// record read again when it closes.
import {useEffect, useState} from 'react';

export const useCutReview = ({result, urlWantsCutReview, urlTaskId, setSearchParams, getJob, resultJobOptions, setLastResult}) => {
    const [cutReviewOpen, setCutReviewOpen] = useState(false);
    // Arriving with ?review=cut opens the report once the named task is open,
    // and the parameter is dropped so a refresh does not open it again.
    useEffect(() => {
        if (!urlWantsCutReview || !result?.task_id || result.task_id !== urlTaskId) return;
        setCutReviewOpen(true);
        setSearchParams((prev) => { const next = new URLSearchParams(prev); next.delete('review'); return next; }, {replace: true});
    }, [urlWantsCutReview, result?.task_id, urlTaskId, setSearchParams]);
    // Answers given in the report change how many places still wait, so the
    // task's cut record is read again when the report closes.
    const closeCutReview = () => {
        setCutReviewOpen(false);
        const taskId = result?.task_id;
        if (!taskId) return;
        getJob(taskId, resultJobOptions)
            .then((job) => {
                const fresh = job?.result;
                if (!fresh) return;
                setLastResult((prev) => (prev?.task_id === taskId ? {...prev, debreath: fresh.debreath} : prev));
            })
            .catch(() => {});
    };

    return {cutReviewOpen, setCutReviewOpen, closeCutReview};
};
