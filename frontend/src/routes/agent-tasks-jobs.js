// Which task a row is and where it came from, and the one list the records
// page shows: rows from the shared task list merged with the upload or task
// this window is sending right now. Used by the page, its card and its actions.
import {sortJobsForHistoryView} from '../app/shared.jsx';
import {TASK_STATE_QUEUED, TASK_STATE_RUNNING, TASK_STATE_UPLOADING} from '../lib/taskState.js';

export const taskIdForJob = (job) => String(job?.task_id || job?.result?.task_id || '').trim();

export const isLocalJob = (job) => String(job?.client_id || '').startsWith('local-') || job?.metadata?.stt_provider === 'local';

export const mediaSourceForJob = (job) => {
    const sourceType = String(job?.source_type || job?.result?.source || '').toLowerCase();
    const filename = String(job?.source_filename || job?.result?.filename || '').toLowerCase();
    if (sourceType === 'video_link') return 'video_link';
    if (sourceType === 'transcript_file') return 'transcript_file';
    if (sourceType === 'audio_file' || /\.(mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i.test(filename)) return 'media_file';
    if (sourceType === 'video_file' || sourceType === 'queue_upload' || /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(filename)) return 'media_file';
    return '';
};

export const jobsFromCurrentJob = (currentJob) => {
    if (!currentJob) return [];
    if (currentJob.queueUpload) {
        const items = Array.isArray(currentJob.queueItems) ? currentJob.queueItems : [];
        return items.map((item, index) => {
            const taskId = item.taskId || item.task_id || item.provisionalId || `queue-upload-${index + 1}`;
            const fileName = item.fileName || item.filename || taskId;
            const hasBackendTask = !!(item.taskId || item.task_id);
            const taskState = item.taskState || item.status || (hasBackendTask ? TASK_STATE_QUEUED : TASK_STATE_UPLOADING);
            return {
                task_id: taskId,
                status: taskState,
                task_state: taskState,
                stage: item.stage || (hasBackendTask ? 'queued' : currentJob.stage || 'upload'),
                progress: item.progress ?? (hasBackendTask ? 0 : currentJob.progress ?? 2),
                source_type: item.sourceType || item.source_type || currentJob.sourceType || null,
                source_filename: fileName,
                source_file_size_mb: item.fileSizeMb ?? item.source_file_size_mb ?? null,
                created_at: currentJob.startedAt ? new Date(currentJob.startedAt).toISOString() : new Date().toISOString(),
                metadata: {
                    display_title: fileName,
                    queue_position: item.queuePosition || item.queue_position || index + 1,
                    queue_total: item.queueTotal || item.queue_total || currentJob.queueTotal || items.length,
                    queue_provisional: !hasBackendTask,
                    stt_provider: currentJob.sttProvider || null,
                },
            };
        });
    }
    if (!currentJob.taskId) return [];
    return [{
        task_id: currentJob.taskId,
        status: currentJob.taskState || (currentJob.stage === 'done' ? 'completed' : TASK_STATE_RUNNING),
        task_state: currentJob.taskState || (currentJob.stage === 'done' ? 'completed' : TASK_STATE_RUNNING),
        stage: currentJob.stage || 'queued',
        progress: currentJob.progress ?? 0,
        source_type: currentJob.sourceType || null,
        source_filename: currentJob.fileName || currentJob.taskId,
        source_file_size_mb: currentJob.fileSizeMb || null,
        created_at: currentJob.startedAt ? new Date(currentJob.startedAt).toISOString() : new Date().toISOString(),
        metadata: {
            display_title: currentJob.fileName || currentJob.taskId,
            stt_provider: currentJob.sttProvider || null,
            video_source_progress: currentJob.videoSourceProgress || null,
        },
    }];
};

export const mergeJobs = (...groups) => {
    const byId = new Map();
    groups.flat().forEach((job) => {
        const taskId = taskIdForJob(job);
        if (!taskId) return;
        const existing = byId.get(taskId);
        const nextTs = Date.parse(job?.updated_at || job?.created_at || '') || 0;
        const existingTs = Date.parse(existing?.updated_at || existing?.created_at || '') || 0;
        if (!existing || nextTs >= existingTs) byId.set(taskId, job);
    });
    return sortJobsForHistoryView(Array.from(byId.values()));
};
