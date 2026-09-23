import SvgIcon from './SvgIcon.jsx';
import {
    RouteCard,
    SecretFeedback,
    SettingCheckbox,
    cellBase,
    fieldLabelClass,
    inputClass,
    saveButtonClass,
} from './settingsPrimitives.jsx';
import {
    DEFAULT_QWEN_MODEL,
    LARK_EXPORT_ROUTE_LOCAL_CLI,
    LARK_EXPORT_ROUTE_OPENAPI,
    DEFAULT_DEEPSEEK_MODEL,
    isLocalLarkExportRoute,
    normalizeSourceMode,
    timeAgo,
    useI18n,
} from '../app/shared.jsx';

// The individual settings controls, edition-neutral. Each row takes the page
// state (see routes/settings-state.js) and renders one control; which rows a
// page shows, and in what order, is each edition's own page file. Nothing here
// asks which edition is rendering it.

// `overlays` are the page's fixed dialogs. They render inside this wrapper
// rather than beside it so they keep inheriting the page's own text color.
export const SettingsPageShell = ({banner = null, overlays = null, children}) => {
    const {t, lang} = useI18n();
    return (
        <div className="ml-[var(--sidebar-offset)] min-h-screen bg-[#f8f7fb] pb-8 text-[#111111] transition-[margin] duration-200 ease-out dark:bg-[#101010] dark:text-white/[0.92]">
            <main className="mx-auto h-dvh max-w-[1040px] overflow-y-auto px-8 py-7 hide-scrollbar">
                <header className="mb-6">
                    <h1 className="font-headline text-2xl font-extrabold tracking-tight text-[#111111] dark:text-white">{t('set.title')}</h1>
                    <p className="mt-2 max-w-[62ch] text-sm font-semibold leading-relaxed text-[#676970] dark:text-white/58">
                        {lang === 'zh'
                            ? '这里只维护长期偏好、凭证和本机数据。单次任务判断放在处理记录里解释。'
                            : 'Long-term preferences, credentials, and local data live here. Per-task decisions are explained in processing records.'}
                    </p>
                </header>
                {banner}
                <div className="space-y-5">
                    {children}
                </div>
            </main>
            {overlays}
        </div>
    );
};

export const SpeakerDiarizationRow = ({state, available}) => {
    const {lang} = useI18n();
    const {settings, updateSettingNow} = state;
    return (
        <label htmlFor="settingsSpeakerDiarization" className={`flex items-start justify-between gap-3 ${cellBase} ${available ? 'cursor-pointer hover:bg-[#f4f3f3] dark:hover:bg-white/[0.04]' : 'cursor-not-allowed opacity-60'}`}>
            <span>
                <span className="block text-sm font-bold">{lang === 'zh' ? '区分不同讲话人' : 'Speaker diarization'}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">
                    {lang === 'zh' ? '适合多人访谈或讲座。不可用时会保持关闭。' : 'Useful for interviews or lectures. It stays off when unavailable.'}
                </span>
            </span>
            <SettingCheckbox
                id="settingsSpeakerDiarization"
                checked={!!settings.speakerDiarization && available}
                disabled={!available}
                onChange={e=>updateSettingNow({speakerDiarization:e.target.checked})}
            />
        </label>
    );
};

export const VoiceEnhanceRow = ({state}) => {
    const {lang} = useI18n();
    const {settings, updateSettingNow} = state;
    return (
        <label htmlFor="settingsVoiceEnhance" className={`flex items-start justify-between gap-3 ${cellBase} cursor-pointer hover:bg-[#f4f3f3] dark:hover:bg-white/[0.04]`}>
            <span>
                <span className="block text-sm font-bold">{lang === 'zh' ? '增强人声清晰度' : 'Clearer voice'}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">
                    {lang === 'zh'
                        ? '手机放桌上录的讲座会发闷，开了会另存一份人声更清楚的音频，转写也读它。已经混好的片子别开，会被改坏。'
                        : 'For lectures recorded with a phone on the table, which sound muffled. Saves a clearer copy of the audio and transcribes from it. Leave off for already-mixed material, which it damages.'}
                </span>
            </span>
            <SettingCheckbox
                id="settingsVoiceEnhance"
                checked={!!settings.voiceEnhance}
                onChange={e=>updateSettingNow({voiceEnhance:e.target.checked})}
            />
        </label>
    );
};

export const LocalSttSpeedRow = ({state}) => {
    const {t} = useI18n();
    const {settings, updateSettingNow} = state;
    return (
        <div className={`flex items-center justify-between gap-3 ${cellBase}`}>
            <span className="text-sm font-bold">{t('set.sttSpeed')}</span>
            <select className="h-10 shrink-0 rounded-[12px] border border-[#dedada] bg-[#fbfbfb] px-3 text-sm font-bold text-[#111111] outline-none transition focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white" value={settings.sttSpeed || 'balanced'} onChange={e=>updateSettingNow({sttSpeed:e.target.value})}>
                <option value="fast">{t('set.speedFast')}</option>
                <option value="balanced">{t('set.speedBalanced')}</option>
                <option value="accurate">{t('set.speedAccurate')}</option>
            </select>
        </div>
    );
};

export const VideoCookiesRow = ({state}) => {
    const {lang} = useI18n();
    const {settings, updateSettingNow, cookieCheck, setCookieCheck, cookieChecking, runCookieCheck} = state;
    return (
        <div className={`md:col-span-2 ${cellBase}`}>
            <label className="block text-sm font-bold">{lang === 'zh' ? '视频链接下载登录态' : 'Video link login'}</label>
            <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
                <select
                    className="h-10 w-[220px] rounded-[12px] border border-[#dedada] bg-[#fbfbfb] px-3 text-sm font-bold text-[#111111] outline-none transition focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white"
                    value={settings.videoCookiesBrowser || ''}
                    onChange={e=>{ updateSettingNow({videoCookiesBrowser:e.target.value}); setCookieCheck(null); }}
                >
                    <option value="">{lang === 'zh' ? '关闭（不读取浏览器登录态）' : 'Off (no browser login)'}</option>
                    <option value="chrome">Chrome</option>
                    <option value="edge">Edge</option>
                    <option value="firefox">Firefox</option>
                    <option value="safari">Safari</option>
                    <option value="brave">Brave</option>
                </select>
                {settings.videoCookiesBrowser && (
                    <button
                        type="button"
                        disabled={cookieChecking}
                        onClick={runCookieCheck}
                        className="inline-flex h-10 shrink-0 items-center gap-2 rounded-[12px] border border-[#dedada] bg-white px-4 text-xs font-bold text-[#111111] transition hover:bg-[#f4f3f3] disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]"
                    >
                        {cookieChecking ? (lang === 'zh' ? '检测中…' : 'Checking…') : (lang === 'zh' ? '检测登录态' : 'Check login')}
                    </button>
                )}
                {cookieCheck && (
                    <span className={`text-xs font-semibold ${cookieCheck.ok ? (cookieCheck.bilibili_logged_in ? 'text-emerald-600 dark:text-emerald-300' : 'text-amber-600 dark:text-amber-300') : 'text-red-600 dark:text-red-300'}`}>
                        {cookieCheck.ok
                            ? (cookieCheck.bilibili_logged_in
                                ? (lang === 'zh' ? '已读取到登录态，且已登录 B 站，可下高清。' : 'Cookies read; logged into Bilibili — HD available.')
                                : (lang === 'zh' ? '已读取到浏览器 cookie，但未检测到 B 站登录（B 站最高约 480p）。B 站高清请先在该浏览器登录；YouTube 受限视频不受影响。' : 'Cookies read, but not logged into Bilibili (Bilibili max ~480p). Log into Bilibili for HD; YouTube restricted videos still work.'))
                            : cookieCheck.message}
                    </span>
                )}
            </div>
            <p className="mt-2.5 text-xs leading-relaxed text-on-surface-variant">
                {lang === 'zh'
                    ? '从所选浏览器复用你的登录 cookie，下载需要登录才能看的视频。仅在本机读取、不会上传；B 站高清和 YouTube 受限视频需要它。'
                    : 'Reuse your login cookies from the chosen browser to download videos that need sign-in. Read locally only, never uploaded; needed for Bilibili HD and restricted YouTube videos.'}
            </p>
        </div>
    );
};

// Both ways in stay available whichever is chosen. This only decides which one
// is already selected, so that someone whose material is always files on this
// machine does not click past a link box every single time.
export const DefaultSourceRow = ({state}) => {
    const {lang} = useI18n();
    const {settings, updateSettingNow} = state;
    const options = [
        {
            value: 'link',
            label: lang === 'zh' ? '链接' : 'Link',
            description: lang === 'zh' ? '粘贴抖音、Bilibili、YouTube 或视频直链。' : 'Paste a Douyin, Bilibili, YouTube, or direct video link.',
        },
        {
            value: 'upload',
            label: lang === 'zh' ? '本地上传' : 'Local files',
            description: lang === 'zh' ? '这台电脑上的录像。' : 'Recordings on this computer.',
        },
    ];
    return (
        <div className="p-5">
            <label className={`${fieldLabelClass} mb-2.5 block`}>{lang === 'zh' ? '默认来源' : 'Default source'}</label>
            <div className="grid gap-3 md:grid-cols-2">
                {options.map((option) => (
                    <RouteCard
                        key={option.value}
                        label={option.label}
                        description={option.description}
                        active={normalizeSourceMode(settings.defaultSourceMode) === option.value}
                        onClick={() => updateSettingNow({defaultSourceMode: option.value})}
                    />
                ))}
            </div>
        </div>
    );
};

export const AutoIllustrateRow = ({state}) => {
    const {lang} = useI18n();
    const {settings, updateSettingNow} = state;
    return (
        <label htmlFor="settingsAutoIllustrate" className={`flex items-start justify-between gap-3 ${cellBase} cursor-pointer hover:bg-[#f4f3f3] dark:hover:bg-white/[0.04]`}>
            <span>
                <span className="block text-sm font-bold">{lang === 'zh' ? '给笔记自动配图' : 'Auto-illustrate notes'}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">
                    {lang === 'zh'
                        ? '视频笔记自动截取关键画面配图；需配置通义千问（DashScope）视觉密钥，会产生额外费用。纯口播视频通常无可配图。'
                        : 'Capture key frames into video notes. Needs a Qwen (DashScope) vision key and adds cost. Talking-head videos usually have nothing to illustrate.'}
                </span>
            </span>
            <SettingCheckbox
                id="settingsAutoIllustrate"
                checked={!!settings.autoIllustrate}
                onChange={e=>updateSettingNow({autoIllustrate:e.target.checked})}
            />
        </label>
    );
};

export const AutoExportRow = ({state}) => {
    const {t, lang} = useI18n();
    const {settings, updateSettingNow} = state;
    return (
        <label htmlFor="settingsExportToLark" className={`flex items-start justify-between gap-3 ${cellBase} cursor-pointer hover:bg-[#f4f3f3] dark:hover:bg-white/[0.04]`}>
            <span>
                <span className="block text-sm font-bold">{t('set.autoExport')}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">
                    {lang === 'zh' ? '处理完成后自动创建飞书文档。' : 'Create a Lark document automatically after processing.'}
                </span>
            </span>
            <SettingCheckbox
                id="settingsExportToLark"
                checked={settings.exportToLark || false}
                onChange={e=>updateSettingNow({exportToLark:e.target.checked})}
            />
        </label>
    );
};

export const LarkExportRouteRow = ({state}) => {
    const {t, lang} = useI18n();
    const {updateSettingNow, larkExportRoute, extraLarkRouteOptions, larkRouteHint} = state;
    return (
        <div className={`flex items-start justify-between gap-3 ${cellBase}`}>
            <span className="min-w-0">
                <span className="block text-sm font-bold">{t('set.larkExportRoute')}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">{larkRouteHint}</span>
            </span>
            <select
                className="h-10 w-[168px] shrink-0 rounded-[12px] border border-[#dedada] bg-[#fbfbfb] px-3 text-sm font-bold text-[#111111] outline-none transition focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white"
                value={larkExportRoute}
                onChange={e=>{
                    const route = e.target.value;
                    updateSettingNow({
                        larkExportRoute: route,
                        larkViaCli: isLocalLarkExportRoute(route),
                    });
                }}
            >
                {extraLarkRouteOptions.map((option) => (
                    <option key={option.value} value={option.value}>{lang === 'zh' ? option.labelZh : option.labelEn}</option>
                ))}
                <option value={LARK_EXPORT_ROUTE_OPENAPI}>{t('set.larkRouteOpenapi')}</option>
                <option value={LARK_EXPORT_ROUTE_LOCAL_CLI}>{t('set.larkRouteLocalCli')}</option>
            </select>
        </div>
    );
};

export const LarkExportHistory = ({state}) => {
    const {t} = useI18n();
    const {larkExports} = state;
    if (larkExports.length === 0) return null;
    return (
        <div className={`md:col-span-2 ${cellBase}`}>
            <div className="mb-3 flex items-center justify-between gap-3">
                <h3 className="text-sm font-bold">{t('set.larkHistory')}</h3>
                <span className="text-xs font-semibold text-on-surface-variant">{larkExports.length} {t('dash.docUnit')}</span>
            </div>
            <div className="max-h-64 space-y-2 overflow-y-auto hide-scrollbar">
                {larkExports.map((ex, i) => (
                    <a key={i} href={ex.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-3 rounded-[12px] bg-[#f4f3f3] p-3 transition hover:bg-[#efeeee] dark:bg-white/[0.08] dark:hover:bg-white/[0.12]">
                        <SvgIcon name="description" className="text-lg text-primary"/>
                        <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold">{ex.title}</span>
                            <span className="block text-[10px] text-on-surface-variant">{timeAgo(ex.timestamp, t)}</span>
                        </span>
                        <SvgIcon name="open_in_new" className="text-sm text-on-surface-variant"/>
                    </a>
                ))}
            </div>
        </div>
    );
};

export const LocalHistoryRow = ({state}) => {
    const {t, lang} = useI18n();
    const {history, cleared, requestClearHistory} = state;
    return (
        <div className={`m-5 grid gap-4 ${cellBase} md:grid-cols-[minmax(0,1fr)_auto] md:items-center`}>
            <div>
                <h3 className="text-sm font-bold">{lang === 'zh' ? '本地历史记录' : 'Local browser history'}</h3>
                <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
                    {lang === 'zh' ? '清除当前浏览器保存的本地历史记录，不会删除服务器任务。' : 'Clear history stored in this browser. Server jobs are not deleted.'}
                </p>
            </div>
            <button onClick={requestClearHistory} disabled={history.length === 0} className="inline-flex h-[40px] items-center gap-1.5 rounded-[12px] bg-red-50 px-4 text-xs font-bold text-red-600 transition hover:bg-red-100 disabled:opacity-30 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20">
                <SvgIcon name="delete_sweep" className="text-sm"/>
                {cleared ? t('edit.clearConfirm') : `${t('edit.clearHistory')} (${history.length})`}
            </button>
        </div>
    );
};

// The advanced fold's shell. The description differs by edition (what the text
// model is actually used for is not the same thing in both), so it is passed in.
export const AdvancedKeysFold = ({description, children}) => {
    const {lang} = useI18n();
    return (
        <details id="advanced" className="group scroll-mt-7 rounded-[18px] border border-[#e4e0e0] bg-white dark:border-white/[0.12] dark:bg-white/[0.06]">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4">
                <div>
                    <h2 className="font-headline text-base font-extrabold">{lang === 'zh' ? '高级 · 模型密钥' : 'Advanced · Model keys'}</h2>
                    <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">{description}</p>
                </div>
                <SvgIcon name="expand_less" className="shrink-0 text-lg text-on-surface-variant transition group-open:rotate-180"/>
            </summary>
            <div className="divide-y divide-[#ece8e8] border-t border-[#ece8e8] dark:divide-white/[0.1] dark:border-white/[0.1]">
                {children}
            </div>
        </details>
    );
};

// Text-model provider, model, and the key that provider needs. `extraKey` is a
// second key field some editions offer alongside it.
export const TextModelKeyRows = ({state, extraKey = null}) => {
    const {t, lang} = useI18n();
    const {
        updateSettingNow, aiProvider, aiModel, aiProviderDefaults, activeAiSecretKey, activeAiConfigured,
        secretDraft, setSecretDraft, saveSecret, secretSaving, secretFeedback, secretRetentionText, secretInputPlaceholder,
    } = state;
    return (
        <div className="grid gap-4 px-5 py-4 md:grid-cols-2">
            <div className="space-y-2">
                <label className={fieldLabelClass}>{t('set.provider')}</label>
                <select className={inputClass} value={aiProvider} onChange={e=>updateSettingNow({aiProvider:e.target.value, aiModel: aiProviderDefaults[e.target.value] || DEFAULT_DEEPSEEK_MODEL})}>
                    <option value="deepseek">DeepSeek</option>
                    <option value="openai">OpenAI</option>
                    <option value="qwen">Qwen</option>
                </select>
            </div>
            <div className="space-y-2">
                <label className={fieldLabelClass}>{t('set.aiModel')}</label>
                {aiProvider === 'openai' ? (
                    <select className={inputClass} value={aiModel} onChange={e=>updateSettingNow({aiModel:e.target.value})}>
                        <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                        <option value="gpt-5.4">gpt-5.4</option>
                        <option value="gpt-5.5">gpt-5.5</option>
                    </select>
                ) : aiProvider === 'qwen' ? (
                    <select className={inputClass} value={aiModel} onChange={e=>updateSettingNow({aiModel:e.target.value})}>
                        <option value={DEFAULT_QWEN_MODEL}>{DEFAULT_QWEN_MODEL}</option>
                    </select>
                ) : (
                    <select className={inputClass} value={aiModel} onChange={e=>updateSettingNow({aiModel:e.target.value})}>
                        <option value="deepseek-reasoner">deepseek-reasoner</option>
                    </select>
                )}
            </div>
            <div className="space-y-2 md:col-span-2">
                <label className={fieldLabelClass}>{aiProvider === 'openai' ? t('set.openaiKey') : (aiProvider === 'qwen' ? t('set.dashscopeKey') : t('set.deepseekKey'))}</label>
                <p className="text-xs leading-relaxed text-on-surface-variant">{secretRetentionText(activeAiConfigured)}</p>
                <div className="flex gap-2">
                    <input className={inputClass} placeholder={secretInputPlaceholder(activeAiConfigured)} type="password" value={secretDraft[activeAiSecretKey] || ''} onChange={e=>setSecretDraft(d=>({...d, [activeAiSecretKey]: e.target.value}))}/>
                    <button type="button" disabled={secretSaving || !secretDraft[activeAiSecretKey]} onClick={()=>saveSecret(activeAiSecretKey)} className={saveButtonClass}>{lang === 'zh' ? '保存' : 'Save'}</button>
                </div>
                <SecretFeedback feedback={secretFeedback} keyName={activeAiSecretKey} lang={lang}/>
            </div>
            {extraKey}
        </div>
    );
};

// The DashScope key as a separate field, for when it is not the text-model key.
// What it is for is not the same in both editions, so the copy is passed in.
export const DashscopeKeyField = ({state, description}) => {
    const {t, lang} = useI18n();
    const {
        credentialStatus, credentialConfigured, secretDraft, setSecretDraft, saveSecret,
        secretSaving, secretFeedback, secretRetentionText, secretInputPlaceholder,
    } = state;
    const configured = credentialConfigured(credentialStatus, 'dashscope_api_key');
    return (
        <div className="space-y-2 md:col-span-2">
            <label className={fieldLabelClass}>{t('set.dashscopeKey')}</label>
            <p className="text-xs leading-relaxed text-on-surface-variant">{description}</p>
            <p className="text-xs leading-relaxed text-on-surface-variant">{secretRetentionText(configured)}</p>
            <div className="flex gap-2">
                <input className={inputClass} placeholder={secretInputPlaceholder(configured)} type="password" value={secretDraft.dashscope_api_key || ''} onChange={e=>setSecretDraft(d=>({...d, dashscope_api_key: e.target.value}))}/>
                <button type="button" disabled={secretSaving || !secretDraft.dashscope_api_key} onClick={()=>saveSecret('dashscope_api_key')} className={saveButtonClass}>{lang === 'zh' ? '保存' : 'Save'}</button>
            </div>
            <SecretFeedback feedback={secretFeedback} keyName="dashscope_api_key" lang={lang}/>
        </div>
    );
};

// The key the note written after an upload uses by default. Not in the advanced
// fold: without it (or a text-model key) the person gets a transcript and no
// note, so it is the one key a first-time user has to find.
export const AnthropicKeyField = ({state}) => {
    const {lang} = useI18n();
    const {
        credentialStatus, credentialConfigured, secretDraft, setSecretDraft, saveSecret,
        secretSaving, secretFeedback, secretRetentionText, secretInputPlaceholder,
    } = state;
    const configured = credentialConfigured(credentialStatus, 'anthropic_api_key');
    return (
        <div className="space-y-2">
            <label className={fieldLabelClass}>Anthropic API Key</label>
            <p className="text-xs leading-relaxed text-on-surface-variant">
                {lang === 'zh'
                    ? '填了它，Claude 会结合画面写笔记，能引用幻灯片和白板上的内容。没填时改用「高级 · 模型密钥」里的文本模型写纯文字版；两处都没填，只会得到转录稿和字幕。'
                    : 'With this key, Claude writes the note while looking at the video frames, so it can quote slides and whiteboards. Without it the text model under “Advanced · Model keys” writes a text-only note; with neither, you get the transcript and subtitles only.'}
                {' '}
                <a className="font-semibold text-primary underline" href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">
                    {lang === 'zh' ? '在 Anthropic 控制台创建 Key' : 'Create a key in the Anthropic Console'}
                </a>
            </p>
            <p className="text-xs leading-relaxed text-on-surface-variant">{secretRetentionText(configured)}</p>
            <div className="flex gap-2">
                <input className={inputClass} placeholder={secretInputPlaceholder(configured)} type="password" value={secretDraft.anthropic_api_key || ''} onChange={e=>setSecretDraft(d=>({...d, anthropic_api_key: e.target.value}))}/>
                <button type="button" disabled={secretSaving || !secretDraft.anthropic_api_key} onClick={()=>saveSecret('anthropic_api_key')} className={saveButtonClass}>{lang === 'zh' ? '保存' : 'Save'}</button>
            </div>
            <SecretFeedback feedback={secretFeedback} keyName="anthropic_api_key" lang={lang}/>
        </div>
    );
};

export const FeishuAppCredentialRows = ({state}) => {
    const {lang} = useI18n();
    const {credentialStatus, secretDraft, setSecretDraft, saveSecret, secretSaving, secretFeedback, secretStatusText} = state;
    return (
        <div className="grid gap-4 px-5 py-4 md:grid-cols-2">
            <div className="space-y-2">
                <label className={fieldLabelClass}>FEISHU APP ID</label>
                <div className="flex gap-2">
                    <input className={inputClass} placeholder={secretStatusText(credentialStatus?.lark_app_id_configured)} value={secretDraft.lark_app_id || ''} onChange={e=>setSecretDraft(d=>({...d, lark_app_id: e.target.value}))}/>
                    <button type="button" disabled={secretSaving || !secretDraft.lark_app_id} onClick={()=>saveSecret('lark_app_id')} className={saveButtonClass}>{lang === 'zh' ? '保存' : 'Save'}</button>
                </div>
                <SecretFeedback feedback={secretFeedback} keyName="lark_app_id" lang={lang}/>
            </div>
            <div className="space-y-2">
                <label className={fieldLabelClass}>FEISHU APP SECRET</label>
                <div className="flex gap-2">
                    <input className={inputClass} placeholder={secretStatusText(credentialStatus?.lark_app_secret_configured)} type="password" value={secretDraft.lark_app_secret || ''} onChange={e=>setSecretDraft(d=>({...d, lark_app_secret: e.target.value}))}/>
                    <button type="button" disabled={secretSaving || !secretDraft.lark_app_secret} onClick={()=>saveSecret('lark_app_secret')} className={saveButtonClass}>{lang === 'zh' ? '保存' : 'Save'}</button>
                </div>
                <SecretFeedback feedback={secretFeedback} keyName="lark_app_secret" lang={lang}/>
            </div>
        </div>
    );
};

export const PyannoteTokenRow = ({state}) => {
    const {lang} = useI18n();
    const {
        secretDraft, setSecretDraft, saveSecret, secretSaving, secretFeedback,
        pyannoteTokenConfigured, pyannoteTokenEditing, setPyannoteTokenEditing,
    } = state;
    const showInput = !pyannoteTokenConfigured || pyannoteTokenEditing;
    return (
        <div className="px-5 py-4">
            <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                    <label className={fieldLabelClass}>PYANNOTE AUTH TOKEN</label>
                    <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
                        {pyannoteTokenConfigured
                            ? (lang === 'zh' ? '已配置。token 不会显示，需要更换时重新输入。' : 'Configured. The token is hidden. Re-enter it only when replacing it.')
                            : (lang === 'zh' ? '用于本机后端获取 pyannote 讲话人区分模型。' : 'Used by the local backend to fetch the pyannote diarization model.')}
                    </p>
                </div>
                {pyannoteTokenConfigured && !pyannoteTokenEditing && (
                    <button type="button" onClick={()=>setPyannoteTokenEditing(true)} className="shrink-0 rounded-[10px] border border-[#dedada] px-3 py-2 text-xs font-bold hover:bg-[#efeeee] dark:border-white/[0.12] dark:hover:bg-white/[0.12]">
                        {lang === 'zh' ? '更换' : 'Replace'}
                    </button>
                )}
            </div>
            {showInput && (
                <div className="flex flex-col gap-2 md:flex-row">
                    <input className={inputClass} placeholder={lang === 'zh' ? '粘贴 Hugging Face hf_... token' : 'Paste Hugging Face hf_... token'} type="password" value={secretDraft.pyannote_auth_token || ''} onChange={e=>setSecretDraft(d=>({...d, pyannote_auth_token: e.target.value}))}/>
                    <button type="button" disabled={secretSaving || !secretDraft.pyannote_auth_token} onClick={()=>saveSecret('pyannote_auth_token')} className={saveButtonClass}>{lang === 'zh' ? '保存' : 'Save'}</button>
                    {pyannoteTokenConfigured && (
                        <button type="button" onClick={()=>{setPyannoteTokenEditing(false); setSecretDraft(d=>({...d, pyannote_auth_token: ''}));}} className="rounded-[14px] border border-[#dedada] px-3 py-3 text-sm font-extrabold text-[#111111] transition hover:bg-[#efeeee] active:translate-y-px dark:border-white/[0.12] dark:text-white dark:hover:bg-white/[0.10]">
                            {lang === 'zh' ? '取消' : 'Cancel'}
                        </button>
                    )}
                </div>
            )}
            <SecretFeedback feedback={secretFeedback} keyName="pyannote_auth_token" lang={lang}/>
        </div>
    );
};

export const ClearHistoryDialog = ({state}) => {
    const {t, lang} = useI18n();
    const {clearConfirmOpen, setClearConfirmOpen, confirmClearHistory} = state;
    if (!clearConfirmOpen) return null;
    return (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 px-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="clearHistoryTitle">
            <div className="w-full max-w-[420px] rounded-[22px] border border-[#dedada] bg-white p-5 shadow-[0_28px_90px_-52px_rgba(17,17,17,.72)] dark:border-white/[0.12] dark:bg-[#151515]">
                <div className="flex items-start gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[14px] bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-300">
                        <SvgIcon name="warning" className="text-xl"/>
                    </span>
                    <div>
                        <h2 id="clearHistoryTitle" className="text-base font-extrabold">
                            {lang === 'zh' ? '确认清除本地历史？' : 'Clear local history?'}
                        </h2>
                        <p className="mt-2 text-sm leading-relaxed text-on-surface-variant">
                            {lang === 'zh'
                                ? '这只会删除当前浏览器保存的历史记录，不会删除服务器任务。删除后无法从本机历史列表恢复。'
                                : 'This only deletes history stored in this browser. Server jobs are not deleted, and this browser list cannot restore the removed items.'}
                        </p>
                    </div>
                </div>
                <div className="mt-5 flex justify-end gap-2">
                    <button type="button" onClick={()=>setClearConfirmOpen(false)} className="inline-flex h-10 items-center rounded-[12px] border border-[#dedada] px-4 text-xs font-bold hover:bg-[#efeeee] dark:border-white/[0.12] dark:hover:bg-white/[0.12]">
                        {t('edit.cancel')}
                    </button>
                    <button type="button" onClick={confirmClearHistory} className="inline-flex h-10 items-center rounded-[12px] bg-red-600 px-4 text-xs font-bold text-white hover:bg-red-700">
                        {lang === 'zh' ? '确认清除' : 'Clear history'}
                    </button>
                </div>
            </div>
        </div>
    );
};
