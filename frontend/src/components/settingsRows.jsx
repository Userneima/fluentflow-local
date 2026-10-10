import {useEffect, useState} from 'react';
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
    LARK_EXPORT_ROUTE_AUTO,
    LARK_EXPORT_ROUTE_LOCAL_CLI,
    LARK_EXPORT_ROUTE_OPENAPI,
    DEFAULT_DEEPSEEK_MODEL,
    normalizeSourceMode,
    timeAgo,
    useApi,
    useI18n,
} from '../app/shared.jsx';

// The individual settings controls. Each row takes the page state (see
// routes/settings-state.js) and renders one control; which rows the page shows,
// and in what order, is decided in routes/settings.jsx.

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
                            ? '这里放长期偏好和凭证。每个任务是怎么处理的，写在它自己的处理记录里。'
                            : 'Long-term preferences and credentials live here. How each task was handled is written in its own processing record.'}
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

// What the speaker-separation switch can honestly say, from
// GET /speaker-diarization/status. The launcher installs without pyannote, so
// on most machines the answer is "not in this build", and that is what the row
// says instead of showing a grey switch with no reason.
export const diarizationAvailability = (status) => {
    if (!status || typeof status !== 'object') return 'unknown';
    if (status.dependency_installed === false) return 'not_installed';
    if (status.available) return 'available';
    return 'needs_model';
};

export const SpeakerDiarizationRow = ({state, status}) => {
    const {lang} = useI18n();
    const {settings, updateSettingNow} = state;
    const availability = diarizationAvailability(status);
    const available = availability === 'available';
    const zh = lang === 'zh';
    const description = availability === 'not_installed'
        ? (zh ? '这个版本没有带讲话人区分组件，暂时用不了。' : 'This build does not include the speaker separation component, so it cannot be used for now.')
        : availability === 'needs_model'
            ? (zh ? '要先在下方「高级」里填讲话人区分令牌（Hugging Face），下载模型后才能用。' : 'Fill in the speaker separation token (Hugging Face) under Advanced below first, so the model can be downloaded.')
            : (zh ? '适合多人访谈或讲座。' : 'Useful for interviews or lectures.');
    return (
        <label htmlFor="settingsSpeakerDiarization" className={`flex items-start justify-between gap-3 ${cellBase} ${available ? 'cursor-pointer hover:bg-[#f4f3f3] dark:hover:bg-white/[0.04]' : 'cursor-not-allowed'}`}>
            <span>
                <span className={`block text-sm font-bold ${available ? '' : 'opacity-60'}`}>{zh ? '区分不同讲话人' : 'Speaker separation'}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant" data-testid="diarization-availability">
                    {description}
                </span>
            </span>
            {availability !== 'not_installed' && (
                <SettingCheckbox
                    id="settingsSpeakerDiarization"
                    checked={!!settings.speakerDiarization && available}
                    disabled={!available}
                    onChange={e=>updateSettingNow({speakerDiarization:e.target.checked})}
                />
            )}
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
    const {settings, updateSettingNow, cookieCheck, setCookieCheck, cookieChecking, runCookieCheck, updateVideoCookiesBrowser, videoPrefError = ''} = state;
    const changeBrowser = (value) => {
        if (updateVideoCookiesBrowser) updateVideoCookiesBrowser(value);
        else { updateSettingNow({videoCookiesBrowser: value}); setCookieCheck(null); }
    };
    return (
        <div className={`md:col-span-2 ${cellBase}`}>
            <label className="block text-sm font-bold">{lang === 'zh' ? '视频链接下载登录态' : 'Video link login'}</label>
            <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
                <select
                    className="h-10 w-[220px] rounded-[12px] border border-[#dedada] bg-[#fbfbfb] px-3 text-sm font-bold text-[#111111] outline-none transition focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white"
                    value={settings.videoCookiesBrowser || ''}
                    onChange={e=>changeBrowser(e.target.value)}
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
                {' '}
                {lang === 'zh'
                    ? '这个选择存在本机服务里，AI 工具提交的链接也会用它。'
                    : 'The choice is kept by the local service, so links submitted by AI tools use it too.'}
            </p>
            {videoPrefError === 'video_cookies_browser' && (
                <p className="mt-1.5 text-xs font-semibold text-red-600 dark:text-red-300">
                    {lang === 'zh'
                        ? '没能存进本机服务：这里提交的链接会用它，AI 工具提交的暂时用不上。'
                        : 'Could not save it to the local service: links submitted here use it, links from AI tools do not yet.'}
                </p>
            )}
        </div>
    );
};

// Its own row, not a sub-item of the browser login: it is the one setting
// here that sends something to a third party. Until the person has answered,
// it shows as off and says when it will be asked (the first Douyin link).
export const DouyinFallbackRow = ({state}) => {
    const {lang} = useI18n();
    const {allowMiuistore, updateAllowMiuistore, videoPrefError = ''} = state;
    return (
        <label id="douyin-fallback" htmlFor="settingsAllowMiuistore" className={`md:col-span-2 flex cursor-pointer items-start justify-between gap-3 ${cellBase} hover:bg-[#f4f3f3] dark:hover:bg-white/[0.04]`}>
            <span>
                <span className="block text-sm font-bold">{lang === 'zh' ? '抖音备用解析' : 'Douyin fallback resolver'}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">
                    {lang === 'zh'
                        ? '读不到抖音登录时，把抖音分享链接（只发链接本身）发给第三方解析服务 miuistore.com 换取视频地址。关掉后，抖音链接只靠浏览器登录态。'
                        : 'When no Douyin login can be read, sends the Douyin share link (only the link itself) to the third-party resolver miuistore.com to get the video address. Off: Douyin links rely on the browser login only.'}
                </span>
                {allowMiuistore === null && (
                    <span className="mt-1 block text-xs font-semibold text-on-surface-variant">
                        {lang === 'zh' ? '还没选，第一次提交抖音链接时会问。' : 'Not chosen yet. You will be asked the first time you submit a Douyin link.'}
                    </span>
                )}
                {videoPrefError === 'allow_miuistore' && (
                    <span className="mt-1 block text-xs font-semibold text-red-600 dark:text-red-300">
                        {lang === 'zh' ? '没能保存，开关已恢复原样。' : 'Could not save; the switch was put back.'}
                    </span>
                )}
            </span>
            <SettingCheckbox
                id="settingsAllowMiuistore"
                checked={allowMiuistore === true}
                disabled={!updateAllowMiuistore}
                onChange={e=>updateAllowMiuistore?.(e.target.checked)}
            />
        </label>
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
                        ? '视频笔记自动截取关键画面配图；需要通义千问（阿里云百炼）的 Key，会产生额外费用。纯口播视频通常无可配图。'
                        : 'Capture key frames into video notes. Needs a Qwen (Alibaba Cloud Bailian) key and adds cost. Talking-head videos usually have nothing to illustrate.'}
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

// AI-tool exports read the route and folder from the service; when saving
// there failed, this page and those exports disagree until it is saved again.
const PrefSaveFailed = () => {
    const {lang} = useI18n();
    return (
        <span className="mt-1 block text-xs font-semibold text-red-600 dark:text-red-300">
            {lang === 'zh' ? '没能保存到本机服务，AI 工具导出时还会用原来的设置。请再改一次。' : 'Could not save to the local service; exports from AI tools still use the old setting. Change it again.'}
        </span>
    );
};

export const LarkExportRouteRow = ({state}) => {
    const {t} = useI18n();
    const {updateLarkExportRoute, larkExportRoute, larkRouteHint, videoPrefError = ''} = state;
    return (
        <div className={`flex items-start justify-between gap-3 ${cellBase}`}>
            <span className="min-w-0">
                <span className="block text-sm font-bold">{t('set.larkExportRoute')}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">{larkRouteHint}</span>
                {videoPrefError === 'lark_export_route' && <PrefSaveFailed/>}
            </span>
            <select
                className="h-10 w-[168px] shrink-0 rounded-[12px] border border-[#dedada] bg-[#fbfbfb] px-3 text-sm font-bold text-[#111111] outline-none transition focus:border-[#111111] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white"
                value={larkExportRoute}
                onChange={e=>updateLarkExportRoute(e.target.value)}
            >
                <option value={LARK_EXPORT_ROUTE_AUTO}>{t('set.larkRouteAuto')}</option>
                <option value={LARK_EXPORT_ROUTE_LOCAL_CLI}>{t('set.larkRouteLocalCli')}</option>
                <option value={LARK_EXPORT_ROUTE_OPENAPI}>{t('set.larkRouteOpenapi')}</option>
            </select>
        </div>
    );
};

// Where the app route puts the document. Without a folder it lands in the
// app's own space, which the user may not be able to open.
export const LarkFolderRow = ({state}) => {
    const {t} = useI18n();
    const {settings, updateLarkFolder, commitLarkFolder, videoPrefError = ''} = state;
    return (
        <div className={`md:col-span-2 space-y-2 ${cellBase}`}>
            <label htmlFor="settingsLarkFolder" className="block text-sm font-bold">{t('set.larkFolder')}</label>
            <span className="block text-xs leading-relaxed text-on-surface-variant">{t('set.larkFolderHint')}</span>
            <input
                id="settingsLarkFolder"
                className={inputClass}
                placeholder={t('set.larkFolderPh')}
                value={settings.larkFolder || ''}
                onChange={e=>updateLarkFolder(e.target.value)}
                onBlur={()=>commitLarkFolder?.()}
            />
            {videoPrefError === 'lark_folder_token' && <PrefSaveFailed/>}
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

// The advanced fold's shell. The page passes in its description.
export const AdvancedKeysFold = ({description, children}) => {
    const {lang} = useI18n();
    return (
        <details id="advanced" className="group scroll-mt-7 rounded-[18px] border border-[#e4e0e0] bg-white dark:border-white/[0.12] dark:bg-white/[0.06]">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4">
                <div>
                    <h2 className="font-headline text-base font-extrabold">{lang === 'zh' ? '高级 · 其他凭证' : 'Advanced · Other credentials'}</h2>
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

// Where each provider hands out keys, so a first-time user is not left searching.
const KEY_CONSOLE_URLS = {
    deepseek: 'https://platform.deepseek.com/api_keys',
    openai: 'https://platform.openai.com/api-keys',
    qwen: 'https://bailian.console.aliyun.com/',
};

// The model a text provider writes with. Free text, so a model released after
// this build can be used; the suggestions are the provider's own current list,
// read with the saved key. The list used to be three names written here, and
// newer models could not be picked at all.
export const ProviderModelField = ({provider, value, configured, onChange}) => {
    const {getProviderModels} = useApi();
    const [models, setModels] = useState([]);
    const [draft, setDraft] = useState(value || '');
    useEffect(() => { setDraft(value || ''); }, [value]);
    useEffect(() => {
        let live = true;
        setModels([]);
        if (configured) {
            getProviderModels(provider).then((list) => { if (live) setModels(list); }).catch(() => {});
        }
        return () => { live = false; };
    }, [provider, configured, getProviderModels]);
    const listId = `provider-models-${provider}`;
    const commit = () => {
        const next = draft.trim();
        if (next && next !== value) onChange(next);
        if (!next) setDraft(value || '');
    };
    return (
        <>
            <input
                className={inputClass}
                list={listId}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={commit}
                onKeyDown={(event) => { if (event.key === 'Enter') commit(); }}
                aria-label="model"
            />
            <datalist id={listId}>
                {models.map((model) => <option key={model} value={model}/>)}
            </datalist>
        </>
    );
};

// Text-model provider, model, and the key that provider needs. `extraKey` is an
// optional second key field rendered alongside it.
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
                    <option value="qwen">{lang === 'zh' ? '通义千问（阿里云百炼）' : 'Qwen (Alibaba Cloud Bailian)'}</option>
                </select>
            </div>
            <div className="space-y-2">
                <label className={fieldLabelClass}>{t('set.aiModel')}</label>
                <ProviderModelField provider={aiProvider} value={aiModel} configured={activeAiConfigured} onChange={(model)=>updateSettingNow({aiModel: model})}/>
            </div>
            <div className="space-y-2 md:col-span-2">
                <label className={fieldLabelClass}>{aiProvider === 'openai' ? t('set.openaiKey') : (aiProvider === 'qwen' ? t('set.dashscopeKey') : t('set.deepseekKey'))}</label>
                <p className="text-xs leading-relaxed text-on-surface-variant">
                    {secretRetentionText(activeAiConfigured)}
                    {' '}
                    <a className="font-semibold text-primary underline" href={KEY_CONSOLE_URLS[aiProvider] || KEY_CONSOLE_URLS.deepseek} target="_blank" rel="noreferrer">
                        {lang === 'zh' ? '去创建 Key' : 'Create a key'}
                    </a>
                </p>
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
// The page passes in the copy that says what it is for.
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

// Optional: with it the note is written while looking at the frames. The text
// model key above is the one a first-time user needs; this one upgrades the note.
export const AnthropicKeyField = ({state}) => {
    const {lang} = useI18n();
    const {
        credentialStatus, credentialConfigured, secretDraft, setSecretDraft, saveSecret,
        secretSaving, secretFeedback, secretRetentionText, secretInputPlaceholder,
        activeAiConfigured,
    } = state;
    const configured = credentialConfigured(credentialStatus, 'anthropic_api_key');
    // What happens without it depends on whether a text-model key is there;
    // promising "the text model above" when none is filled in was untrue.
    const withoutIt = activeAiConfigured
        ? (lang === 'zh' ? '不填也能出笔记，由上面的文本模型根据转录稿写。' : 'Without it the text model above still writes a note from the transcript.')
        : (lang === 'zh' ? '上面的文本模型 Key 也还没填，两个都不填就只有转录稿，没有笔记。' : 'No text-model key is filled in above either; with neither, jobs produce a transcript but no note.');
    return (
        <div className="space-y-2">
            <label className={fieldLabelClass}>{lang === 'zh' ? 'Anthropic API Key（可选）' : 'Anthropic API Key (optional)'}</label>
            <p className="text-xs leading-relaxed text-on-surface-variant">
                {lang === 'zh'
                    ? '另外填上它，笔记改由 Claude 结合画面来写，能引用幻灯片和白板上的内容。'
                    : 'Add this as well and Claude writes the note while looking at the video frames, so it can quote slides and whiteboards.'}
                {lang === 'zh' ? '' : ' '}
                {withoutIt}
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
                <label className={fieldLabelClass}>{lang === 'zh' ? '飞书应用 ID' : 'Feishu app ID'}</label>
                <div className="flex gap-2">
                    <input className={inputClass} placeholder={secretStatusText(credentialStatus?.lark_app_id_configured)} value={secretDraft.lark_app_id || ''} onChange={e=>setSecretDraft(d=>({...d, lark_app_id: e.target.value}))}/>
                    <button type="button" disabled={secretSaving || !secretDraft.lark_app_id} onClick={()=>saveSecret('lark_app_id')} className={saveButtonClass}>{lang === 'zh' ? '保存' : 'Save'}</button>
                </div>
                <SecretFeedback feedback={secretFeedback} keyName="lark_app_id" lang={lang}/>
            </div>
            <div className="space-y-2">
                <label className={fieldLabelClass}>{lang === 'zh' ? '飞书应用密钥' : 'Feishu app secret'}</label>
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
                    <label className={fieldLabelClass}>{lang === 'zh' ? '讲话人区分令牌（Hugging Face）' : 'Speaker separation token (Hugging Face)'}</label>
                    <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
                        {pyannoteTokenConfigured
                            ? (lang === 'zh' ? '已配置。token 不会显示，需要更换时重新输入。' : 'Configured. The token is hidden. Re-enter it only when replacing it.')
                            : (lang === 'zh' ? '用来下载讲话人区分模型，下载一次后在本机运行。' : 'Used to download the speaker separation model once; it then runs on this machine.')}
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
