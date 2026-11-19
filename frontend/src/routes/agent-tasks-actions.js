// What the buttons on the records page do: cancel an upload or a task, delete,
// carry on, pin, stop a step, submit again, download the cut file, open the
// result. Each keeps which task it is busy with, so that card can show it.
import {useMemo, useRef, useState} from 'react';
import {friendlyTaskError, isPinnedJob, jobToHistoryEntry, useApi, useSettings} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {isLiveTask, markBackendJob, cutsToJudge} from '../lib/taskState.js';
import {retryInputForJob, retryOptionsForJob} from '../lib/linkRetry.js';
import {isLocalJob, mediaSourceForJob, taskIdForJob} from './agent-tasks-jobs.js';

export const useTaskActions = ({confirm, t, lang, navigate, loadJobs, setError}) => {
    const {currentJob, setCurrentJob, setLastResult, addToHistory, removeFromHistory, ingestJobs, markCancelled, revertCancelled, restoreTask, abortPendingUpload} = useApp();
    const {getJob, cancelJob, deleteJob, pinJob, retryJob, stopJobStep, resumeJob, createVideoSourceJob, downloadJobMedia} = useApi();
    const {loadSettings} = useSettings();
    const [cancellingTaskId, setCancellingTaskId] = useState('');
    const [openingTaskId, setOpeningTaskId] = useState('');
    const [deletingTaskId, setDeletingTaskId] = useState('');
    const [retryingTaskId, setRetryingTaskId] = useState('');
    const [resumingTaskId, setResumingTaskId] = useState('');
    const [stoppingTaskId, setStoppingTaskId] = useState('');
    const [pinningTaskId, setPinningTaskId] = useState('');
    // A refusal to re-run belongs on the card that was refused, not in the page
    // banner: that banner is cleared by every successful poll, and this page polls
    // while any task is live, so the reason a retry did not happen was wiped
    // before it could be read. The button just spun and nothing appeared.
    const [retryError, setRetryError] = useState(null);
    const [downloadingTaskId, setDownloadingTaskId] = useState('');

    const cancelPendingUpload = async () => {
        const ok = await confirm({
            title: t('confirm.cancelUploadTitle'),
            message: t('confirm.cancelUploadDesc'),
            confirmLabel: t('confirm.cancelUploadAction'),
            cancelLabel: t('confirm.keepUpload'),
        });
        if (!ok) return;
        abortPendingUpload();
    };


    const cancelLiveJob = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        const ok = await confirm({
            title: t('confirm.cancelTaskTitle'),
            message: t('confirm.cancelTaskDesc'),
            confirmLabel: t('confirm.cancelTaskAction'),
            cancelLabel: t('confirm.keepTask'),
        });
        if (!ok) return;
        setCancellingTaskId(taskId);
        markCancelled(taskId);
        try {
            await cancelJob(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            if (currentJob?.taskId === taskId) setCurrentJob(null);
            await loadJobs();
        } catch (err) {
            revertCancelled(taskId);
            setError(friendlyTaskError(err.message || String(err), lang));
            await loadJobs();
        } finally {
            setCancellingTaskId('');
        }
    };

    const deleteTerminalJob = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId || isLiveTask(job)) return;
        const ok = await confirm({
            title: t('confirm.deleteRecordTitle'),
            message: t('confirm.deleteRecordDesc'),
            confirmLabel: t('confirm.deleteRecordAction'),
        });
        if (!ok) return;
        setDeletingTaskId(taskId);
        // Tombstone + drop through AppProvider so an in-flight poll cannot
        // resurrect it; restore if the backend delete fails (non-404).
        removeFromHistory(taskId);
        if (currentJob?.taskId === taskId) setCurrentJob(null);
        try {
            await deleteJob(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            setError(null);
        } catch (err) {
            if (err.status === 404) {
                setError(null);
                return;
            }
            restoreTask(taskId);
            setError(friendlyTaskError(err.message || String(err), lang));
            await loadJobs();
        } finally {
            setDeletingTaskId('');
        }
    };

    // Carry a stopped task on, or redo the step that failed, on the same task;
    // a refusal is said on the card, like a refused retry.
    const resumeTerminalJob = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        setResumingTaskId(taskId);
        setRetryError(null);
        try {
            await resumeJob(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            await loadJobs();
        } catch (err) {
            setRetryError({taskId, message: friendlyTaskError(err.message || String(err), lang)});
        } finally {
            setResumingTaskId('');
        }
    };

    // Pin or unpin; the answer is the task row, put straight into the list
    // (pinning does not move updated_at, so a poll would not bring it).
    const togglePin = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        setPinningTaskId(taskId);
        try {
            const updated = await pinJob(taskId, !isPinnedJob(job));
            if (updated?.task_id) ingestJobs([markBackendJob(updated)]);
            setError(null);
        } catch (err) {
            setError(friendlyTaskError(err.message || String(err), lang));
        } finally {
            setPinningTaskId('');
        }
    };

    const stopJobStepOf = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        setStoppingTaskId(taskId);
        try {
            await stopJobStep(taskId, isLocalJob(job) ? {sttProvider: 'local'} : {});
            setError(null);
            await loadJobs();
        } catch (err) {
            setError(friendlyTaskError(err.message || String(err), lang));
        } finally {
            setStoppingTaskId('');
        }
    };

    const rememberRetriedJobs = (nextJobs=[]) => {
        const normalized = nextJobs.filter(Boolean).map(markBackendJob);
        if (!normalized.length) return;
        ingestJobs(normalized);
    };

    const retryStoredSourceJob = async (job) => {
        const taskId = taskIdForJob(job);
        const localOptions = isLocalJob(job) ? {sttProvider: 'local'} : {};
        return await retryJob(taskId, localOptions);
    };

    const retryTerminalJob = async (job, {pictureNote = false} = {}) => {
        const taskId = taskIdForJob(job);
        if (!taskId || isLiveTask(job)) return;
        setRetryingTaskId(taskId);
        setError(null);
        setRetryError(null);
        try {
            if (pictureNote) {
                // 「照画面写笔记」: the backend retries the task itself, a link
                // task included, so the flag reaches the new one.
                const localOptions = isLocalJob(job) ? {sttProvider: 'local'} : {};
                const response = await retryJob(taskId, {...localOptions, pictureNote: true});
                if (response?.job) rememberRetriedJobs([response.job]);
                await loadJobs();
                return;
            }
            // Today's browser-login choice, not the one stored on the job: the
            // usual fix for a failed link is to pick a browser and try again.
            const options = retryOptionsForJob(job, {cookiesBrowser: loadSettings().videoCookiesBrowser || ''});
            const sourceKind = mediaSourceForJob(job);
            if (sourceKind === 'video_link') {
                const input = retryInputForJob(job);
                if (!input) {
                    throw new Error(lang === 'zh' ? '这条记录没有保留原视频链接，请从开始处理页重新提交。' : 'This record does not keep the original video link. Submit it again from the start page.');
                }
                const response = await createVideoSourceJob(input, options);
                const nextJob = response?.job ? markBackendJob(response.job) : null;
                if (nextJob) rememberRetriedJobs([nextJob]);
                await loadJobs();
                return;
            }
            if (sourceKind === 'media_file') {
                const response = await retryStoredSourceJob(job);
                const nextJobs = response?.job
                    ? [response.job]
                    : (Array.isArray(response?.queued) ? response.queued : []);
                rememberRetriedJobs(nextJobs);
                await loadJobs();
                return;
            }
            throw new Error(lang === 'zh' ? '这条记录的来源暂不支持直接重新提交，请从开始处理页重新提交。' : 'This record source cannot be resubmitted directly yet. Submit it again from the start page.');
        } catch (err) {
            setRetryError({taskId, message: friendlyTaskError(err.message || String(err), lang)});
        } finally {
            setRetryingTaskId('');
        }
    };

    const getJobWithFallback = async (job) => {
        const taskId = taskIdForJob(job);
        const primaryOptions = isLocalJob(job) ? {sttProvider: 'local'} : {};
        try {
            return await getJob(taskId, primaryOptions);
        } catch (err) {
            if (err.status !== 404 || primaryOptions.sttProvider === 'local') throw err;
            return getJob(taskId, {sttProvider: 'local'});
        }
    };

    // Hand over the cut file itself.
    //
    // Offered only on the two cards that say something: one whose cut file stayed
    // in the app because the drop had no folder to go back to, and one whose copy
    // beside the original failed. On every other card the file is already in the
    // owner's folder and a download button would be offering them what they have.
    const downloadCutFile = async (job) => {
        const taskId = taskIdForJob(job);
        const artifact = job?.result?.artifacts?.debreath_media;
        if (!taskId || !artifact) return;
        const name = String(artifact.filename || '').split('/').pop() || `${taskId}_debreath.mp4`;
        setDownloadingTaskId(taskId);
        setRetryError((prev) => (prev?.taskId === taskId ? null : prev));
        try {
            await downloadJobMedia(taskId, 'debreath_media', name);
        } catch (error) {
            // On the card itself, where the button was pressed: the page-wide
            // error bar is cleared by the next poll and sits above a long list.
            setRetryError({taskId, message: error?.message || (lang === 'zh' ? '剪后文件不可用' : 'The cut file is unavailable')});
        } finally {
            setDownloadingTaskId('');
        }
    };

    const openResult = async (job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        // The task id goes in the address, and a task whose cut still has
        // places for the owner to judge says so, so the editor can take them
        // there (the editor side is drawn by the editor's own work).
        const open = (sourceJob, result) => {
            setLastResult(result);
            addToHistory(jobToHistoryEntry({...sourceJob, result}));
            const params = new URLSearchParams({task: taskId});
            if (cutsToJudge({...sourceJob, result})) params.set('review', 'cut');
            navigate(`/editor?${params.toString()}`);
        };
        if (job.result) {
            open(job, job.result);
            return;
        }
        setOpeningTaskId(taskId);
        try {
            const fresh = await getJobWithFallback(job);
            if (fresh?.result) {
                open(fresh, fresh.result);
                return;
            }
            setError(lang === 'zh' ? '这条记录暂时没有可打开的结果。请刷新后再试。' : 'This record does not have an openable result yet. Refresh and try again.');
        } catch (err) {
            setError(friendlyTaskError(err.message || String(err), lang));
        } finally {
            setOpeningTaskId('');
        }
    };

    // The handlers close over the latest page state, so they are new every
    // render; the cards receive stable wrappers that look them up through a ref,
    // which is what lets a card skip rendering when its row did not change.
    const handlersRef = useRef({});
    const openSteps = (job) => navigate(`/tasks/${encodeURIComponent(taskIdForJob(job))}/agent`);
    handlersRef.current = {cancelLiveJob, deleteTerminalJob, downloadCutFile, openResult, openSteps, retryTerminalJob, resumeTerminalJob, stopJobStepOf, togglePin};
    const cardHandlers = useMemo(() => ({
        onCancel: (job) => handlersRef.current.cancelLiveJob(job),
        onDelete: (job) => handlersRef.current.deleteTerminalJob(job),
        onDownloadCut: (job) => handlersRef.current.downloadCutFile(job),
        onOpenResult: (job) => handlersRef.current.openResult(job),
        onOpenSteps: (job) => handlersRef.current.openSteps(job),
        onPin: (job) => handlersRef.current.togglePin(job),
        onRetry: (job, options) => handlersRef.current.retryTerminalJob(job, options),
        onResume: (job) => handlersRef.current.resumeTerminalJob(job),
        onStop: (job) => handlersRef.current.stopJobStepOf(job),
    }), []);

    const busy = {cancellingTaskId, deletingTaskId, openingTaskId, retryingTaskId, downloadingTaskId, resumingTaskId, stoppingTaskId, pinningTaskId};
    return {busy, retryError, cancelPendingUpload, cardHandlers};
};
