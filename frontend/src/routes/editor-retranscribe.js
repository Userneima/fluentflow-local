// Transcribing a result again from the editor, moved out of editor.jsx: the
// confirm dialog's source line, queuing the task again from the recording the
// service can reach, and only when it cannot, a file from this browser (the
// matched local file, the saved audio, or one picked in the hidden input).
import {useRef, useState} from 'react';
import {
    createTaskId,
    effectiveSttProvider,
    submittedSttProvider,
    fileNameStem,
    larkExportRouteFromSettings,
    normalizeSttModel,
    resultToHistoryEntry,
} from '../app/shared.jsx';

export const useRetranscribe = ({
    result,
    matchedLocalSourceFile,
    lang,
    t,
    runtimeConfig,
    loadSettings,
    showToast,
    doneJobTimerRef,
    setLastSourceFile,
    setCurrentJob,
    setLastResult,
    addToHistory,
    addLarkExport,
    ingestJobs,
    processVideoSSE,
    retryJob,
    fetchJobArtifactFile,
    resultJobOptions,
}) => {
    const [retranscribing, setRetranscribing] = useState(false);
    const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false);
    const retranscribeInputRef = useRef(null);
    const canUseStoredSource = !!result?.source_file_available && !!result?.task_id;
    const canUsePlaybackAudio = !!result?.artifacts?.playback_audio && !!result?.task_id;
    const canRetranscribeStoredMedia = !!matchedLocalSourceFile || canUseStoredSource || canUsePlaybackAudio;
    const retranscribeSourceLabel = canRetranscribeStoredMedia
        ? ((matchedLocalSourceFile || canUseStoredSource)
            ? (lang === 'zh' ? '原文件' : 'Source file')
            : (lang === 'zh' ? '已保存音频' : 'Saved audio'))
        : (lang === 'zh' ? '当前结果' : 'Current result');
    const retranscribeSourceName = matchedLocalSourceFile?.name
        || (canUseStoredSource ? result?.filename : result?.artifacts?.playback_audio?.filename)
        || result?.filename
        || t('edit.title');
    const buildAiOptions = (settings) => ({
        noteMode: settings.noteMode||'auto',
        generateVisuals: !!settings.autoIllustrate,
        sttProvider: effectiveSttProvider(settings, runtimeConfig),
    });

    const runRetranscribe = async (file) => {
        if(!file || retranscribing) return;
        const validExts = /\.(mp4|mov|avi|mkv|wmv|flv|webm|m4v|mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i;
        if(!validExts.test(file.name)){
            showToast(t('dash.fileError'), false);
            return;
        }
        const settings = loadSettings();
        const sttModel = normalizeSttModel(settings.sttModel);
        const sttProvider = submittedSttProvider(settings, runtimeConfig);
        const retranscribeErrorMessage = (err) => (
            err?.message || (lang === 'zh' ? '重新转录失败' : 'Retranscription failed')
        );
        const taskId = createTaskId();
        const sourceType = /\.(mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i.test(file.name) ? "audio" : "video";
        const fileSizeMb = Math.round(file.size / 1024 / 1024 * 1000) / 1000;
        setRetranscribing(true);
        setLastSourceFile(file);
        setCurrentJob({
            taskId,
            fileName:file.name,
            stage:'upload',
            progress:2,
            startedAt: Date.now(),
            sourceType,
            fileSizeMb,
            sttProvider,
            sttModel,
            sttLanguage: 'auto',
            skipSummary: !!settings.skipAiSummary,
            exportToLark: !!settings.exportToLark,
            noteMode: settings.noteMode||'auto',
        });
        try {
            const resultData = await processVideoSSE(file, {
                taskId,
                sourceLastModifiedMs: file.lastModified||null,
                larkExportRoute: larkExportRouteFromSettings(settings),
                larkViaCli: !!settings.larkViaCli,
                folderToken: settings.larkFolder || undefined,
                title: file.name.replace(/\.[^/.]+$/,""),
                ...buildAiOptions(settings),
                skipSummary: !!settings.skipAiSummary,
                sttProvider,
                sttModel,
                sttLanguage: 'auto',
            }, (ev) => {
                setCurrentJob(prev => prev ? {
                    ...prev,
                    stage:ev.stage,
                        progress:ev.progress,
                        sttProgress: ev.stt_progress ?? prev.sttProgress,
                        transcribedSeconds: ev.transcribed_seconds ?? prev.transcribedSeconds,
                            durationSeconds: ev.duration_seconds ?? prev.durationSeconds,
                            sttElapsedSeconds: ev.stt_elapsed_seconds ?? prev.sttElapsedSeconds,
                            sttStatus: ev.stt_status ?? prev.sttStatus,
                            sttProvider: ev.stt_provider ?? prev.sttProvider,
                        } : null);
                if(ev.stage === 'transcript_ready' && ev.result) setLastResult(ev.result);
            });

            setLastResult(resultData);
            const larkUrl = resultData.lark_response?.url || null;
            addToHistory(resultToHistoryEntry(resultData, {taskId, name:file.name, requestedNoteMode: settings.noteMode||'auto'}));
            if(larkUrl) addLarkExport({url:larkUrl, title: resultData.lark_doc_title || fileNameStem(file.name), timestamp:Date.now()});
            setCurrentJob({fileName:file.name, stage:'done', progress:100});
            if (doneJobTimerRef.current) clearTimeout(doneJobTimerRef.current);
            doneJobTimerRef.current = setTimeout(() => {
                doneJobTimerRef.current = null;
                setCurrentJob(null);
            }, 3000);
            showToast(t('edit.retranscribeDone'));
        } catch(err) {
            setCurrentJob(null);
            showToast(retranscribeErrorMessage(err), false);
        } finally {
            setRetranscribing(false);
        }
    };

    const handleRetranscribe = () => {
        setRetranscribeConfirmOpen(true);
    };

    const confirmRetranscribe = async () => {
        setRetranscribeConfirmOpen(false);
        const fetchPlaybackAudioForRetranscribe = async () => {
            const artifact = result?.artifacts?.playback_audio;
            if (!result?.task_id || !artifact) throw new Error(t('edit.retranscribeUnavailableTitle'));
            return await fetchJobArtifactFile(
                result.task_id,
                'playback_audio',
                artifact.filename || `${result.filename || 'source'}_audio.mp3`,
                resultJobOptions,
            );
        };
        // Run it again from the recording the backend can reach: the original where
        // it sits for a file chosen on this machine (whose task records no stored
        // copy), else the backend's own copy. Fetching it into the browser and
        // posting it back, as before, moved the whole file twice. Only when the
        // backend cannot reach any recording does a file in the browser go up.
        if(result?.task_id) {
            try {
                const data = await retryJob(result.task_id, resultJobOptions);
                if (data?.job) ingestJobs([data.job]);
                showToast(lang === 'zh'
                    ? '已重新排队转录，用的是本机上的原文件。完成后在处理记录里打开新的结果，这一条保留不动。'
                    : 'Queued again from the file on this machine. Open the new result from the records when it is done; this one stays as it is.');
                return;
            } catch (err) {
                if (matchedLocalSourceFile) {
                    runRetranscribe(matchedLocalSourceFile);
                } else if (result?.artifacts?.playback_audio) {
                    try {
                        const audioFile = await fetchPlaybackAudioForRetranscribe();
                        runRetranscribe(audioFile);
                    } catch (playbackErr) {
                        showToast(playbackErr.message || err.message || t('edit.retranscribeUnavailableTitle'), false);
                        retranscribeInputRef.current?.click();
                    }
                } else {
                    showToast(err.message || t('edit.retranscribeUnavailableTitle'), false);
                    retranscribeInputRef.current?.click();
                }
            }
        } else if(matchedLocalSourceFile) {
            runRetranscribe(matchedLocalSourceFile);
        } else retranscribeInputRef.current?.click();
    };

    return {
        retranscribeConfirmOpen,
        setRetranscribeConfirmOpen,
        retranscribeInputRef,
        canRetranscribeStoredMedia,
        retranscribeSourceLabel,
        retranscribeSourceName,
        runRetranscribe,
        handleRetranscribe,
        confirmRetranscribe,
    };
};
