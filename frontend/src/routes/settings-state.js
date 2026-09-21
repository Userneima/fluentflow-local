import {useEffect, useState} from 'react';
import {
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
    DEFAULT_ANTHROPIC_MODEL,
    aiProviderSecretKey,
    effectiveSttProvider,
    extraLarkExportRouteOptions,
    isLocalLarkExportRoute,
    larkExportRouteFromSettings,
    normalizeAiModel,
    normalizeSttModel,
    useApi,
    useI18n,
    useSettings,
} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';

// Everything the settings page needs to read and write, with no page layout and
// no edition knowledge. Both editions build their own page on top of this, so a
// stored setting, a credential, or the clear-history flow is written once and
// cannot drift between the two.
export const useSettingsPageState = () => {
    const {t, lang} = useI18n();
    const {loadSettings, saveSettings} = useSettings();
    const {clearHistory, history, larkExports, runtimeConfig} = useApp();
    const {getCredentialsStatus, saveCredentials, getSpeakerDiarizationStatus, checkVideoCookies} = useApi();
    const [settings, setSettings] = useState(() => loadSettings());
    const [cleared, setCleared] = useState(false);
    const [clearConfirmOpen, setClearConfirmOpen] = useState(false);
    const [credentialStatus, setCredentialStatus] = useState(null);
    const [diarizationStatus, setDiarizationStatus] = useState(null);
    const [cookieCheck, setCookieCheck] = useState(null);
    const [cookieChecking, setCookieChecking] = useState(false);
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
    }, []);

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

    const requestClearHistory = () => {
        if (history.length === 0) return;
        setClearConfirmOpen(true);
    };

    const confirmClearHistory = () => {
        setClearConfirmOpen(false);
        clearHistory();
        setCleared(true);
        setTimeout(() => setCleared(false), 2000);
    };

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
        anthropic: DEFAULT_ANTHROPIC_MODEL,
    };
    // Looked up rather than chained: a ternary chain routes an unknown provider
    // into its last branch, so adding one used to mean saving it as deepseek.
    const activeAiSecretKey = aiProviderSecretKey(aiProvider);
    const activeAiConfigured = credentialConfigured(credentialStatus, activeAiSecretKey);
    const sttProvider = effectiveSttProvider(settings, runtimeConfig);
    const larkExportRoute = larkExportRouteFromSettings(settings);
    // Extra routes (today: the hosted account-OAuth route) come from the
    // edition policy with their own labels and hint copy.
    const extraLarkRouteOptions = extraLarkExportRouteOptions();
    const selectedExtraLarkRoute = extraLarkRouteOptions.find((option) => option.value === larkExportRoute);
    const larkRouteHint = selectedExtraLarkRoute
        ? (lang === 'zh' ? selectedExtraLarkRoute.hintZh : selectedExtraLarkRoute.hintEn)
        : (isLocalLarkExportRoute(larkExportRoute) ? t('set.larkRouteLocalCliHint') : t('set.larkRouteOpenapiHint'));
    const pyannoteTokenConfigured = !!(credentialStatus?.pyannote_auth_token_configured || diarizationStatus?.auth_configured);

    return {
        t,
        lang,
        runtimeConfig,
        settings,
        updateSettingNow,
        history,
        larkExports,
        // Clear-history flow.
        cleared,
        clearConfirmOpen,
        setClearConfirmOpen,
        requestClearHistory,
        confirmClearHistory,
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
        // Text model and its key.
        aiProvider,
        aiModel,
        aiProviderDefaults,
        activeAiSecretKey,
        activeAiConfigured,
        // Feishu export route.
        larkExportRoute,
        extraLarkRouteOptions,
        larkRouteHint,
        // Speaker diarization model token.
        pyannoteTokenConfigured,
        pyannoteTokenEditing,
        setPyannoteTokenEditing,
    };
};
