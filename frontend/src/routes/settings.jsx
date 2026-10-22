import {useEffect, useState} from 'react';
import {SecretSaveBar} from '../components/settingsPrimitives.jsx';
import {
    AnthropicKeyField,
    VisionModelField,
    AutoExportRow,
    AutoIllustrateRow,
    CpuUsageRow,
    ProcessingPresetRow,
    DashscopeKeyField,
    DefaultSourceRow,
    FEISHU_APP_KEYS,
    FeishuAppCredentialRows,
    LarkExportRouteRow,
    LarkFolderRow,
    PyannoteTokenRow,
    SettingsPageShell,
    SpeakerDiarizationRow,
    VoiceEnhanceRow,
    TextModelKeyRows,
    VideoCookiesRow,
    VISION_MODEL_KEYS,
} from '../components/settingsRows.jsx';
import {LARK_EXPORT_ROUTE_LOCAL_CLI} from '../app/shared.jsx';
import {useSettingsPageState} from './settings-state.js';
import {resolveNoteWriter} from '../lib/noteWriter.js';
import {NoteKeyOnboarding} from '../components/NoteKeyOnboarding.jsx';
import {NoteStylePanel} from '../components/NoteStylePanel.jsx';
import LocalAgentAccessPanel from '../components/LocalAgentAccessPanel.jsx';
import {dismissNoteKeyOnboarding} from '../lib/noteKeyOnboarding.js';

// The settings page (decision 6.24): one card per step a recording goes
// through, each saying first who does that step now and in what state; its
// options open under it. Keys are a way to change who does a step, so they are
// kept together under 账号和 Key and linked from the steps that use them.
// Something that needs the person is said once at the top.

const linkClass = 'text-xs font-semibold text-primary underline underline-offset-2';

const StepCard = ({step, open, onToggle, lang, children}) => {
    const zh = lang === 'zh';
    const attention = step.level === 'attention';
    return (
        <section id={step.id} className="scroll-mt-7 rounded-[16px] border border-[#e4e0e0] bg-white dark:border-white/[0.12] dark:bg-white/[0.06]">
            <button type="button" aria-expanded={open} onClick={onToggle} className="grid w-full gap-1 px-5 py-4 text-left md:grid-cols-[112px_minmax(0,1fr)_auto] md:items-center md:gap-4">
                <h2 className="text-[13px] font-extrabold text-[#676970] dark:text-white/55">{step.title}</h2>
                <p className={`text-sm font-semibold leading-relaxed ${attention ? 'text-amber-700 dark:text-amber-300' : 'text-[#111111] dark:text-white'}`}>
                    {step.summary}
                </p>
                <span className="text-xs font-bold text-on-surface-variant">{open ? (zh ? '收起' : 'Close') : (zh ? '调整' : 'Adjust')}</span>
            </button>
            {open && <div className="border-t border-[#ece8e8] dark:border-white/[0.1]">{children}</div>}
        </section>
    );
};

const Settings = () => {
    const state = useSettingsPageState();
    const {
        lang, runtimeConfig, diarizationStatus, aiProvider, larkExportRoute, credentialStatus, credentialLoad,
        settings, activeAiSecretKey,
    } = state;
    const zh = lang === 'zh';
    const noteWriter = resolveNoteWriter({runtimeConfig, credentialStatus, settings});
    const claudeWritesNote = noteWriter.kind === 'claude';
    const {workflow, workflowError} = state;
    const loading = credentialLoad !== 'ready';
    const [openStep, setOpenStep] = useState('');
    const [accountsOpen, setAccountsOpen] = useState(false);
    const [keySetupOpen, setKeySetupOpen] = useState(false);
    const toggle = (id) => setOpenStep((current) => (current === id ? '' : id));
    const goTo = (id) => {
        if (id === 'accounts') setAccountsOpen(true);
        else setOpenStep(id);
        setTimeout(() => {
            const target = document.getElementById(id);
            if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({block: 'start', behavior: 'smooth'});
        }, 0);
    };
    // Arriving from "add a key in Settings" (/settings#notes) or a task card's
    // link to one row: open what holds it.
    useEffect(() => {
        const id = typeof window !== 'undefined' ? decodeURIComponent(window.location.hash.slice(1)) : '';
        if (!id) return;
        if (id === 'notes') { goTo('accounts'); return; }
        const target = document.getElementById(id);
        if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({block: 'center'});
    }, []);

    const noteKeys = {group: 'note-keys', keys: [activeAiSecretKey, 'dashscope_api_key', 'anthropic_api_key', ...VISION_MODEL_KEYS]};
    const appKeys = {group: 'app-keys', keys: [...FEISHU_APP_KEYS, 'pyannote_auth_token']};
    const pyannote = diarizationStatus?.requested_backend === 'pyannote' ? diarizationStatus.pyannote : null;
    const showPyannoteToken = !!pyannote && pyannote.dependency_installed !== false && !pyannote.models_local;
    const showFeishuAppRows = larkExportRoute !== LARK_EXPORT_ROUTE_LOCAL_CLI;

    const pick = (text) => (text ? (zh ? text.zh : text.en) : '');
    const steps = Object.fromEntries((workflow?.steps || []).map((step) => [step.id, step]));
    const STEP_TITLES = {
        intake: zh ? '拿到素材' : 'Getting material',
        cutting: zh ? '剪辑' : 'Cutting',
        transcription: zh ? '转录' : 'Transcription',
        notes: zh ? '写笔记' : 'Notes',
        outputs: zh ? '产出和同步' : 'Outputs',
        agent: zh ? '你的 agent' : 'Your agent',
    };
    const attention = workflow?.attention || [];
    const card = (id, children) => {
        const step = steps[id];
        const summary = step
            ? pick(step.summary)
            : workflowError ? (zh ? '读不到现状，刷新页面再试。' : 'Could not read the status. Reload the page.') : (zh ? '正在读取…' : 'Loading…');
        return (
            <StepCard
                step={{id, title: step ? pick(step.title) : STEP_TITLES[id], summary, level: step?.level || 'ok'}}
                open={openStep === id}
                onToggle={() => toggle(id)}
                lang={lang}
            >
                {children}
            </StepCard>
        );
    };
    const toAccounts = (text) => (
        <button type="button" className={linkClass} onClick={() => goTo('accounts')}>{text}</button>
    );

    return (
        <SettingsPageShell intro={zh ? '一条录像从拿到素材到出笔记，每一步现在是怎么做的。' : 'How each step from recording to note is done right now.'}>
            {attention.length > 0 && (
                <section className="rounded-[16px] border border-amber-300 bg-amber-50 px-5 py-3 dark:border-amber-400/30 dark:bg-amber-400/10" data-testid="settings-attention">
                    <h2 className="text-[13px] font-extrabold text-amber-800 dark:text-amber-200">{zh ? '需要你处理' : 'Needs you'}</h2>
                    <ul className="mt-1.5 space-y-1.5">
                        {attention.map((item) => (
                            <li key={item.id} className="flex flex-wrap items-baseline justify-between gap-2 text-sm font-semibold text-amber-900 dark:text-amber-100">
                                <span>{pick(item.text)}</span>
                                <button type="button" className={linkClass} onClick={() => goTo(item.target)}>{zh ? '去处理' : 'Fix'}</button>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {card('intake', (
                <div className="grid gap-3 p-5">
                    <VideoCookiesRow state={state}/>
                    <DefaultSourceRow state={state}/>
                </div>
            ))}

            {card('cutting', (
                <div className="divide-y divide-[#ece8e8] dark:divide-white/[0.1]">
                    <ProcessingPresetRow state={state}/>
                    <CpuUsageRow state={state}/>
                    <p className="px-5 py-3">{toAccounts(zh ? '换谁来判断剪哪里（看图模型）' : 'Change who decides the cuts (picture model)')}</p>
                </div>
            ))}

            {card('transcription', (
                <div className="grid gap-3 p-5 md:grid-cols-2">
                    <SpeakerDiarizationRow state={state} status={diarizationStatus}/>
                    <VoiceEnhanceRow state={state}/>
                </div>
            ))}

            {card('notes', (
                <div className="divide-y divide-[#ece8e8] dark:divide-white/[0.1]">
                    {claudeWritesNote ? <NoteStylePanel/> : (
                        <div className="grid gap-3 p-5"><AutoIllustrateRow state={state}/></div>
                    )}
                    <p className="px-5 py-3">{toAccounts(zh ? '换谁来写笔记（模型和 Key）' : 'Change who writes notes (models and keys)')}</p>
                </div>
            ))}

            {card('outputs', (
                <div className="grid gap-3 p-5 md:grid-cols-2">
                    <AutoExportRow state={state}/>
                    <LarkExportRouteRow state={state}/>
                    {showFeishuAppRows && <LarkFolderRow state={state}/>}
                </div>
            ))}

            {card('agent', <LocalAgentAccessPanel compact/>)}

            <section id="accounts" className="scroll-mt-7 rounded-[16px] border border-[#e4e0e0] bg-white dark:border-white/[0.12] dark:bg-white/[0.06]">
                <button type="button" aria-expanded={accountsOpen} onClick={() => setAccountsOpen(!accountsOpen)} className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left">
                    <span>
                        <h2 className="text-sm font-extrabold">{zh ? '账号和 Key' : 'Accounts and keys'}</h2>
                        <p className="mt-0.5 text-xs text-on-surface-variant">{zh ? '想换别的模型写笔记、判断剪辑，或用飞书应用导出时填。' : 'Fill these to use other models, or a Feishu app for export.'}</p>
                    </span>
                    <span className="text-xs font-bold text-on-surface-variant">{accountsOpen ? (zh ? '收起' : 'Close') : (zh ? '展开' : 'Open')}</span>
                </button>
                {accountsOpen && (
                    <div className="divide-y divide-[#ece8e8] border-t border-[#ece8e8] dark:divide-white/[0.1] dark:border-white/[0.1]">
                        {loading ? (
                            <p className="px-5 py-4 text-xs text-on-surface-variant" data-testid="note-keys-loading">
                                {credentialLoad === 'error'
                                    ? (zh ? '读不到 Key 的状态，刷新页面再试。' : 'Could not read the key status. Reload the page.')
                                    : (zh ? '正在读取 Key 的状态…' : 'Reading the key status…')}
                            </p>
                        ) : (
                            <>
                                <div className="px-5 pt-4">
                                    {keySetupOpen ? (
                                        <NoteKeyOnboarding
                                            runtimeConfig={runtimeConfig}
                                            onClose={({completed} = {}) => {
                                                if (!completed) dismissNoteKeyOnboarding();
                                                setKeySetupOpen(false);
                                            }}
                                            onStatusChange={state.setCredentialStatus}
                                            onUseTextProvider={state.updateSettingNow}
                                        />
                                    ) : (
                                        <button type="button" onClick={() => setKeySetupOpen(true)} className={linkClass}>
                                            {zh ? '第一次填 Key？看看 Key 是什么、去哪里申请、大概花多少钱' : 'First time? What a key is, where to get one, and roughly what it costs'}
                                        </button>
                                    )}
                                </div>
                                <TextModelKeyRows
                                    state={state}
                                    keyGroup={noteKeys}
                                    extraKey={aiProvider !== 'qwen' && !claudeWritesNote && <DashscopeKeyField state={state} keyGroup={noteKeys}/>}
                                />
                                <div className="grid gap-3 px-5 py-4">
                                    <AnthropicKeyField state={state} keyGroup={noteKeys}/>
                                    <VisionModelField state={state} keyGroup={noteKeys}/>
                                    <SecretSaveBar state={state} {...noteKeys} lang={lang}/>
                                </div>
                                {(showFeishuAppRows || showPyannoteToken) && (
                                    <>
                                        {showFeishuAppRows && <FeishuAppCredentialRows state={state} keyGroup={appKeys}/>}
                                        {showPyannoteToken && <PyannoteTokenRow state={state} keyGroup={appKeys}/>}
                                        <div className="px-5 py-4"><SecretSaveBar state={state} {...appKeys} lang={lang}/></div>
                                    </>
                                )}
                            </>
                        )}
                    </div>
                )}
            </section>
        </SettingsPageShell>
    );
};

export default Settings;
