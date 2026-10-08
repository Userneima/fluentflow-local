import {useEffect, useRef, useState} from 'react';
import {
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
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
    MIUISTORE_PREF,
    VIDEO_COOKIES_PREF,
    normalizeCookiesBrowser,
    syncVideoLinkPreferences,
} from '../lib/videoLinkPrefs.js';

// Everything the settings page needs to read and write, with no page layout:
// stored settings and credentials.
export const useSettingsPageState = () => {
    const {t, lang} = useI18n();
    const {loadSettings, saveSettings} = useSettings();
    const {larkExports, runtimeConfig} = useApp();
    const {getCredentialsStatus, saveCredentials, getSpeakerDiarizationStatus, checkVideoCookies, getPreferences, savePreferences} = useApi();
    const [settings, setSettings] = useState(() => loadSettings());
    const [credentialStatus, setCredentialStatus] = useState(null);
    const [diarizationStatus, setDiarizationStatus] = useState(null);
    const [cookieCheck, setCookieCheck] = useState(null);
    const [cookieChecking, setCookieChecking] = useState(false);
    // Kept by the service (see lib/videoLinkPrefs.js); on until it says otherwise.
    const [allowMiuistore, setAllowMiuistore] = useState(true);
    // Which video-link choice failed to reach the service, if any.
    const [videoPrefError, setVideoPrefError] = useState('');
    // A choice made on this page before the service answered must not be
    // overwritten by that answer.
    const touchedVideoPrefsRef = useRef({browser: false, miuistore: false});
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
        getCredentialsStatus().then(setCredentialStatus).catch(() => {});
        getSpeakerDiarizationStatus().then(setDiarizationStatus).catch(() => {});
        let active = true;
        if (getPreferences) {
            syncVideoLinkPreferences({
                getPreferences,
                savePreferences,
                loadSettings,
                saveSettings,
                storage: (() => { try { return localStorage; } catch (_) { return null; } })(),
            }).then((prefs) => {
                if (!active || !prefs.reachable) return;
                if (!touchedVideoPrefsRef.current.miuistore) setAllowMiuistore(prefs.allowMiuistore);
                if (touchedVideoPrefsRef.current.browser) return;
                setSettings((s) => (
                    normalizeCookiesBrowser(s.videoCookiesBrowser) === prefs.videoCookiesBrowser
                        ? s
                        : {...s, videoCookiesBrowser: prefs.videoCookiesBrowser}
                ));
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

    const updateAllowMiuistore = async (allowed) => {
        const previous = allowMiuistore;
        touchedVideoPrefsRef.current.miuistore = true;
        setAllowMiuistore(allowed);
        setVideoPrefError('');
        if (!savePreferences) return;
        try {
            await savePreferences({[MIUISTORE_PREF]: allowed});
        } catch (_) {
            setAllowMiuistore(previous);
            setVideoPrefError(MIUISTORE_PREF);
        }
    };

    const updateSettingNow = (patch) => {
        setSettings((s) => {
            const next = {...s, ...patch};
            saveSettings(next);
            return next;
        });
    };

    const saveSecret = async (key) => {
        const value = secretDraft[key];
        if (value === undefined) return;
        setSecretSaving(true);
        setSecretFeedback(null);
        try {
            const next = await saveCredentials({[key]: value});
            const fresh = await getCredentialsStatus().catch(() => next);
            const statusKey = credentialStatusKey(key);
            if (value && statusKey) {
                fresh[statusKey] = true;
                if (key === 'dashscope_api_key' || key === 'qwen_api_key') {
                    fresh.qwen_api_key_configured = true;
                    fresh.dashscope_api_key_configured = true;
                }
            }
            setCredentialStatus(fresh);
            if (key === 'pyannote_auth_token') {
                const status = await getSpeakerDiarizationStatus();
                setDiarizationStatus(status);
                setPyannoteTokenEditing(false);
            }
            setSecretDraft((draft) => ({...draft, [key]: ''}));
            setSecretFeedback({key, ok: true});
        } catch (err) {
            setSecretFeedback({key, ok: false, message: err.message || String(err)});
        } finally {
            setSecretSaving(false);
        }
    };

    const secretStatusText = (configured) => (
        configured ? (lang === 'zh' ? '已配置' : 'Configured') : (lang === 'zh' ? '未配置' : 'Not configured')
    );

    const secretRetentionText = (configured) => (
        configured
            ? (lang === 'zh' ? '已配置，留空则保留' : 'Configured. Leave blank to keep it.')
            : (lang === 'zh' ? '未配置' : 'Not configured')
    );

    const secretInputPlaceholder = (configured) => (
        configured
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

    const aiProvider = settings.aiProvider || 'deepseek';
    const aiModel = normalizeAiModel(aiProvider, settings.aiModel);
    const aiProviderDefaults = {
        deepseek: DEFAULT_DEEPSEEK_MODEL,
        openai: DEFAULT_OPENAI_MODEL,
        qwen: DEFAULT_QWEN_MODEL,
    };
    const activeAiSecretKey = aiProvider === 'openai' ? 'openai_api_key' : (aiProvider === 'qwen' ? 'dashscope_api_key' : 'deepseek_api_key');
    const activeAiConfigured = aiProvider === 'openai'
        ? credentialStatus?.openai_api_key_configured
        : (aiProvider === 'qwen' ? credentialConfigured(credentialStatus, 'dashscope_api_key') : credentialStatus?.deepseek_api_key_configured);
    const sttProvider = effectiveSttProvider(settings, runtimeConfig);
    const larkExportRoute = larkExportRouteFromSettings(settings);
    const larkRouteHint = isLocalLarkExportRoute(larkExportRoute)
        ? t('set.larkRouteLocalCliHint')
        : (larkExportRoute === LARK_EXPORT_ROUTE_AUTO ? t('set.larkRouteAutoHint') : t('set.larkRouteOpenapiHint'));
    const pyannoteTokenConfigured = !!(credentialStatus?.pyannote_auth_token_configured || diarizationStatus?.auth_configured);

    return {
        t,
        lang,
        runtimeConfig,
        settings,
        updateSettingNow,
        larkExports,
        // Credentials.
        credentialStatus,
        credentialConfigured,
        secretDraft,
        setSecretDraft,
        saveSecret,
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
        allowMiuistore,
        updateAllowMiuistore,
        videoPrefError,
        // Text model and its key.
        aiProvider,
        aiModel,
        aiProviderDefaults,
        activeAiSecretKey,
        activeAiConfigured,
        // Feishu export route.
        larkExportRoute,
        larkRouteHint,
        // Speaker diarization model token.
        pyannoteTokenConfigured,
        pyannoteTokenEditing,
        setPyannoteTokenEditing,
    };
};
