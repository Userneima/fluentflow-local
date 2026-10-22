import {TEXT_PROVIDERS, normalizeTextProvider, textProvider, textProviderKeyConfigured} from '../lib/textProviders.js';
import {useEffect, useRef, useState} from 'react';
import {
    effectiveSttProvider,
    LARK_EXPORT_ROUTE_AUTO,
    isLocalLarkExportRoute,
    larkExportRouteFromSettings,
    normalizeAiModel,
    normalizeSttModel,
    useApi,
    useI18n,
    useSettings,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import {
    VIDEO_COOKIES_PREF,
    normalizeCookiesBrowser,
    syncVideoLinkPreferences,
} from '../lib/videoLinkPrefs.js';
import {
    LARK_FOLDER_PREF,
    LARK_ROUTE_PREF,
    larkRouteSettingsPatch,
    syncLarkExportPreferences,
} from '../lib/larkExportPrefs.js';

// Everything the settings page needs to read and write, with no page layout:
// stored settings and credentials.
export const useSettingsPageState = () => {
    const {t, lang} = useI18n();
    const {loadSettings, saveSettings} = useSettings();
    const {larkExports, runtimeConfig} = useApp();
    const {getCredentialsStatus, saveCredentials, getSpeakerDiarizationStatus, getWorkflowStatus, checkVideoCookies, getPreferences, savePreferences} = useApi();
    const [settings, setSettings] = useState(() => loadSettings());
    const [credentialStatus, setCredentialStatus] = useState(null);
    // Until the service answers, nothing on the page may say a key is or is
    // not configured: 'loading' | 'ready' | 'error'.
    const [credentialLoad, setCredentialLoad] = useState('loading');
    const [diarizationStatus, setDiarizationStatus] = useState(null);
    const [cookieCheck, setCookieCheck] = useState(null);
    const [cookieChecking, setCookieChecking] = useState(false);
    // Which service-kept choice failed to save, if any (its preference name).
    const [videoPrefError, setVideoPrefError] = useState('');
    // How much of the processor a cut may take; kept only by the service.
    const [cpuUsage, setCpuUsage] = useState('balanced');
    const touchedCpuUsageRef = useRef(false);
    // What a cut favours; kept only by the service.
    const [processingPreset, setProcessingPreset] = useState('quality');
    const touchedPresetRef = useRef(false);
    // A choice made on this page before the service answered must not be
    // overwritten by that answer.
    const touchedVideoPrefsRef = useRef({browser: false, lark: false});
    // The folder value last saved to the service, so leaving the field
    // without changing it does not save again.
    const savedLarkFolderRef = useRef(null);
    const [secretDraft, setSecretDraft] = useState({});
    const [pyannoteTokenEditing, setPyannoteTokenEditing] = useState(false);
    const [secretSaving, setSecretSaving] = useState(false);
    const [secretFeedback, setSecretFeedback] = useState(null);

    const credentialConfigured = (status, key) => {
        if (key === 'dashscope_api_key' || key === 'qwen_api_key') {
            return !!(status?.dashscope_api_key_configured || status?.qwen_api_key_configured);
        }
        return !!status?.[`${key}_configured`];
    };

    const credentialStatusKey = (key) => (
        key === 'dashscope_api_key' || key === 'qwen_api_key'
            ? 'dashscope_api_key_configured'
            : `${key}_configured`
    );

    useEffect(() => {
        const normalizedSttModel = normalizeSttModel(settings.sttModel);
        if (settings.sttModel !== normalizedSttModel) {
            const next = {...settings, sttModel: normalizedSttModel};
            setSettings(next);
            saveSettings(next);
        }
        getCredentialsStatus()
            .then((status) => { setCredentialStatus(status || {}); setCredentialLoad('ready'); })
            .catch(() => setCredentialLoad('error'));
        getSpeakerDiarizationStatus().then(setDiarizationStatus).catch(() => {});
        let active = true;
        if (getPreferences) {
            getPreferences().then((prefs) => {
                if (!active || touchedCpuUsageRef.current) return;
                const stored = String(prefs?.cpu_usage || '');
                if (['light', 'balanced', 'full'].includes(stored)) setCpuUsage(stored);
            }).catch(() => {});
            getPreferences().then((prefs) => {
                if (!active || touchedPresetRef.current) return;
                const stored = String(prefs?.processing_preset || '');
                if (['quality', 'speed', 'size'].includes(stored)) setProcessingPreset(stored);
            }).catch(() => {});
            const sync = {
                getPreferences,
                savePreferences,
                loadSettings,
                saveSettings,
                storage: (() => { try { return localStorage; } catch (_) { return null; } })(),
            };
            syncVideoLinkPreferences(sync).then((prefs) => {
                if (!active || !prefs.reachable) return;
                if (touchedVideoPrefsRef.current.browser) return;
                setSettings((s) => (
                    normalizeCookiesBrowser(s.videoCookiesBrowser) === prefs.videoCookiesBrowser
                        ? s
                        : {...s, videoCookiesBrowser: prefs.videoCookiesBrowser}
                ));
            }).then(() => syncLarkExportPreferences(sync)).then((lark) => {
                if (!active || !lark?.reachable) return;
                savedLarkFolderRef.current = lark.folder;
                if (touchedVideoPrefsRef.current.lark) return;
                setSettings((s) => ({...s, ...larkRouteSettingsPatch(lark.route), larkFolder: lark.folder}));
            });
        }
        return () => { active = false; };
    }, []);

    // Saved in the service as well as here, so a link an AI tool submits reads
    // the same browser's login.
    const updateVideoCookiesBrowser = async (value) => {
        const browser = normalizeCookiesBrowser(value);
        touchedVideoPrefsRef.current.browser = true;
        updateSettingNow({videoCookiesBrowser: browser});
        setCookieCheck(null);
        setVideoPrefError('');
        if (!savePreferences) return;
        try {
            await savePreferences({[VIDEO_COOKIES_PREF]: browser});
        } catch (_) {
            setVideoPrefError(VIDEO_COOKIES_PREF);
        }
    };

    // The Feishu route and folder are saved in the service as well as here, so
    // an export an AI tool asks for goes the same way (lib/larkExportPrefs.js).
    const updateLarkExportRoute = async (route) => {
        const patch = larkRouteSettingsPatch(route);
        touchedVideoPrefsRef.current.lark = true;
        updateSettingNow(patch);
        setVideoPrefError('');
        if (!savePreferences) return;
        try {
            await savePreferences({[LARK_ROUTE_PREF]: patch.larkExportRoute});
        } catch (_) {
            setVideoPrefError(LARK_ROUTE_PREF);
        }
    };

    const updateCpuUsage = async (value) => {
        touchedCpuUsageRef.current = true;
        setCpuUsage(value);
        setVideoPrefError('');
        if (!savePreferences) return;
        try {
            await savePreferences({cpu_usage: value});
        } catch (_) {
            setVideoPrefError('cpu_usage');
        }
    };

    const updateProcessingPreset = async (value) => {
        touchedPresetRef.current = true;
        setProcessingPreset(value);
        setVideoPrefError('');
        if (!savePreferences) return;
        try {
            await savePreferences({processing_preset: value});
        } catch (_) {
            setVideoPrefError('processing_preset');
        }
    };

    // Typed or pasted into the field: kept here at once, sent to the service
    // when the field is left, not on every keystroke.
    const updateLarkFolder = (value) => {
        touchedVideoPrefsRef.current.lark = true;
        updateSettingNow({larkFolder: String(value ?? '').trim()});
    };
    const commitLarkFolder = async () => {
        const folder = String(settings.larkFolder ?? '').trim();
        if (!savePreferences || savedLarkFolderRef.current === folder) return;
        setVideoPrefError('');
        try {
            await savePreferences({[LARK_FOLDER_PREF]: folder});
            savedLarkFolderRef.current = folder;
        } catch (_) {
            setVideoPrefError(LARK_FOLDER_PREF);
        }
    };

    const updateSettingNow = (patch) => {
        setSettings((s) => {
            const next = {...s, ...patch};
            saveSettings(next);
            return next;
        });
    };

    // Saves every field of one group (`group` names it for the feedback line)
    // that has something typed in, in one request, and only when asked: a key
    // half pasted is never sent because the cursor left the field.
    const saveSecrets = async (group, keys) => {
        const payload = Object.fromEntries(
            keys.map((key) => [key, String(secretDraft[key] ?? '').trim()]).filter(([, value]) => value),
        );
        const names = Object.keys(payload);
        if (names.length === 0) return;
        setSecretSaving(true);
        setSecretFeedback(null);
        try {
            const next = await saveCredentials(payload);
            const fresh = {...(await getCredentialsStatus().catch(() => next))};
            for (const key of names) {
                fresh[credentialStatusKey(key)] = true;
                if (key === 'dashscope_api_key' || key === 'qwen_api_key') {
                    fresh.qwen_api_key_configured = true;
                    fresh.dashscope_api_key_configured = true;
                }
            }
            setCredentialStatus(fresh);
            setCredentialLoad('ready');
            if (names.includes('pyannote_auth_token')) {
                const status = await getSpeakerDiarizationStatus();
                setDiarizationStatus(status);
                setPyannoteTokenEditing(false);
            }
            setSecretDraft((draft) => ({...draft, ...Object.fromEntries(names.map((key) => [key, '']))}));
            setSecretFeedback({key: group, ok: true});
        } catch (err) {
            setSecretFeedback({key: group, ok: false, message: err.message || String(err)});
        } finally {
            setSecretSaving(false);
        }
    };

    // Whether any of these fields has something typed in that is not saved.
    const secretsPending = (keys) => keys.some((key) => String(secretDraft[key] ?? '').trim());

    const loadingText = lang === 'zh' ? '正在读取…' : 'Loading…';

    const secretStatusText = (configured) => (
        credentialLoad !== 'ready' ? loadingText : configured ? (lang === 'zh' ? '已配置' : 'Configured') : (lang === 'zh' ? '未配置' : 'Not configured')
    );

    const secretRetentionText = (configured) => (
        credentialLoad !== 'ready' ? loadingText : configured
            ? (lang === 'zh' ? '已配置，留空则保留' : 'Configured. Leave blank to keep it.')
            : (lang === 'zh' ? '未配置' : 'Not configured')
    );

    const secretInputPlaceholder = (configured) => (
        credentialLoad !== 'ready' ? loadingText : configured
            ? (lang === 'zh' ? '已配置，输入新 Key 可替换' : 'Configured. Enter a new key to replace it.')
            : (lang === 'zh' ? '粘贴 API Key' : 'Paste API key')
    );

    const runCookieCheck = async () => {
        setCookieChecking(true);
        setCookieCheck(null);
        try {
            setCookieCheck(await checkVideoCookies(settings.videoCookiesBrowser));
        } catch (err) {
            setCookieCheck({ok: false, message: err.message || String(err)});
        } finally {
            setCookieChecking(false);
        }
    };

    const aiProvider = normalizeTextProvider(settings.aiProvider);
    // Who does each step now, from the service (decision 6.24). Read again
    // whenever something on this page that changes the answer changes, and
    // every 10 seconds for what changes elsewhere (an agent connecting, a
    // Claude login expiring).
    const [workflow, setWorkflow] = useState(null);
    const [workflowError, setWorkflowError] = useState(false);
    const workflowChoices = JSON.stringify({
        ai_provider: aiProvider,
        ai_model: settings.aiModel || '',
        default_source: settings.defaultSourceMode || '',
        speaker_diarization: settings.speakerDiarization ? '1' : '',
        voice_enhance: settings.voiceEnhance ? '1' : '',
        skip_ai_summary: settings.skipAiSummary ? '1' : '',
        export_to_lark: settings.exportToLark ? '1' : '',
    });
    const workflowInputs = JSON.stringify([workflowChoices, credentialStatus, diarizationStatus, cpuUsage, processingPreset, settings.videoCookiesBrowser, settings.larkExportRoute]);
    useEffect(() => {
        if (!getWorkflowStatus) return undefined;
        let live = true;
        const read = () => getWorkflowStatus(JSON.parse(workflowChoices))
            .then((value) => { if (live) { setWorkflow(value); setWorkflowError(false); } })
            .catch(() => { if (live) setWorkflowError(true); });
        read();
        const timer = setInterval(read, 10000);
        return () => { live = false; clearInterval(timer); };
    }, [workflowInputs]); // eslint-disable-line react-hooks/exhaustive-deps
    const aiModel = normalizeAiModel(aiProvider, settings.aiModel);
    // Switching provider starts on that provider's default ('' follows it).
    const aiProviderDefaults = Object.fromEntries(TEXT_PROVIDERS.map((item) => [item.id, '']));
    const activeAiSecretKey = textProvider(aiProvider).secretKey;
    const activeAiConfigured = textProviderKeyConfigured(credentialStatus, aiProvider);
    const sttProvider = effectiveSttProvider(settings, runtimeConfig);
    const larkExportRoute = larkExportRouteFromSettings(settings);
    const larkRouteHint = isLocalLarkExportRoute(larkExportRoute)
        ? t('set.larkRouteLocalCliHint')
        : (larkExportRoute === LARK_EXPORT_ROUTE_AUTO ? t('set.larkRouteAutoHint') : t('set.larkRouteOpenapiHint'));
    const pyannoteTokenConfigured = !!(credentialStatus?.pyannote_auth_token_configured || diarizationStatus?.pyannote?.auth_configured);

    return {
        t,
        lang,
        runtimeConfig,
        settings,
        updateSettingNow,
        larkExports,
        // Who does each step now.
        workflow,
        workflowError,
        // Credentials.
        credentialStatus,
        setCredentialStatus,
        credentialLoad,
        credentialConfigured,
        secretDraft,
        setSecretDraft,
        saveSecrets,
        secretsPending,
        secretSaving,
        secretFeedback,
        secretStatusText,
        secretRetentionText,
        secretInputPlaceholder,
        // Transcription.
        sttProvider,
        diarizationStatus,
        // Video-link login cookies.
        cookieCheck,
        setCookieCheck,
        cookieChecking,
        runCookieCheck,
        updateVideoCookiesBrowser,
        videoPrefError,
        // Text model and its key.
        aiProvider,
        aiModel,
        aiProviderDefaults,
        activeAiSecretKey,
        activeAiConfigured,
        // Processor share for cuts.
        cpuUsage,
        updateCpuUsage,
        processingPreset,
        updateProcessingPreset,
        // Feishu export route and folder.
        larkExportRoute,
        updateLarkExportRoute,
        updateLarkFolder,
        commitLarkFolder,
        larkRouteHint,
        // Speaker diarization model token.
        pyannoteTokenConfigured,
        pyannoteTokenEditing,
        setPyannoteTokenEditing,
    };
};
