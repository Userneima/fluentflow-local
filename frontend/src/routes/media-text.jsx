import {useEffect, useMemo, useRef, useState} from 'react';
import {Link, useNavigate, useSearchParams} from 'react-router-dom';
import {XCircle} from 'lucide-react';
import {
    DEFAULT_PROMPT_PRESET,
    presetDisplayLabel,
    resolveSystemPromptFromSettings,
} from '../lib/promptPresets.js';
import {
    createTaskId,
    effectiveSttProvider,
    submittedSttProvider,
    fileNameStem,
    fmtFileSize,
    friendlyTaskError,
    hasTranscriptResult,
    historyEntryToResult,
    jobToCurrentJob,
    jobToHistoryEntry,
    larkExportRouteFromSettings,
    normalizeSourceMode,
    normalizeSttModel,
    resultToHistoryEntry,
    timeAgo,
    totalFileSizeMb,
    useApi,
    useI18n,
    useSettings,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {
    queueUploadItemsFromFiles,
    queueUploadItemsFromQueuedResponse,
} from '../lib/queueUpload.js';
import SvgIcon from '../components/SvgIcon.jsx';
import {finishedDuplicateLink, submitIgnoredExtraUrls, submitWasDuplicateOfActive} from '../lib/linkRetry.js';
import {noteWriterBatchClause, resolveNoteWriter, textModelKeyConfigured} from '../lib/noteWriter.js';
import {NOTE_ATTENTION_IDS, workflowChoicesFrom} from '../lib/workflowStatus.js';
import {NoteKeyOnboarding} from '../components/NoteKeyOnboarding.jsx';
import {dismissNoteKeyOnboarding, noteKeyOnboardingDismissed, shouldAutoOpenNoteKeyOnboarding} from '../lib/noteKeyOnboarding.js';
import {taskOutcome} from '../lib/taskState.js';
import {fmtDuration} from '../lib/format.js';
import {speakerDiarizationRequested} from '../lib/settingsModel.js';

const mediaExts = /\.(mp4|mov|avi|mkv|wmv|flv|webm|m4v|mp3|wav|flac|aac|ogg|m4a|wma|opus)$/i;
const transcriptExts = /\.(srt|vtt|txt|md)$/i;
// local_folder_intake.MAX_FILES_PER_FOLDER on the backend, which takes no more per request.
const LOCAL_PATHS_PER_BATCH = 30;

const platformItems = [
    {label: '抖音', tone: 'bg-[#111111] text-white', icon: 'douyin'},
    {label: 'Bilibili', tone: 'bg-[#00aeec] text-white', icon: 'bilibili'},
    {label: 'YouTube', tone: 'bg-[#ff0033] text-white', icon: 'youtube'},
    {label: '本地文件', tone: 'bg-[#efeeee] text-[#111111]', icon: 'local-file'},
];

const RECENT_BADGE_TONE = {
    live: 'bg-blue-50 text-blue-700 dark:bg-blue-400/10 dark:text-blue-200',
    warn: 'bg-[#fffaf0] text-[#8a5a00] dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]',
    failed: 'bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-300',
};

const ENTRY_TABS = [
    {id: 'link', zh: '视频链接', en: 'Video link'},
    {id: 'upload', zh: '本机文件', en: 'Files on this computer'},
    {id: 'subtitle', zh: '字幕文件', en: 'Subtitle file'},
];

const MediaText = () => {
    const {t, lang} = useI18n();
    const {
        history,
        tasks,
        addToHistory,
        currentJob,
        setCurrentJob,
        setLastResult,
        setLastSourceFile,
        addLarkExport,
        runtimeConfig,
        diarizationStatus,
        setPendingUploadAbort,
        abortPendingUpload,
        backendDown,
        reportBackendError,
    } = useApp();
    const {
        enqueueProcessFiles,
        createVideoSourceJob,
        summarizeTranscriptFile,
        cancelJob,
        chooseLocalMedia,
        chooseLocalFolder,
        processLocalFolder,
        locateDroppedFile,
        processLocalPaths,
        getCredentialsStatus,
        getWorkflowStatus,
        getJob,
    } = useApi();
    const {loadSettings} = useSettings();
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    // How many files were just dropped that this machine could not place. Says
    // what is different about them, once, at the moment it becomes true.
    const [droppedWithoutPath, setDroppedWithoutPath] = useState(0);
    const mode = searchParams.get('mode') === 'subtitle' ? 'subtitle' : 'media';
    // Which way in this page opens on, from settings. Someone whose material is
    // always files on this machine was clicking past the link box every time.
    const [sourceMode, setSourceMode] = useState(() => normalizeSourceMode(loadSettings().defaultSourceMode));
    const [videoLinkInput, setVideoLinkInput] = useState('');
    const [uploadError, setUploadError] = useState(null);
    const [processingResult, setProcessingResult] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [choosing, setChoosing] = useState(false);
    const subtitleInputRef = useRef(null);
    const abortRef = useRef(null);

    // Who will write the note for the next job, from the same rule the backend
    // uses (lib/noteWriter.js). A fresh install has no model key, and nothing
    // else on this page says so: the first job would finish with a transcript
    // and no note, and the person would only then learn there was a setting to
    // fill in. Said once, here, before they spend a job finding out. The same
    // answer goes into the batch confirmations below, so the three never
    // disagree about whose model is doing the writing.
    const [credentialStatus, setCredentialStatus] = useState(null);
    useEffect(() => {
        let active = true;
        getCredentialsStatus?.()
            .then((status) => { if (active && status) setCredentialStatus(status); })
            .catch(() => {});
        return () => { active = false; };
    }, [getCredentialsStatus]);
    const noteWriter = useMemo(
        () => resolveNoteWriter({runtimeConfig, credentialStatus, settings: loadSettings()}),
        // loadSettings reads localStorage; the stored settings only change on the
        // settings page, which is a different route.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [runtimeConfig, credentialStatus],
    );
    // What the banner says comes from the service's step list, the one
    // Settings shows (decision 6.24), read again whenever the keys change.
    const [noteAttention, setNoteAttention] = useState([]);
    useEffect(() => {
        if (credentialStatus === null || !getWorkflowStatus) return undefined;
        let active = true;
        getWorkflowStatus(workflowChoicesFrom(loadSettings()))
            .then((overview) => {
                if (!active) return;
                setNoteAttention((overview?.attention || []).filter((item) => NOTE_ATTENTION_IDS.includes(item.id)));
            })
            .catch(() => {});
        return () => { active = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [credentialStatus, getWorkflowStatus]);
    const noNoteKey = noteAttention.some((item) => item.id === 'no-writer');
    // The first-run note setup (lib/noteKeyOnboarding.js). Decided once, when
    // the key status first arrives; after that only the person opens or
    // closes it, so a status refresh cannot pop it back up.
    const [keySetupOpen, setKeySetupOpen] = useState(false);
    const keySetupDecidedRef = useRef(false);
    useEffect(() => {
        if (keySetupDecidedRef.current || credentialStatus === null) return;
        keySetupDecidedRef.current = true;
        if (shouldAutoOpenNoteKeyOnboarding({writer: noteWriter, credentialStatus, dismissed: noteKeyOnboardingDismissed()})) {
            setKeySetupOpen(true);
        }
    }, [credentialStatus, noteWriter]);
    const closeKeySetup = ({completed} = {}) => {
        if (!completed) dismissNoteKeyOnboarding();
        setKeySetupOpen(false);
    };
    // A subtitle or transcript file is turned into a note by the text model
    // only; Claude's frame note needs the video. With no text-model key the
    // job would finish without a note and without saying why.
    const subtitleNeedsTextKey = mode === 'subtitle' && credentialStatus !== null
        && !textModelKeyConfigured({credentialStatus, settings: loadSettings()});
    // The subtitle notice says the same thing more precisely; one is enough.
    const showWriterBanner = !subtitleNeedsTextKey && noteAttention.length > 0;
    const subtitleKeyText = lang === 'zh'
        ? '用字幕生成笔记需要一个文本模型的 Key（例如 DeepSeek），去设置填写'
        : 'Turning subtitles into a note needs a text-model key (for example DeepSeek). Add one in Settings';
    // The "done" card clears itself after a moment; the timer must not outlive
    // the page, and a second result must not be cleared by the first one's timer.
    const settleTimerRef = useRef(null);
    useEffect(() => () => { if (settleTimerRef.current) clearTimeout(settleTimerRef.current); }, []);
    useEffect(() => {
        if (mode === 'subtitle') setSourceMode('upload');
    }, [mode]);
    // Which of the three tabs is showing; a subtitle file keeps its own address
    // (?mode=subtitle) so links and settings that open it still do.
    const entry = mode === 'subtitle' ? 'subtitle' : sourceMode;
    const chooseEntry = (next) => {
        setUploadError(null);
        if (next === 'subtitle') {
            setSearchParams({mode: 'subtitle'});
            return;
        }
        if (mode === 'subtitle') setSearchParams({mode: 'media'});
        setSourceMode(next);
    };

    const buildAiOptions = (settings) => ({
        aiProvider: settings.aiProvider || 'deepseek',
        aiModel: settings.aiModel || null,
        systemPrompt: resolveSystemPromptFromSettings(settings) || null,
        noteMode: settings.noteMode || 'auto',
        promptPreset: settings.promptPreset || DEFAULT_PROMPT_PRESET,
        promptPresetLabel: presetDisplayLabel(settings.promptPreset || DEFAULT_PROMPT_PRESET, settings, lang),
        speakerDiarization: speakerDiarizationRequested(settings, diarizationStatus),
        generateVisuals: !!settings.autoIllustrate,
        sttProvider: effectiveSttProvider(settings, runtimeConfig),
        cookiesFromBrowser: settings.videoCookiesBrowser || '',
    });

    // The settings every way in sends: upload, link, a path from the system
    // dialog, a whole folder. One builder, so a setting cannot reach one entry
    // and miss another (the dialog and folder entries used to drop the Feishu
    // export, the provider and the prompt without saying so).
    const buildSubmitOptions = (settings) => ({
        exportToLark: settings.exportToLark || false,
        larkExportRoute: larkExportRouteFromSettings(settings),
        larkViaCli: !!settings.larkViaCli,
        folderToken: settings.larkFolder || undefined,
        ...buildAiOptions(settings),
        skipSummary: !!settings.skipAiSummary,
        // Sent only when the user actually chose an engine; null lets the
        // server apply its own default.
        sttProvider: submittedSttProvider(settings, runtimeConfig),
        sttModel: normalizeSttModel(settings.sttModel),
        sttLanguage: 'auto',
        voiceEnhance: !!settings.voiceEnhance,
    });

    const settleResult = (result, {taskId, fileName, source = 'media'} = {}) => {
        const displayName = result?.title || result?.filename || fileName;
        setLastResult(result);
        setProcessingResult(result);
        setCurrentJob({taskId, fileName: displayName || fileName, stage: 'done', progress: 100});
        addToHistory(resultToHistoryEntry(result, {
            taskId,
            name: displayName || fileName,
            rawFilename: fileName,
            requestedNoteMode: loadSettings().noteMode || 'auto',
            source,
        }));
        const larkUrl = result?.lark_response?.url || null;
        if (larkUrl) addLarkExport({url: larkUrl, title: result.lark_doc_title || fileNameStem(displayName || fileName), timestamp: Date.now()});
        if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
        settleTimerRef.current = setTimeout(() => {
            settleTimerRef.current = null;
            setCurrentJob((prev) => prev?.taskId === taskId ? null : prev);
        }, 3000);
    };

    const startMediaFiles = async (files) => {
        const selectedFiles = Array.from(files || []);
        if (selectedFiles.length === 0) return;
        if (!selectedFiles.every((file) => mediaExts.test(file.name))) {
            setUploadError(t('dash.fileError'));
            return;
        }
        // Same limit and same sentence as the server's refusal, said before
        // the bytes are sent.
        const maxQueueFiles = Number(runtimeConfig?.limits?.max_queue_files) || 0;
        if (maxQueueFiles > 0 && selectedFiles.length > maxQueueFiles) {
            setUploadError(lang === 'zh'
                ? `一次最多提交 ${maxQueueFiles} 个文件，这次选了 ${selectedFiles.length} 个。请分成几批提交。`
                : `At most ${maxQueueFiles} files per batch; ${selectedFiles.length} were chosen. Split them into batches.`);
            return;
        }
        setUploadError(null);
        setProcessingResult(null);
        setLastResult(null);
        const settings = loadSettings();

        // Every local media upload (single or multiple) goes through the
        // background queue so the single worker processes them one at a time.
        // This is the only way to guarantee "one video at a time" regardless of
        // whether the user uploads files individually or selects several at once.
        const queueLabel = selectedFiles.length === 1
            ? selectedFiles[0].name
            : (lang === 'zh' ? `${selectedFiles.length} 个文件` : `${selectedFiles.length} files`);
        const uploadController = new AbortController();
        abortRef.current = uploadController;
        setPendingUploadAbort(uploadController);
        setSubmitting(true);
        setLastSourceFile(null);
        const provisionalQueueItems = queueUploadItemsFromFiles(selectedFiles);
        setCurrentJob({
            taskId: null,
            fileName: queueLabel,
            stage: 'upload',
            progress: 2,
            startedAt: Date.now(),
            sourceType: 'queue_upload',
            fileSizeMb: totalFileSizeMb(selectedFiles),
            queueTotal: selectedFiles.length,
            queueItems: provisionalQueueItems,
            queueUpload: true,
        });
        navigate('/agent');
        try {
            const data = await enqueueProcessFiles(selectedFiles, buildSubmitOptions(settings), {
                onProgress: (pct) => setCurrentJob((prev) => (
                    prev && prev.queueUpload && !prev.queueSubmitted
                        ? {...prev, progress: Math.max(2, Math.min(99, pct))}
                        : prev
                )),
                signal: uploadController.signal,
            });
            const queueItems = queueUploadItemsFromQueuedResponse(data?.queued, provisionalQueueItems);
            setCurrentJob({
                taskId: null,
                fileName: queueLabel,
                stage: 'queued',
                progress: 100,
                startedAt: Date.now(),
                sourceType: 'queue_upload',
                fileSizeMb: totalFileSizeMb(selectedFiles),
                queueTotal: selectedFiles.length,
                queueItems,
                queueUpload: true,
                queueSubmitted: true,
            });
            navigate('/agent', {replace: true, state: {queueSubmittedAt: Date.now()}});
        } catch (err) {
            setCurrentJob(null);
            if (err?.aborted) {
                navigate('/agent', {replace: true});
                return;
            }
            reportBackendError?.(err);
            const submitError = err?.status
                ? friendlyTaskError(err.message || 'Queue failed.', lang)
                : (lang === 'zh'
                    ? '上传失败或中断，请重新提交。'
                    : 'Upload failed or was interrupted. Please submit again.');
            navigate('/agent', {
                replace: true,
                state: {queueSubmitError: submitError},
            });
        } finally {
            if (abortRef.current === uploadController) {
                abortRef.current = null;
                setPendingUploadAbort(null);
            }
            setSubmitting(false);
        }
        return;

    };

    // Pick a recording through the machine's own dialog instead of the browser's.
    //
    // The browser's picker hands this page bytes and a name, never the folder, so
    // an uploaded recording has no "next to the original" for its cut version to
    // land in. The system dialog returns a real path: the file is then read where
    // it is — no gigabyte copied into the store first — and the cut version is
    // written beside it. Local edition only; if the dialog is unavailable the
    // backend says so and the ordinary upload above still works.
    // Queue recordings this machine can name by path: read where they are, no copy
    // into the store, and the cut version written beside the original. Shared by
    // the picker and by a drop whose file was found on disk, because from here on
    // the two are the same thing — a path.
    const queueLocalPaths = async (chosen) => {
        // At most this many at a time, the same as a folder; the backend holds the
        // same number. Past it the rest are named here rather than dropped quietly.
        const taken = chosen.slice(0, LOCAL_PATHS_PER_BATCH);
        const left = chosen.length - taken.length;
        // Every task queued here spends one call of whichever model writes the
        // note. One file is the ordinary flow; thirty at once is a different
        // order of magnitude of someone's allowance, so it is confirmed, and the
        // confirmation names the writer.
        if (chosen.length > 1) {
            const clause = noteWriterBatchClause(noteWriter, lang);
            const tail = left
                ? (lang === 'zh'
                    ? `\n（一次最多 ${LOCAL_PATHS_PER_BATCH} 个，这次处理前 ${taken.length} 个，剩下 ${left} 个之后再选一次）`
                    : `\n(At most ${LOCAL_PATHS_PER_BATCH} at a time: the first ${taken.length} now, choose the other ${left} afterwards.)`)
                : '';
            const confirmText = lang === 'zh'
                ? `选了 ${chosen.length} 个文件。每个都会自动去气口、转写${clause}。继续吗？${tail}`
                : `${chosen.length} files selected. Each one is cut and transcribed${clause}. Continue?${tail}`;
            if (!window.confirm(confirmText)) return null;
        }
        return processLocalPaths(taken, buildSubmitOptions(loadSettings()));
    };

    // A drop, taken as far as this machine can take it.
    //
    // The browser gives the page a dropped file's bytes and three labels, never
    // its folder. Uploading is what that leaves — a second copy of a gigabyte in
    // the store, and no "beside the original" for the cut file. But the service
    // is on the same machine as the file, so before uploading anything the labels
    // go out and it looks in the folders it has already been sent to. Found means
    // the copy never happens; not found means the upload, which is what a drop
    // did before this existed.
    const handleDroppedMedia = async (files) => {
        const located = await Promise.all(files.map((file) => locateDroppedFile?.(file)));
        if (!located.every((hit) => hit?.path)) {
            setDroppedWithoutPath(files.length);
            startMediaFiles(files);
            return;
        }
        setDroppedWithoutPath(0);
        try {
            const queued = await queueLocalPaths(located.map((hit) => hit.path));
            if (queued) navigate('/agent');
        } catch (error) {
            reportBackendError?.(error);
            setUploadError(friendlyTaskError(error?.message || (lang === 'zh' ? '处理失败' : 'Could not start processing'), lang));
        }
    };

    const handleChooseFromComputer = async () => {
        if (choosing || submitting) return;
        setChoosing(true);
        setUploadError(null);
        try {
            const chosen = await chooseLocalMedia({prompt: lang === 'zh' ? '选择要做笔记的录像' : 'Choose a recording'});
            if (chosen?.cancelled) return;
            const files = Array.isArray(chosen?.files) ? chosen.files : [];
            const usable = files.filter((item) => item?.usable && item?.path);
            const rejected = files.filter((item) => item && !item.usable);
            if (!usable.length) {
                setUploadError(rejected[0]?.reason || (lang === 'zh' ? '没有可处理的文件。' : 'Nothing usable was chosen.'));
                return;
            }
            // The system dialog allows multiple selections, and every task queued here
            // spends one call of whichever model writes the note. One file is the
            // flow the owner asked for; thirty at once is a different order of
            // magnitude of someone's allowance, so the count is confirmed first.
            setDroppedWithoutPath(0);
            const queued = await queueLocalPaths(usable.map((item) => item.path));
            if (!queued) return;
            if (rejected.length) {
                // Partly usable is a real outcome: say what was left out rather
                // than silently processing a subset.
                setUploadError(
                    lang === 'zh'
                        ? `已开始处理 ${queued?.count || 0} 个；跳过 ${rejected.length} 个：${rejected[0]?.reason || ''}`
                        : `Started ${queued?.count || 0}; skipped ${rejected.length}: ${rejected[0]?.reason || ''}`
                );
            }
            navigate('/agent');
        } catch (error) {
            reportBackendError?.(error);
            setUploadError(friendlyTaskError(error?.message || (lang === 'zh' ? '选择文件失败' : 'Could not choose a file'), lang));
        } finally {
            setChoosing(false);
        }
    };

    // The entry this edition is actually for: the recordings are already on the
    // disk, in a folder. Uploading a morning's material one file at a time is
    // asking someone to copy gigabytes they already have, and the browser's own
    // picker cannot hand over a folder at all.
    //
    // The folder is chosen and its contents reported in one call, because the
    // count is what the decision is about — a folder is however many model calls
    // it has recordings in it, and that has to be on screen before the button,
    // not discovered afterwards.
    const handleChooseFolder = async () => {
        if (choosing || submitting) return;
        setChoosing(true);
        setUploadError(null);
        try {
            const chosen = await chooseLocalFolder({
                prompt: lang === 'zh' ? '选择装着录像的文件夹' : 'Choose a folder of recordings',
            });
            if (chosen?.cancelled) return;
            const count = Number(chosen?.count) || 0;
            if (!count) {
                setUploadError(lang === 'zh'
                    ? '这个文件夹里没有可以处理的录音或录像。'
                    : 'There is nothing in that folder this can process.');
                return;
            }
            // What was left out, said before the run rather than discovered in the
            // records afterwards. A second pass over a folder skips what the first
            // one produced, and that is the number that explains "I chose ten and
            // it started six".
            const skipped = [];
            if (chosen.skipped_cut_files) {
                skipped.push(lang === 'zh'
                    ? `${chosen.skipped_cut_files} 个是上次剪出来的成品，跳过`
                    : `${chosen.skipped_cut_files} already cut by a previous pass, skipped`);
            }
            if (chosen.skipped_unsupported) {
                skipped.push(lang === 'zh'
                    ? `${chosen.skipped_unsupported} 个不是音视频，跳过`
                    : `${chosen.skipped_unsupported} not audio or video, skipped`);
            }
            if (chosen.truncated) {
                skipped.push(lang === 'zh'
                    ? `一次最多 ${chosen.limit} 个，这次只取前 ${count} 个`
                    : `at most ${chosen.limit} at a time, so only the first ${count} are taken`);
            }
            const tail = skipped.length ? `\n（${skipped.join('；')}）` : '';
            const clause = noteWriterBatchClause(noteWriter, lang);
            const confirmText = lang === 'zh'
                ? `要处理这个文件夹里的 ${count} 个录像吗？每个都会自动去气口、转写${clause}。${tail}`
                : `Process ${count} recordings in this folder? Each is cut and transcribed${clause}.${tail}`;
            if (!window.confirm(confirmText)) return;
            setDroppedWithoutPath(0);
            await processLocalFolder(chosen.path, buildSubmitOptions(loadSettings()));
            navigate('/agent');
        } catch (error) {
            reportBackendError?.(error);
            setUploadError(friendlyTaskError(error?.message || (lang === 'zh' ? '选择文件夹失败' : 'Could not choose a folder'), lang));
        } finally {
            setChoosing(false);
        }
    };

    // A link already finished once: {input, existingTaskId, existingStatus}.
    // Asked about before anything is downloaded a second time.
    const [duplicateLink, setDuplicateLink] = useState(null);

    const submitVideoLink = async (input, {allowDuplicate = false} = {}) => {
        setUploadError(null);
        setProcessingResult(null);
        setLastResult(null);
        setLastSourceFile(null);
        const settings = loadSettings();
        const sttModel = normalizeSttModel(settings.sttModel);
        // Sent only when the user actually chose an engine; null lets the
        // server apply its own default.
        const sttProvider = submittedSttProvider(settings, runtimeConfig);
        const ac = new AbortController();
        abortRef.current = ac;
        setSubmitting(true);
        try {
            const options = {
                ...buildSubmitOptions(settings),
                ...(allowDuplicate ? {allowDuplicate: true} : {}),
            };
            const data = await createVideoSourceJob(input, options, ac.signal);
            const job = data?.job || {};
            if (job.task_id && submitWasDuplicateOfActive(data)) {
                // Already queued or running: nothing new was started, so the
                // page's own "current job" is left alone and the person is
                // taken to the task that has the link.
                setVideoLinkInput('');
                abortRef.current = null;
                setSubmitting(false);
                navigate('/agent', {state: {job, duplicateOfActive: true, highlightTaskId: job.task_id}});
                return;
            }
            if (job.task_id) {
                setCurrentJob({
                    ...jobToCurrentJob({...job, progress: job.progress ?? 2, created_at: job.created_at || new Date().toISOString()}),
                    sourceType: 'video_link',
                    resume: true,
                    skipSummary: !!settings.skipAiSummary,
                    exportToLark: !!settings.exportToLark,
                    noteMode: settings.noteMode || 'auto',
                    sttProvider,
                    sttModel,
                    sttLanguage: 'auto',
                });
                setVideoLinkInput('');
                abortRef.current = null;
                setSubmitting(false);
                navigate('/agent', {state: {job, extraUrlsIgnored: submitIgnoredExtraUrls(data)}});
                return;
            }
        } catch (err) {
            const duplicate = !allowDuplicate ? finishedDuplicateLink(err) : null;
            if (duplicate) {
                setDuplicateLink({input, ...duplicate});
            } else {
                reportBackendError?.(err);
                setUploadError(friendlyTaskError(err?.message || 'Video link fetch failed.', lang));
            }
        }
        if (abortRef.current === ac) abortRef.current = null;
        setSubmitting(false);
    };

    const handleVideoLinkSubmit = async () => {
        const input = videoLinkInput.trim();
        if (!input) {
            setUploadError(t('dash.linkEmpty'));
            return;
        }
        await submitVideoLink(input);
    };

    const handleProcessLinkAgain = async () => {
        const pending = duplicateLink;
        setDuplicateLink(null);
        if (pending) await submitVideoLink(pending.input, {allowDuplicate: true});
    };

    // Open the earlier result in the editor; if it cannot be read, point at it
    // in the task list instead of failing silently.
    const handleOpenExistingLinkTask = async () => {
        const pending = duplicateLink;
        setDuplicateLink(null);
        if (!pending?.existingTaskId) return;
        setVideoLinkInput('');
        try {
            const fresh = await getJob?.(pending.existingTaskId, {sttProvider: 'local'});
            if (fresh?.result) {
                setLastResult(fresh.result);
                addToHistory(jobToHistoryEntry({...fresh, result: fresh.result}));
                navigate('/editor');
                return;
            }
            navigate('/agent', {state: {...(fresh?.task_id ? {job: fresh} : {}), highlightTaskId: pending.existingTaskId}});
        } catch (_) {
            navigate('/agent', {state: {highlightTaskId: pending.existingTaskId}});
        }
    };

    const handleSubtitleSelect = async (eventOrFiles) => {
        const file = Array.isArray(eventOrFiles)
            ? eventOrFiles[0]
            : eventOrFiles?.target?.files?.[0];
        if (subtitleInputRef.current) subtitleInputRef.current.value = '';
        if (!file) return;
        if (!transcriptExts.test(file.name)) {
            setUploadError(t('dash.subtitleFileError'));
            return;
        }
        if (credentialStatus !== null && !textModelKeyConfigured({credentialStatus, settings: loadSettings()})) {
            setUploadError(subtitleKeyText);
            return;
        }
        setUploadError(null);
        setProcessingResult(null);
        setLastResult(null);
        setLastSourceFile(null);

        const ac = new AbortController();
        abortRef.current = ac;
        const taskId = createTaskId();
        const settings = loadSettings();
        const fileSizeMb = Math.round(file.size / 1024 / 1024 * 1000) / 1000;
        setSubmitting(true);
        setCurrentJob({
            taskId,
            fileName: file.name,
            stage: 'summary',
            progress: 20,
            startedAt: Date.now(),
            sourceType: 'transcript_file',
            fileSizeMb,
            skipSummary: false,
            exportToLark: false,
            noteMode: settings.noteMode || 'auto',
        });
        try {
            const result = await summarizeTranscriptFile(file, {taskId, ...buildAiOptions(settings), skipSummary: false}, ac.signal);
            settleResult(result, {taskId, fileName: file.name, source: 'transcript_file'});
            navigate('/editor');
        } catch (err) {
            if (err.name !== 'AbortError') {
                reportBackendError?.(err);
                setUploadError(friendlyTaskError(err?.message || 'Summary generation failed.', lang));
                addToHistory({id: Date.now(), taskId, name: file.name, timestamp: Date.now(), durationMin: 0, status: 'failed'});
            }
            setCurrentJob(null);
        } finally {
            if (abortRef.current === ac) abortRef.current = null;
            setSubmitting(false);
        }
    };

    const handleDrop = (e) => {
        e.preventDefault();
        const files = Array.from(e.dataTransfer.files || []);
        if (files.length === 0) return;
        if (mode === 'subtitle' || (files.length === 1 && transcriptExts.test(files[0].name))) {
            handleSubtitleSelect(files);
        } else {
            handleDroppedMedia(files);
        }
    };

    const handleCancel = async () => {
        const confirmText = lang === 'zh'
            ? '取消当前正在上传或处理的任务？任务会中止，完整结果不会生成；如果任务已经进入队列，可到处理记录查看已取消记录。这不是删除历史记录。'
            : 'Cancel the current upload or processing task? The task will stop and a complete result will not be created. If it already entered the queue, you can check the cancelled record in Processing records. This does not delete history.';
        if (!window.confirm(confirmText)) return;
        abortPendingUpload();
        abortRef.current = null;
        if (currentJob?.taskId) {
            try { await cancelJob(currentJob.taskId, {sttProvider: currentJob.sttProvider}); } catch (err) { setUploadError(friendlyTaskError(err.message || String(err), lang)); }
        }
        setCurrentJob(null);
        setSubmitting(false);
    };

    const recent = history.slice(0, 6);
    // The badge comes from the task itself where the page has it, so a card
    // says when the note or the cut did not happen; a finished task with
    // nothing to add carries no badge at all.
    const jobByTaskId = useMemo(() => new Map((tasks || []).map((job) => [String(job?.task_id || ''), job])), [tasks]);
    const recentOutcome = (item) => taskOutcome(
        jobByTaskId.get(String(item.taskId || '')) || {status: item.status === 'processing' ? 'running' : item.status},
        lang,
    );

    const openRecentTask = async (item) => {
        const cachedResult = historyEntryToResult(item);
        const openCachedEditor = () => {
            if (item.status !== 'completed' || !hasTranscriptResult(cachedResult)) return false;
            setLastResult(cachedResult);
            navigate('/editor');
            return true;
        };
        if (openCachedEditor()) return;
        if (!item.taskId) return;
        navigate('/agent', {state: {job: item}});
    };

    return (
        <main className="ml-[var(--sidebar-offset)] min-h-screen bg-[#f8f7fb] text-[#111111] transition-[margin] duration-200 ease-out dark:bg-[#101010] dark:text-white/[0.92]">
            <section className="mx-auto h-dvh max-w-[1280px] overflow-y-auto px-8 py-9 hide-scrollbar">
                <input ref={subtitleInputRef} type="file" accept=".srt,.vtt,.txt,.md,text/plain,text/markdown" onChange={handleSubtitleSelect} className="hidden"/>

                {keySetupOpen && (
                    <NoteKeyOnboarding
                        runtimeConfig={runtimeConfig}
                        onClose={closeKeySetup}
                        onStatusChange={setCredentialStatus}
                    />
                )}

                {showWriterBanner && !keySetupOpen && (
                    <div data-testid="note-writer-banner" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-[16px] border border-[#ecd9a8] bg-[#fff8e6] px-4 py-3 text-sm text-[#5c4a1a] dark:border-[#6b5a2a] dark:bg-[#2a2415] dark:text-[#f0dfb0]">
                        <span>{noteAttention.map((item) => (lang === 'zh' ? item.text.zh : item.text.en)).join(' ')}</span>
                        {noNoteKey && (
                            <span className="flex shrink-0 items-center gap-4">
                                <button type="button" onClick={() => setKeySetupOpen(true)} className="font-extrabold underline">
                                    {lang === 'zh' ? '怎么拿 Key？' : 'How do I get a key?'}
                                </button>
                                <Link to="/settings#notes" className="font-extrabold underline">{lang === 'zh' ? '去设置填写' : 'Add one in Settings'}</Link>
                            </span>
                        )}
                    </div>
                )}

                {subtitleNeedsTextKey && (
                    <div data-testid="subtitle-key-notice" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-[16px] border border-[#ecd9a8] bg-[#fff8e6] px-4 py-3 text-sm text-[#5c4a1a] dark:border-[#6b5a2a] dark:bg-[#2a2415] dark:text-[#f0dfb0]">
                        <span>{lang === 'zh' ? '用字幕生成笔记需要一个文本模型的 Key（例如 DeepSeek）。' : 'Turning subtitles into a note needs a text-model key (for example DeepSeek).'}</span>
                        <Link to="/settings#notes" className="shrink-0 font-extrabold underline">{lang === 'zh' ? '去设置填写' : 'Add one in Settings'}</Link>
                    </div>
                )}

                <div className="mb-7 flex flex-wrap items-center justify-center gap-3">
                    <span className="text-sm font-bold text-[#8a8a8a] dark:text-white/40">{lang === 'zh' ? '目前支持：' : 'Supported:'}</span>
                    {platformItems.map((item) => (
                        <span key={item.label} className="inline-flex items-center gap-2 rounded-full px-3 py-2 text-sm font-bold text-[#666] dark:text-white/55">
                            <span className={`flex size-7 items-center justify-center rounded-[8px] ${item.tone}`}>
                                <SvgIcon name={item.icon} className="size-4"/>
                            </span>
                            {item.label}
                        </span>
                    ))}
                </div>

                <section
                    className="rounded-[24px] border border-[#dedada] bg-white p-8 shadow-[0_26px_70px_-46px_rgba(17,17,17,.5)] dark:border-white/[0.12] dark:bg-[#1d1f22] dark:shadow-none"
                    onDrop={handleDrop}
                    onDragOver={(e) => e.preventDefault()}
                >
                    <div>
                        {/* One switch for the three ways in. It was two stacked
                            switches (视频/字幕, then 链接/本机文件), and the second
                            only meant anything under the first. */}
                        <div className="mb-6 inline-flex max-w-full rounded-[18px] border border-[#dedada] bg-[#f4f3f3] p-1 dark:border-white/[0.12] dark:bg-white/[0.08]" role="tablist" aria-label={lang === 'zh' ? '从哪里来' : 'Source'}>
                            {ENTRY_TABS.map((item) => {
                                const active = entry === item.id;
                                return (
                                    <button
                                        key={item.id}
                                        type="button"
                                        role="tab"
                                        aria-selected={active}
                                        onClick={() => chooseEntry(item.id)}
                                        className={`h-10 whitespace-nowrap rounded-[14px] px-4 text-sm font-extrabold transition ${active ? 'bg-white text-[#111111] shadow-sm dark:bg-white/[0.16] dark:text-white' : 'text-[#777] hover:text-[#111111] dark:text-white/55 dark:hover:text-white'}`}
                                    >
                                        {lang === 'zh' ? item.zh : item.en}
                                    </button>
                                );
                            })}
                        </div>

                        {mode === 'media' && (
                            <div className="space-y-5">
                                {sourceMode === 'link' ? (
                                    <div>
                                        <textarea
                                            aria-label={lang === 'zh' ? '视频链接' : 'Video link'}
                                            value={videoLinkInput}
                                            onChange={(e) => setVideoLinkInput(e.target.value)}
                                            className="min-h-[116px] w-full resize-none rounded-[18px] border border-[#dedada] bg-[#fbfbfb] px-5 py-4 text-[15px] font-semibold text-[#111111] outline-none placeholder:text-[#aaa] focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:placeholder:text-white/30 dark:focus:border-white/[0.4]"
                                            placeholder={lang === 'zh' ? '粘贴一个抖音、Bilibili 或 YouTube 的视频链接（分享文本也行），或 .mp4 直链' : 'Paste one Douyin, Bilibili, or YouTube video link (share text works too), or a direct .mp4 link'}
                                        />
                                    </div>
                                ) : (
                                    // Clicking opens the system dialog rather than
                                    // the browser's, so the service gets the file's
                                    // real path instead of an uploaded copy.
                                    <button
                                        type="button"
                                        onClick={handleChooseFromComputer}
                                        disabled={submitting || choosing || backendDown}
                                        className="flex min-h-[180px] w-full flex-col items-center justify-center rounded-[20px] py-8 border border-dashed border-[#cfcaca] bg-[#fbfbfb] px-6 text-center transition hover:border-[#111111] hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.16] dark:bg-white/[0.04] dark:hover:border-white/[0.4] dark:hover:bg-white/[0.08]"
                                    >
                                        <SvgIcon name={choosing ? 'sync' : 'upload-file'} className={`mb-3 size-8 text-[#111111] dark:text-white ${choosing ? 'animate-spin' : ''}`}/>
                                        <span className="text-lg font-extrabold">{lang === 'zh' ? '拖放或选择音视频文件' : 'Drop or choose media files'}</span>
                                        <span className="mt-2 text-sm font-semibold text-[#777] dark:text-white/55">MP4 / MOV / MP3 / WAV / M4A</span>
                                        {/* Said once, where the choice is made: the owner
                                            otherwise does not know a folder appears. */}
                                        <span className="mt-3 text-[13px] font-semibold text-[#8a8a8a] dark:text-white/45">
                                            {lang === 'zh'
                                                ? '产出放在原片旁边的同名文件夹，原片不动。'
                                                : 'Outputs go in a folder next to the recording; the original is left untouched.'}
                                        </span>
                                    </button>
                                )}

                                {/* The folder entry, next to the file one rather
                                    than inside it: choosing one recording and
                                    choosing a morning's worth are different
                                    decisions, and only the second one needs the
                                    count confirmed. */}
                                {sourceMode !== 'link' && (
                                    <button
                                        type="button"
                                        onClick={handleChooseFolder}
                                        disabled={submitting || choosing || backendDown}
                                        className="mt-3 inline-flex h-12 w-full items-center justify-center gap-2 rounded-[16px] border border-[#dedada] bg-white px-5 text-sm font-extrabold text-[#111111] transition hover:bg-[#f4f3f3] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.12]"
                                    >
                                        <SvgIcon name="folder" className="size-4"/>
                                        {lang === 'zh' ? '处理整个文件夹' : 'Process a whole folder'}
                                    </button>
                                )}
                            </div>
                        )}

                        {mode === 'subtitle' && (
                            <button
                                type="button"
                                onClick={() => subtitleInputRef.current?.click()}
                                disabled={backendDown || subtitleNeedsTextKey}
                                className="flex min-h-[220px] w-full flex-col items-center justify-center rounded-[20px] border border-dashed border-[#cfcaca] bg-[#fbfbfb] px-6 text-center transition hover:border-[#111111] hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.16] dark:bg-white/[0.04] dark:hover:border-white/[0.4] dark:hover:bg-white/[0.08]"
                            >
                                <SvgIcon name="subtitles" className="mb-3 size-8 text-[#111111] dark:text-white"/>
                                <span className="text-lg font-extrabold">{lang === 'zh' ? '拖放或选择字幕 / 文本文件' : 'Drop or choose subtitle files'}</span>
                                <span className="mt-2 text-sm font-semibold text-[#777] dark:text-white/55">SRT / VTT / TXT / MD</span>
                            </button>
                        )}

                        <div className="mt-6 flex flex-col gap-3 md:flex-row md:items-center md:justify-end">
                            {mode === 'subtitle' && (
                                <button type="button" onClick={() => subtitleInputRef.current?.click()} disabled={submitting || backendDown || subtitleNeedsTextKey} className="inline-flex h-12 items-center justify-center gap-2 rounded-[16px] border border-[#dedada] bg-white px-5 text-sm font-extrabold text-[#111111] hover:bg-[#f4f3f3] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.12]">
                                    <SvgIcon name="subtitles" className="size-4"/>
                                    {lang === 'zh' ? '选择字幕文件' : 'Choose subtitle file'}
                                </button>
                            )}
                            {mode === 'media' && sourceMode === 'link' && (
                                <button type="button" onClick={handleVideoLinkSubmit} disabled={submitting || backendDown} data-testid="submit-video-link" className="inline-flex h-12 items-center justify-center gap-2 rounded-[16px] border border-[#dedada] bg-white px-7 text-sm font-extrabold text-[#111111] hover:bg-[#f4f3f3] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.12]">
                                    {submitting ? <SvgIcon name="sync" className="size-4 animate-spin"/> : <SvgIcon name="arrow-right" className="size-4"/>}
                                    {lang === 'zh' ? '开始生成笔记' : 'Start'}
                                </button>
                            )}
                        </div>

                        {/* Only when it is true, and only about the files it is true
                            of. A drop this machine could place is silent, because
                            nothing about it is different. */}
                        {droppedWithoutPath > 0 && (
                            <div className="mt-5 flex items-start gap-2 rounded-[16px] border border-[#f5c86b] bg-[#fffaf0] px-4 py-3 text-sm font-semibold leading-relaxed text-[#8a5a00] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022]">
                            <SvgIcon name="info" className="mt-0.5 size-4 shrink-0"/>
                            <p>
                                {lang === 'zh'
                                    ? '没认出这个文件在哪，结果只在处理记录里。想放回原文件夹，点上面重新选一次。'
                                    : 'Could not tell where this file lives, so the results are only in the processing records. To have them land in its folder, click above and pick it again.'}
                            </p>
                            </div>
                        )}
                        {uploadError && <div className="mt-5 rounded-[16px] border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700 dark:border-red-400/30 dark:bg-red-400/10 dark:text-red-300">{uploadError}</div>}
                        {processingResult && <div className="mt-5 rounded-[16px] border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800 dark:border-emerald-400/30 dark:bg-emerald-400/10 dark:text-emerald-300">{t('dash.done')} <button type="button" onClick={() => navigate('/editor')} className="underline hover:no-underline">{t('dash.viewEditor')}</button></div>}
                    </div>
                </section>

                {currentJob && currentJob.stage !== 'done' && currentJob.sourceType !== 'video_link' && (
                    <section className="mt-6 rounded-[22px] border border-[#dedada] bg-white p-5 dark:border-white/[0.12] dark:bg-white/[0.06]">
                        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                            <div className="min-w-0">
                                <p className="text-xs font-extrabold text-[#777] dark:text-white/55">{lang === 'zh' ? '当前任务' : 'Active task'}</p>
                                <h2 className="mt-1 truncate text-xl font-extrabold">{currentJob.fileName}</h2>
                                <p className="mt-1 text-sm font-semibold text-[#666] dark:text-white/55">{t(`status.${currentJob.stage}`)}</p>
                            </div>
                            <button type="button" onClick={handleCancel} className="inline-flex h-10 items-center justify-center gap-2 rounded-[14px] border border-red-200 bg-red-50 px-3 text-xs font-extrabold text-red-600 hover:bg-red-100 dark:border-red-400/30 dark:bg-red-400/10 dark:text-red-300 dark:hover:bg-red-400/20">
                                <XCircle className="size-4" strokeWidth={2.15}/>
                                {t('dash.cancel')}
                            </button>
                        </div>
                        <div className="mt-4 flex items-center gap-3">
                            <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-[#efeeee] dark:bg-white/[0.12]">
                                <div className="h-full rounded-full bg-[#111111] transition-all duration-700 dark:bg-white" style={{width: `${Math.max(0, Math.min(100, Number(currentJob?.progress) || 0))}%`}}/>
                            </div>
                            <span className="shrink-0 text-sm font-extrabold tabular-nums">{Math.round(Math.max(0, Math.min(100, Number(currentJob?.progress) || 0)))}%</span>
                        </div>
                        <div className="mt-4 grid grid-cols-2 gap-3">
                            <div className="rounded-[16px] bg-[#f4f3f3] p-3 dark:bg-white/[0.08]">
                                <p className="text-[11px] font-bold text-[#777] dark:text-white/55">{t('dash.fileSize')}</p>
                                <p className="mt-1 text-sm font-extrabold">{fmtFileSize(currentJob.fileSizeMb)}</p>
                            </div>
                            <div className="rounded-[16px] bg-[#f4f3f3] p-3 dark:bg-white/[0.08]">
                                <p className="text-[11px] font-bold text-[#777] dark:text-white/55">{lang === 'zh' ? '来源' : 'Source'}</p>
                                <p className="mt-1 truncate text-sm font-extrabold">{(() => { const s = String(currentJob.sourceType || '').toLowerCase(); if (s.includes('audio')) return lang === 'zh' ? '音频' : 'Audio'; if (s.includes('transcript') || s.includes('subtitle')) return lang === 'zh' ? '字幕' : 'Subtitle'; if (s.includes('video') || s === 'queue_upload') return lang === 'zh' ? '视频' : 'Video'; return '-'; })()}</p>
                            </div>
                        </div>
                    </section>
                )}

                <section className="mt-7 rounded-[24px] border border-[#dedada] bg-white p-6 shadow-[0_18px_44px_-38px_rgba(17,17,17,.45)] dark:border-white/[0.12] dark:bg-white/[0.06] dark:shadow-none">
                    <div className="mb-5 flex items-center justify-between gap-4">
                        <div>
                            <h2 className="text-[22px] font-extrabold">{t('dash.recent')}</h2>
                            <p className="mt-1 text-sm font-semibold text-[#777] dark:text-white/55">{lang === 'zh' ? '最近完成和处理中任务会显示在这里。' : 'Recent completed and active tasks appear here.'}</p>
                        </div>
                        <Link to="/agent" className="rounded-full bg-[#efeeee] px-4 py-2 text-xs font-extrabold text-[#111111] hover:bg-[#e8e5e5] dark:bg-white/[0.12] dark:text-white dark:hover:bg-white/[0.18]">{t('dash.viewAll')}</Link>
                    </div>
                    {recent.length === 0 ? (
                        <div className="rounded-[18px] border border-dashed border-[#dedada] bg-[#fbfbfb] px-4 py-12 text-center text-sm font-semibold text-[#999] dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-white/40">
                            {t('dash.noActivity')}
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                            {recent.map((item) => {
                                const outcome = recentOutcome(item);
                                return (
                                    <button key={item.taskId || item.id} type="button" onClick={() => openRecentTask(item)} className="min-w-0 rounded-[18px] bg-[#f4f3f3] p-4 text-left transition hover:bg-[#efeeee] dark:bg-white/[0.08] dark:hover:bg-white/[0.12]">
                                        <div className="mb-2 flex items-center justify-between gap-2">
                                            <h3 className="min-w-0 flex-1 truncate text-sm font-extrabold">{item.name}</h3>
                                            {outcome.tone !== 'done' && (
                                                <span data-testid="recent-badge" className={`inline-flex shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-bold ${RECENT_BADGE_TONE[outcome.tone]}`}>{outcome.label}</span>
                                            )}
                                        </div>
                                        <p className="text-xs font-semibold text-[#777] dark:text-white/55">{timeAgo(item.timestamp, t)}{(item.audioDurationSec || item.durationMin) > 0 && ` · ${fmtDuration(item.audioDurationSec || item.durationMin * 60, lang)}`}</p>
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </section>
            </section>
            {duplicateLink && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6 backdrop-blur-sm">
                    <div role="dialog" aria-modal="true" aria-labelledby="duplicate-link-title" className="w-full max-w-md rounded-[24px] border border-[#e4e0e0] bg-white p-6 shadow-[0_24px_70px_-35px_rgba(17,17,17,.65)] dark:border-white/[0.12] dark:bg-[#151515]">
                        <p id="duplicate-link-title" className="text-sm font-bold leading-relaxed text-[#111111] dark:text-white">
                            {lang === 'zh'
                                ? `这个链接已经处理过了（任务 ${duplicateLink.existingTaskId}）。要再处理一次吗？`
                                : `This link was already processed (task ${duplicateLink.existingTaskId}). Process it again?`}
                        </p>
                        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                            <button type="button" onClick={() => setDuplicateLink(null)} className="rounded-[13px] px-4 py-2 text-sm font-bold text-[#666] hover:bg-[#efeeee] dark:text-white/60 dark:hover:bg-white/[0.08]">
                                {lang === 'zh' ? '取消' : 'Cancel'}
                            </button>
                            <button type="button" onClick={handleProcessLinkAgain} className="rounded-[13px] bg-[#efeeee] px-4 py-2 text-sm font-bold text-[#111111] hover:bg-[#e4e0e0] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]">
                                {lang === 'zh' ? '再处理一次' : 'Process again'}
                            </button>
                            <button type="button" onClick={handleOpenExistingLinkTask} className="rounded-[13px] bg-[#111111] px-4 py-2 text-sm font-bold text-white hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85">
                                {lang === 'zh' ? '打开已有的' : 'Open the existing one'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </main>
    );
};

export default MediaText;
