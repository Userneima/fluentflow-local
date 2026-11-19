// Which file the editor's player plays, moved out of editor.jsx: the cut
// file the transcript belongs to, else the recording or its saved audio, read
// straight from the service; or a file the reader picked on this machine. The
// player itself and its clock are editor-playback.js.
import {useCallback, useEffect, useRef, useState} from 'react';
import {API_BASE, apiFetch} from '../app/shared.jsx';
import {probeMedia} from '../lib/probeMedia.js';
import {isAutoCutFlow, isLikelyVideoFile, playbackMediaChoice} from './editor-helpers.js';

const jobSourceUrl = (taskId) => `${API_BASE}/jobs/${encodeURIComponent(taskId)}/source`;
const jobArtifactUrl = (taskId, kind) => `${API_BASE}/jobs/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(kind)}`;

export const useMediaSource = ({result, matchedLocalSourceFile, lang}) => {
    const mediaObjectUrlRef = useRef('');
    // Effects keyed on a derived string (mediaSourceKey) read the latest
    // result through these refs.
    const resultRef = useRef(result);
    resultRef.current = result;
    const matchedLocalSourceFileRef = useRef(matchedLocalSourceFile);
    matchedLocalSourceFileRef.current = matchedLocalSourceFile;
    const langRef = useRef(lang);
    langRef.current = lang;
    // Which file plays is part of the key: a result opened from recent activity
    // is a light copy without the cut file's record, and when the full copy
    // arrives the player must move to the cut file the transcript belongs to.
    const mediaSourceKey = result
        ? [
            result.task_id || result.filename || 'current_result',
            playbackMediaChoice(result)?.kind || 'no-preferred-media',
            result.artifacts?.playback_audio?.filename || (result.playback_audio_available ? 'playback-audio' : 'no-playback-audio'),
            result.source_file_available ? 'stored' : 'unstored',
            matchedLocalSourceFile ? `${matchedLocalSourceFile.name}:${matchedLocalSourceFile.size}:${matchedLocalSourceFile.lastModified || 0}` : 'no-local-file',
        ].join(':')
        : 'empty_media_source';
    const [mediaUrl, setMediaUrl] = useState('');
    const [mediaKind, setMediaKind] = useState('audio');
    const [mediaLoading, setMediaLoading] = useState(false);
    const [mediaError, setMediaError] = useState('');

    const replaceMediaUrl = useCallback((nextUrl = '') => {
        const previousUrl = mediaObjectUrlRef.current;
        if (previousUrl && previousUrl !== nextUrl) URL.revokeObjectURL(previousUrl);
        mediaObjectUrlRef.current = nextUrl;
        setMediaUrl(nextUrl);
    }, []);

    const loadMediaFile = useCallback((file) => {
        if (!file) return;
        const url = URL.createObjectURL(file);
        setMediaKind(isLikelyVideoFile(file) ? 'video' : 'audio');
        replaceMediaUrl(url);
        setMediaError('');
        setMediaLoading(false);
    }, [replaceMediaUrl]);

    // The player reads the task's media straight from the service, which answers
    // range requests, so playback starts at once and only what is played is read.
    // Fetching the whole file into the browser first meant waiting for, and
    // holding in memory, a recording of several gigabytes before the first second
    // played. One byte is asked for first so a missing file still falls through
    // to the next choice the way a failed fetch did.
    const openJobMedia = useCallback(async (url, name) => {
        const probe = await probeMedia(apiFetch, url).catch(() => null);
        if (!probe?.ok) throw new Error(lang === 'zh' ? '读不到这个文件' : 'File not found');
        const type = probe.headers.get('content-type') || '';
        try { await probe.body?.cancel?.(); } catch (_) { /* nothing left to read */ }
        const kind = type.startsWith('video/') || isLikelyVideoFile(name) ? 'video' : 'audio';
        return () => {
            setMediaKind(kind);
            replaceMediaUrl(url);
            setMediaError('');
            setMediaLoading(false);
        };
    }, [replaceMediaUrl, lang]);

    useEffect(() => {
        let cancelled = false;
        const result = resultRef.current;
        const matchedLocalSourceFile = matchedLocalSourceFileRef.current;
        const lang = langRef.current;
        replaceMediaUrl('');
        setMediaError('');
        setMediaLoading(false);
        setMediaKind('audio');
        if (!result) return () => { cancelled = true; };
        if (matchedLocalSourceFile) {
            loadMediaFile(matchedLocalSourceFile);
            return () => { cancelled = true; };
        }
        // The file this transcript belongs to, which for a task that was cut
        // before transcription is the shortened one. Loading the recording instead
        // would leave every seek and every highlighted line drifting further out
        // the longer it plays, with nothing on screen saying why.
        const preferred = playbackMediaChoice(result);
        // Same rule one step earlier: if this task's material is the cut file and
        // there is no cut file to load, nothing else may take its place — not the
        // recording, not the saved audio. The bar at the top says why.
        if (isAutoCutFlow(result) && !preferred?.isCut) {
            setMediaError(lang === 'zh' ? '剪后文件读不到了' : 'The cut file cannot be read');
            setMediaLoading(false);
            return () => { cancelled = true; };
        }
        if (preferred?.isCut) {
            setMediaLoading(true);
            openJobMedia(jobArtifactUrl(result.task_id, preferred.kind), preferred.filename)
                .then((play) => { if (!cancelled) play(); })
                .catch((cutErr) => {
                    if (cancelled) return;
                    // No falling back to the recording when the transcript came from
                    // the cut file. The original is longer, so every line and every
                    // seek would point at the wrong moment while the page looked
                    // perfectly normal. Say it is unavailable instead.
                    if (!isAutoCutFlow(result) && result.source_file_available) {
                        openJobMedia(jobSourceUrl(result.task_id), result.filename || 'source')
                            .then((play) => { if (!cancelled) play(); })
                            .catch((sourceErr) => {
                                if (!cancelled) {
                                    setMediaError(sourceErr.message || cutErr.message || 'Source file unavailable');
                                    setMediaLoading(false);
                                }
                            });
                        return;
                    }
                    setMediaError(cutErr.message || 'Cut file unavailable');
                    setMediaLoading(false);
                });
            return () => { cancelled = true; };
        }
        if (result.task_id && result.source_file_available) {
            setMediaLoading(true);
            openJobMedia(jobSourceUrl(result.task_id), result.filename || 'source')
                .then((play) => { if (!cancelled) play(); })
                .catch((sourceErr) => {
                    if (!cancelled && result.artifacts?.playback_audio) {
                        const playbackArtifact = result.artifacts.playback_audio;
                        openJobMedia(jobArtifactUrl(result.task_id, 'playback_audio'), playbackArtifact.filename || `${result.filename || 'source'}_audio.mp3`)
                            .then((play) => { if (!cancelled) play(); })
                            .catch((err) => {
                                if (!cancelled) {
                                    setMediaError(err.message || sourceErr.message || 'Source file unavailable');
                                    setMediaLoading(false);
                                }
                            });
                        return;
                    }
                    if (!cancelled) {
                        setMediaError(sourceErr.message || 'Source file unavailable');
                        setMediaLoading(false);
                    }
                });
            return () => { cancelled = true; };
        }
        const playbackArtifact = result.artifacts?.playback_audio;
        if (result.task_id && playbackArtifact) {
            setMediaLoading(true);
            openJobMedia(jobArtifactUrl(result.task_id, 'playback_audio'), playbackArtifact.filename || `${result.filename || 'source'}_audio.mp3`)
                .then((play) => { if (!cancelled) play(); })
                .catch((err) => {
                    if (!cancelled && result.source_file_available) {
                        openJobMedia(jobSourceUrl(result.task_id), result.filename || 'source')
                            .then((play) => { if (!cancelled) play(); })
                            .catch((sourceErr) => {
                                if (!cancelled) {
                                    setMediaError(sourceErr.message || err.message || 'Audio file unavailable');
                                    setMediaLoading(false);
                                }
                            });
                        return;
                    }
                    if (!cancelled) {
                        setMediaError(err.message || 'Audio file unavailable');
                        setMediaLoading(false);
                    }
            });
            return () => { cancelled = true; };
        }
        return () => { cancelled = true; };
    }, [mediaSourceKey, replaceMediaUrl, loadMediaFile, openJobMedia]);

    return {mediaUrl, mediaKind, mediaLoading, mediaError, setMediaError, loadMediaFile, mediaObjectUrlRef};
};
