import {TEXT_PROVIDERS, textProvider, textProviderLabel} from '../lib/textProviders.js';
import {useEffect, useState} from 'react';
import SvgIcon from './SvgIcon.jsx';
import {
    MoreInfo,
    RouteCard,
    SecretInput,
    SettingCheckbox,
    cellBase,
    fieldLabelClass,
    inputClass,
} from './settingsPrimitives.jsx';
import {
    LARK_EXPORT_ROUTE_AUTO,
    LARK_EXPORT_ROUTE_LOCAL_CLI,
    LARK_EXPORT_ROUTE_OPENAPI,
    normalizeSourceMode,
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
// GET /speaker-diarization/status. The installers put sherpa-onnx and its two
// models in place; when either is missing the row says what to run instead of
// showing a grey switch with no reason.
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
        ? (zh ? '这台电脑还没装讲话人区分组件。重新运行一次安装脚本就会装上。' : 'The speaker separation component is not installed on this computer. Run the installer again to add it.')
        : availability === 'needs_model'
            ? (zh ? '讲话人区分的模型还没下载（约 47MB）。在 FluentFlow 文件夹里运行 scripts/fetch_diarization_models.py 即可。' : 'The speaker separation models are not downloaded yet (about 47 MB). Run scripts/fetch_diarization_models.py in the FluentFlow folder.')
            : (zh ? '标出每句话是谁说的，适合多人会议和访谈。' : 'Labels who said each line, for meetings and interviews.');
    const fallback = status?.fallback_reason || '';
    return (
        <label htmlFor="settingsSpeakerDiarization" className={`flex items-start justify-between gap-3 ${cellBase} ${available ? 'cursor-pointer hover:bg-[#f4f3f3] dark:hover:bg-white/[0.04]' : 'cursor-not-allowed'}`}>
            <span>
                <span className={`block text-sm font-bold ${available ? '' : 'opacity-60'}`}>{zh ? '区分不同讲话人' : 'Speaker separation'}</span>
                <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant" data-testid="diarization-availability">
                    {description}
                </span>
                {fallback && (
                    <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant" data-testid="diarization-fallback">
                        {fallback}
                    </span>
                )}
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
                        ? '适合手机放桌上录的、发闷的讲座；已经混好的片子别开。'
                        : 'For muffled lectures recorded with a phone on the table; leave off for already-mixed material.'}
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
                    ? '下载 B 站高清、YouTube 受限视频时，借用这个浏览器的登录状态。'
                    : 'Borrows this browser\'s login to download Bilibili HD and restricted YouTube videos.'}
            </p>
            <MoreInfo lang={lang}>
                <p>
                    {lang === 'zh'
                        ? '只在本机读取浏览器的登录 cookie，不会上传。这个选择存在本机服务里，AI 工具提交的链接也会用它。'
                        : 'The login cookies are read on this machine only and never uploaded. The choice is kept by the local service, so links submitted by AI tools use it too.'}
                </p>
            </MoreInfo>
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
            label: lang === 'zh' ? '本机文件' : 'Local files',
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

// What a cut favours (backend/core/device_profile.py preset). Kept by the
// local service beside the CPU share; FLUENTFLOW_PROCESSING_PRESET still wins.
export const ProcessingPresetRow = ({state}) => {
    const {lang} = useI18n();
    const {processingPreset, updateProcessingPreset, videoPrefError = ''} = state;
    const zh = lang === 'zh';
    const options = [
        {
            value: 'quality',
            label: zh ? '画质优先（推荐）' : 'Picture first (recommended)',
            description: zh ? '剪后画面和原片一样清楚，共享文档里的小字也能读。' : 'The cut looks like the recording, small shared-document text included.',
        },
        {
            value: 'speed',
            label: zh ? '速度优先' : 'Speed first',
            description: zh ? '剪视频的同时就开始转录，整体更快，处理时电脑更忙。' : 'Starts transcribing while the video is still being cut; faster overall, and the computer is busier while it runs.',
        },
        {
            value: 'size',
            label: zh ? '省空间' : 'Save space',
            description: zh ? '剪后文件不超过原片的码率，更小；码率低的录屏里小字会糊。' : 'The cut stays within the recording\'s bitrate, so it is smaller; small text in low-bitrate recordings blurs.',
        },
    ];
    return (
        <div className="p-5">
            <label className={`${fieldLabelClass} mb-2.5 block`}>{zh ? '处理偏好' : 'Processing preference'}</label>
            <div className="grid gap-3 md:grid-cols-3">
                {options.map((option) => (
                    <RouteCard
                        key={option.value}
                        label={option.label}
                        description={option.description}
                        active={processingPreset === option.value}
                        onClick={() => updateProcessingPreset(option.value)}
                    />
                ))}
            </div>
            {videoPrefError === 'processing_preset' && (
                <p className="mt-2 text-xs font-semibold text-red-600 dark:text-red-300">
                    {zh ? '没能保存到本机服务，处理时还会用原来的偏好。请再选一次。' : 'Could not save to the local service; processing still uses the old preference. Choose again.'}
                </p>
            )}
        </div>
    );
};

// How much of the processor a cut may take. Kept by the local service, which is
// what runs ffmpeg (backend/core/device_profile.py). At full use the one thing
// the user would otherwise blame on the product is said here: a video playing
// beside the cut can stutter or pop because the machine is busy, not because a
// file is broken.
export const CPU_USAGE_OPTIONS = ['light', 'balanced', 'full'];

export const CpuUsageRow = ({state}) => {
    const {lang} = useI18n();
    const {cpuUsage, updateCpuUsage, videoPrefError = ''} = state;
    const zh = lang === 'zh';
    const options = [
        {
            value: 'light',
            label: zh ? '省着用' : 'Light',
            description: zh ? '最多用一半左右的处理器。剪得慢一些，边处理边看视频、开会最稳。' : 'Up to about half the processor. Slower, and the steadiest while you watch videos or join calls.',
        },
        {
            value: 'balanced',
            label: zh ? '留出余量（推荐）' : 'Leave room (recommended)',
            description: zh ? '用四分之三左右，并让你正在用的软件优先。' : 'About three quarters, and the apps you are using go first.',
        },
        {
            value: 'full',
            label: zh ? '不限制' : 'No limit',
            description: zh ? '处理器有多少用多少。' : 'As much of the processor as it can get.',
        },
    ];
    return (
        <div className="p-5">
            <label className={`${fieldLabelClass} mb-2.5 block`}>{zh ? '电脑占用' : 'Computer load'}</label>
            <div className="grid gap-3 md:grid-cols-3">
                {options.map((option) => (
                    <RouteCard
                        key={option.value}
                        label={option.label}
                        description={option.description}
                        active={cpuUsage === option.value}
                        onClick={() => updateCpuUsage(option.value)}
                    />
                ))}
            </div>
            {cpuUsage === 'full' && (
                <p className="mt-3 text-xs font-semibold leading-relaxed text-amber-600 dark:text-amber-300">
                    {zh
                        ? '处理时同时放视频或开会可能卡顿、爆音，文件本身没有问题。'
                        : 'A video or call running alongside may stutter or pop; the files themselves are fine.'}
                </p>
            )}
            {videoPrefError === 'cpu_usage' && (
                <p className="mt-2 text-xs font-semibold text-red-600 dark:text-red-300">
                    {zh ? '没能保存到本机服务，处理时还会用原来的档位。请再选一次。' : 'Could not save to the local service; processing still uses the old level. Choose again.'}
                </p>
            )}
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
                        ? '从视频里截关键画面放进笔记，要用通义千问的 Key，会多花一点钱。'
                        : 'Adds key frames to video notes. Uses the Qwen key and costs a little extra.'}
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
    const {t, lang} = useI18n();
    const {settings, updateLarkFolder, commitLarkFolder, videoPrefError = ''} = state;
    return (
        <div className={`md:col-span-2 space-y-2 ${cellBase}`}>
            <label htmlFor="settingsLarkFolder" className="block text-sm font-bold">{t('set.larkFolder')}</label>
            <span className="block text-xs leading-relaxed text-on-surface-variant">
                {lang === 'zh' ? '粘贴飞书文件夹链接，应用导出的文档放在这里。' : 'Paste a Feishu folder link; app exports go there.'}
            </span>
            <MoreInfo lang={lang}><p>{t('set.larkFolderHint')}</p></MoreInfo>
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

// The model a text provider writes with. Free text, so a model released after
// this build can be used; the suggestions are the provider's own current list,
// read with the saved key. The list used to be three names written here, and
// newer models could not be picked at all.
export const ProviderModelField = ({provider, value, configured, onChange}) => {
    const {getProviderModels, getModelDefaults} = useApi();
    const [models, setModels] = useState([]);
    const [draft, setDraft] = useState(value || '');
    // What "default" is now, from the service's recommended-model table. Empty
    // value = follow it, so a default changed later reaches this user too.
    const [defaultModel, setDefaultModel] = useState('');
    useEffect(() => { setDraft(value || ''); }, [value]);
    useEffect(() => {
        let live = true;
        if (getModelDefaults) {
            getModelDefaults().then((table) => { if (live) setDefaultModel(table?.[provider]?.text || ''); }).catch(() => {});
        }
        return () => { live = false; };
    }, [provider, getModelDefaults]);
    useEffect(() => {
        let live = true;
        setModels([]);
        if (configured) {
            getProviderModels(provider).then((list) => { if (live) setModels(list); }).catch(() => {});
        }
        return () => { live = false; };
    }, [provider, configured, getProviderModels]);
    const {lang} = useI18n();
    const listId = `provider-models-${provider}`;
    // The provider's own list says the saved name is gone (retired, renamed):
    // said here, since the backend quietly writes with the default instead.
    const gone = models.length > 0 && !!value && !models.includes(value);
    // Clearing the field goes back to following the default.
    const commit = () => {
        const next = draft.trim();
        if (next !== (value || '')) onChange(next);
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
                placeholder={lang === 'zh'
                    ? `默认${defaultModel ? `（现在是 ${defaultModel}）` : ''}，FluentFlow 会跟着换新`
                    : `Default${defaultModel ? ` (now ${defaultModel})` : ''}; FluentFlow keeps it current`}
                aria-label="model"
            />
            <datalist id={listId}>
                {models.map((model) => <option key={model} value={model}/>)}
            </datalist>
            {gone && (
                <p className="mt-1 text-xs leading-relaxed text-error">
                    {lang === 'zh'
                        ? `${value} 已不在这家服务商现在的模型列表里，写笔记时会自动改用它现在的默认模型。想用别的就从下拉里换一个。`
                        : `${value} is no longer in this provider's model list; notes will use its current default. Pick another from the list to change that.`}
                </p>
            )}
        </>
    );
};

// Text-model provider, model, and the key that provider needs. `extraKey` is an
// optional second key field rendered alongside it. `keyGroup` ({group, keys})
// is the set of key fields saved together by the section's one save button.
export const TextModelKeyRows = ({state, keyGroup, extraKey = null}) => {
    const {t, lang} = useI18n();
    const {
        updateSettingNow, aiProvider, aiModel, aiProviderDefaults, activeAiSecretKey, activeAiConfigured,
        secretRetentionText, secretInputPlaceholder,
    } = state;
    return (
        <div className="grid gap-4 px-5 py-4 md:grid-cols-2">
            <div className="space-y-2">
                <label className={fieldLabelClass}>{t('set.provider')}</label>
                {/* No "recommended" mark here: this list shows what is chosen
                    now, and a mark on another provider reads as "you chose
                    wrong". The first-run setup still recommends one. */}
                <select className={inputClass} value={aiProvider} onChange={e=>updateSettingNow({aiProvider:e.target.value, aiModel: aiProviderDefaults[e.target.value] || ''})}>
                    {TEXT_PROVIDERS.map((item) => (
                        <option key={item.id} value={item.id}>{lang === 'zh' ? item.labelZh : item.labelEn}</option>
                    ))}
                </select>
            </div>
            <div className="space-y-2">
                <label className={fieldLabelClass}>{t('set.aiModel')}</label>
                <ProviderModelField provider={aiProvider} value={aiModel} configured={activeAiConfigured} onChange={(model)=>updateSettingNow({aiModel: model})}/>
            </div>
            <div className="space-y-2 md:col-span-2">
                <label className={fieldLabelClass}>{`${textProviderLabel(aiProvider, lang)} API Key`}</label>
                <p className="text-xs leading-relaxed text-on-surface-variant">
                    {secretRetentionText(activeAiConfigured)}
                    {' '}
                    <a className="font-semibold text-primary underline" href={textProvider(aiProvider).consoleUrl} target="_blank" rel="noreferrer">
                        {lang === 'zh' ? '去创建 Key' : 'Create a key'}
                    </a>
                </p>
                <SecretInput state={state} name={activeAiSecretKey} {...keyGroup} placeholder={secretInputPlaceholder(activeAiConfigured)}/>
            </div>
            {extraKey}
        </div>
    );
};

// The DashScope key as a separate field, for when it is not the text-model key.
export const DashscopeKeyField = ({state, keyGroup}) => {
    const {t, lang} = useI18n();
    const {credentialStatus, credentialConfigured, secretRetentionText, secretInputPlaceholder} = state;
    const configured = credentialConfigured(credentialStatus, 'dashscope_api_key');
    return (
        <div className="space-y-2 md:col-span-2">
            <label className={fieldLabelClass}>{t('set.dashscopeKey')}</label>
            <p className="text-xs leading-relaxed text-on-surface-variant">
                {lang === 'zh' ? '给笔记自动配图时用来挑截图。' : 'Picks screenshots when notes are auto-illustrated.'}
                {' '}
                {secretRetentionText(configured)}
            </p>
            <SecretInput state={state} name="dashscope_api_key" {...keyGroup} placeholder={secretInputPlaceholder(configured)}/>
        </div>
    );
};

// Optional: with it the note is written while looking at the frames. The text
// model key above is the one a first-time user needs; this one upgrades the note.
export const AnthropicKeyField = ({state, keyGroup}) => {
    const {lang} = useI18n();
    const {credentialStatus, credentialConfigured, secretRetentionText, secretInputPlaceholder, activeAiConfigured} = state;
    const configured = credentialConfigured(credentialStatus, 'anthropic_api_key');
    const zh = lang === 'zh';
    // What happens without it depends on whether a text-model key is there;
    // promising "the text model above" when none is filled in was untrue.
    const withoutIt = activeAiConfigured
        ? (zh ? '不填也能出笔记，由上面的文本模型根据转录稿写。' : 'Without it the text model above still writes a note from the transcript.')
        : (zh ? '上面的文本模型 Key 也还没填，两个都不填就只有转录稿，没有笔记。' : 'No text-model key is filled in above either; with neither, jobs produce a transcript but no note.');
    return (
        <div className="space-y-2">
            <label className={fieldLabelClass}>{zh ? 'Anthropic API Key（可选）' : 'Anthropic API Key (optional)'}</label>
            <p className="text-xs leading-relaxed text-on-surface-variant">
                {zh ? '填上后由 Claude 结合画面写笔记。' : 'With it, Claude writes the note from the video frames.'}
                {' '}
                {secretRetentionText(configured)}
            </p>
            <MoreInfo lang={lang}>
                <p>{zh ? '结合画面写的笔记能引用幻灯片和白板上的内容。' : 'A note written from the frames can quote slides and whiteboards.'} {withoutIt}</p>
                <a className="font-semibold text-primary underline" href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">
                    {zh ? '在 Anthropic 控制台创建 Key' : 'Create a key in the Anthropic Console'}
                </a>
            </MoreInfo>
            <SecretInput state={state} name="anthropic_api_key" {...keyGroup} placeholder={secretInputPlaceholder(configured)}/>
        </div>
    );
};

// Optional: a picture model behind an OpenAI-compatible endpoint (Kimi, MiMo, a
// relay). When Claude is not there, it judges the cut from screenshots of the
// long silent stretches (backend cut_judge). Address and model are shown back;
// only the key is hidden.
export const VISION_MODEL_KEYS = ['vision_base_url', 'vision_api_key', 'vision_model'];

export const VisionModelField = ({state, keyGroup}) => {
    const {lang} = useI18n();
    const {credentialStatus, credentialConfigured, credentialLoad, secretInputPlaceholder} = state;
    const zh = lang === 'zh';
    const keyConfigured = credentialConfigured(credentialStatus, 'vision_api_key');
    const shown = {
        vision_base_url: credentialStatus?.vision_base_url || '',
        vision_model: credentialStatus?.vision_model || '',
    };
    const field = (name, label, placeholder, type = 'text') => (
        <div className="space-y-1">
            <span className="text-xs text-on-surface-variant">{label}</span>
            <SecretInput
                state={state}
                name={name}
                {...keyGroup}
                type={type}
                placeholder={name === 'vision_api_key' ? secretInputPlaceholder(keyConfigured) : (shown[name] || placeholder)}
            />
        </div>
    );
    const ready = keyConfigured && shown.vision_base_url && shown.vision_model;
    return (
        <div className="space-y-2">
            <label className={fieldLabelClass}>{zh ? '看图模型（可选）' : 'Picture model (optional)'}</label>
            <p className="text-xs leading-relaxed text-on-surface-variant">
                {zh ? '没有 Claude 时，由它看截图判断剪哪里。' : 'Without Claude, it judges the cut from screenshots.'}
                {' '}
                {credentialLoad !== 'ready'
                    ? ''
                    : ready
                        ? (zh ? `现在用的是 ${shown.vision_model}。` : `Now using ${shown.vision_model}.`)
                        : (zh ? '还没配好。' : 'Not set up yet.')}
            </p>
            <MoreInfo lang={lang}>
                <p>
                    {zh
                        ? '填兼容 OpenAI 接口的服务，三项都填才生效。比如月之暗面：地址 https://api.moonshot.cn/v1，模型 kimi-k2.6。没有它时只看转录稿判断。'
                        : 'Any OpenAI-compatible service; all three fields are needed, e.g. Moonshot: https://api.moonshot.cn/v1 with kimi-k2.6. Without it the cut is judged from the transcript alone.'}
                </p>
            </MoreInfo>
            {field('vision_base_url', zh ? '地址' : 'Address', 'https://api.moonshot.cn/v1')}
            {field('vision_api_key', 'Key', '', 'password')}
            {field('vision_model', zh ? '模型名' : 'Model', 'kimi-k2.6')}
        </div>
    );
};

export const FEISHU_APP_KEYS = ['lark_app_id', 'lark_app_secret'];

export const FeishuAppCredentialRows = ({state, keyGroup}) => {
    const {lang} = useI18n();
    const {credentialStatus, secretStatusText} = state;
    return (
        <div className="grid gap-4 px-5 py-4 md:grid-cols-2">
            <div className="space-y-2">
                <label className={fieldLabelClass}>{lang === 'zh' ? '飞书应用 ID' : 'Feishu app ID'}</label>
                <SecretInput state={state} name="lark_app_id" {...keyGroup} type="text" placeholder={secretStatusText(credentialStatus?.lark_app_id_configured)}/>
            </div>
            <div className="space-y-2">
                <label className={fieldLabelClass}>{lang === 'zh' ? '飞书应用密钥' : 'Feishu app secret'}</label>
                <SecretInput state={state} name="lark_app_secret" {...keyGroup} placeholder={secretStatusText(credentialStatus?.lark_app_secret_configured)}/>
            </div>
        </div>
    );
};

export const PyannoteTokenRow = ({state, keyGroup}) => {
    const {lang} = useI18n();
    const {setSecretDraft, pyannoteTokenConfigured, pyannoteTokenEditing, setPyannoteTokenEditing} = state;
    const showInput = !pyannoteTokenConfigured || pyannoteTokenEditing;
    return (
        <div className="px-5 py-4">
            <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                    <label className={fieldLabelClass}>{lang === 'zh' ? 'pyannote 模型令牌（Hugging Face）' : 'pyannote model token (Hugging Face)'}</label>
                    <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
                        {pyannoteTokenConfigured
                            ? (lang === 'zh' ? '已配置。需要更换时重新输入。' : 'Configured. Re-enter it only when replacing it.')
                            : (lang === 'zh' ? '用来下载 pyannote 讲话人区分的模型，下载一次后在本机运行。' : 'Downloads the pyannote speaker separation model once; it then runs on this machine.')}
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
                    <SecretInput state={state} name="pyannote_auth_token" {...keyGroup} placeholder={lang === 'zh' ? '粘贴 Hugging Face hf_... token' : 'Paste Hugging Face hf_... token'}/>
                    {pyannoteTokenConfigured && (
                        <button type="button" onClick={()=>{setPyannoteTokenEditing(false); setSecretDraft(d=>({...d, pyannote_auth_token: ''}));}} className="rounded-[14px] border border-[#dedada] px-3 py-3 text-sm font-extrabold text-[#111111] transition hover:bg-[#efeeee] active:translate-y-px dark:border-white/[0.12] dark:text-white dark:hover:bg-white/[0.10]">
                            {lang === 'zh' ? '取消' : 'Cancel'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};
