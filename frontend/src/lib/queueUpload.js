const audioExts = /\.(mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i;

const fileSizeMb = (file) => (
    Math.round((Number(file?.size || 0) / 1024 / 1024) * 1000) / 1000
);

const sourceTypeForFile = (filename='') => (
    audioExts.test(String(filename || '')) ? 'audio_file' : 'video_file'
);

export const queueUploadItemsFromFiles = (files=[]) => {
    const list = Array.from(files || []);
    const total = list.length;
    return list.map((file, index) => {
        const fileName = file?.name || `source-${index + 1}`;
        return {
            provisionalId: `queue-upload-${index + 1}-${fileName}-${file?.size || 0}-${file?.lastModified || 0}`,
            taskId: null,
            fileName,
            sourceType: sourceTypeForFile(fileName),
            fileSizeMb: fileSizeMb(file),
            queuePosition: index + 1,
            queueTotal: total,
            stage: 'upload',
            status: 'uploading',
            taskState: 'uploading',
            progress: 2,
            provisional: true,
        };
    });
};

export const queueUploadItemsFromQueuedResponse = (queued=[], fallbackItems=[]) => {
    const fallbackByName = new Map(
        (Array.isArray(fallbackItems) ? fallbackItems : []).map((item) => [String(item.fileName || item.filename || ''), item])
    );
    const list = Array.isArray(queued) ? queued : [];
    const total = list.length || fallbackItems.length || 0;
    return list.map((item, index) => {
        const fileName = item?.filename || item?.source_filename || `source-${index + 1}`;
        const fallback = fallbackByName.get(String(fileName)) || fallbackItems[index] || {};
        return {
            provisionalId: fallback.provisionalId || `queue-upload-${index + 1}-${fileName}`,
            taskId: item?.task_id || fallback.taskId || null,
            fileName,
            sourceType: item?.source_type || fallback.sourceType || sourceTypeForFile(fileName),
            fileSizeMb: item?.source_file_size_mb ?? fallback.fileSizeMb ?? null,
            queuePosition: item?.queue_position || fallback.queuePosition || index + 1,
            queueTotal: item?.queue_total || fallback.queueTotal || total,
            stage: 'queued',
            status: item?.status || 'queued',
            taskState: item?.status || 'queued',
            progress: 0,
            provisional: !item?.task_id,
        };
    });
};
