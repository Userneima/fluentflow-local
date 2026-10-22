import {useEffect, useState} from 'react';
import {SecretSaveBar, Section} from '../components/settingsPrimitives.jsx';
import {
    AdvancedKeysFold,
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
import {noteWriterSentence, resolveNoteWriter} from '../lib/noteWriter.js';
import {NoteKeyOnboarding} from '../components/NoteKeyOnboarding.jsx';
import {NoteStylePanel} from '../components/NoteStylePanel.jsx';
import {dismissNoteKeyOnboarding} from '../lib/noteKeyOnboarding.js';

// The settings page.
//
// It lists the sections this app actually has. There is one transcription
// route and it runs here, so there is no route picker and no "cloud or local"
// copy; every device-side control (speed, browser login, the diarization model
// token) is simply present instead of asking whether local transcription is
// allowed. The only thing still asked at runtime is whether an upload writes
// its own note, because that is a switch the person running the server can
// turn off, not a fixed fact about the app.
const Settings = () => {
    const state = useSettingsPageState();
    const {lang, runtimeConfig, diarizationStatus, aiProvider, larkExportRoute, credentialStatus, credentialLoad, settings, activeAiSecretKey} = state;
    const zh = lang === 'zh';
    // The same sentence the start page shows, from the same rule, so the two
    // pages never disagree about whose model writes the note.
    const noteWriter = resolveNoteWriter({runtimeConfig, credentialStatus, settings});
    const writerSentence = credentialStatus ? noteWriterSentence(noteWriter, lang) : '';
    // Auto-illustrate and the Qwen key steer the pipeline's text note. They are
    // hidden only when Claude writes the note instead, which takes both this
    // build writing its own note and a Claude channel being available; the
    // build switch alone is not enough, because without Claude the backend
    // falls back to the text note and these settings apply (and cost money).
    const claudeWritesNote = noteWriter.kind === 'claude';
    // The first-run note setup, opened again by hand from here.
    const [keySetupOpen, setKeySetupOpen] = useState(false);
    // Arriving from a task card's "go to settings" link (/settings#<row id>).
    useEffect(() => {
        const id = typeof window !== 'undefined' ? decodeURIComponent(window.location.hash.slice(1)) : '';
        if (!id) return;
        const target = document.getElementById(id);
        if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({block: 'center'});
    }, []);
    // The notes section says who writes the note and nothing else: that
    // transcription needs no key belongs to the transcription section.
    const notesIntro = credentialLoad === 'loading'
        ? (zh ? '正在读取…' : 'Loading…')
        : writerSentence;
    // The key fields of each section are saved together by one button.
    const noteKeys = {group: 'note-keys', keys: [activeAiSecretKey, 'dashscope_api_key', 'anthropic_api_key', ...VISION_MODEL_KEYS]};
    const advancedKeys = {group: 'advanced-keys', keys: [...FEISHU_APP_KEYS, 'pyannote_auth_token']};
    // The token only fetches the optional pyannote backend's model, so it is
    // asked for only when that backend is chosen and installed and its model
    // files are not already here. The default backend needs no token.
    const pyannote = diarizationStatus?.requested_backend === 'pyannote' ? diarizationStatus.pyannote : null;
    const showPyannoteToken = !!pyannote
        && pyannote.dependency_installed !== false
        && !pyannote.models_local;
    const showFeishuAppRows = larkExportRoute !== LARK_EXPORT_ROUTE_LOCAL_CLI;
    const advancedDescription = (() => {
        const zh = lang === 'zh';
        if (showFeishuAppRows && showPyannoteToken) return zh ? '飞书应用凭证和 pyannote 模型令牌，用到时再展开。' : 'Feishu app credentials and the pyannote model token. Open when you need them.';
        if (showFeishuAppRows) return zh ? '飞书应用凭证，用到时再展开。' : 'Feishu app credentials. Open when you need them.';
        return zh ? 'pyannote 模型令牌，用到时再展开。' : 'The pyannote model token. Open when you need it.';
    })();

    return (
        <SettingsPageShell>
            <Section
                id="notes"
                title={lang === 'zh' ? '笔记' : 'Notes'}
                description={notesIntro}
            >
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
                        <button type="button" onClick={() => setKeySetupOpen(true)} className="text-xs font-semibold text-primary underline">
                            {lang === 'zh' ? '第一次填 Key？看看 Key 是什么、去哪里申请、大概花多少钱' : 'First time? What a key is, where to get one, and roughly what it costs'}
                        </button>
                    )}
                </div>
                {credentialLoad === 'ready' ? (
                    <div className="divide-y divide-[#ece8e8] dark:divide-white/[0.1]">
                        <TextModelKeyRows
                            state={state}
                            keyGroup={noteKeys}
                            extraKey={aiProvider !== 'qwen' && !claudeWritesNote && <DashscopeKeyField state={state} keyGroup={noteKeys}/>}
                        />
                        <div className="grid gap-3 px-5 py-4">
                            <AnthropicKeyField state={state} keyGroup={noteKeys}/>
                            <VisionModelField state={state} keyGroup={noteKeys}/>
                            {!claudeWritesNote && <AutoIllustrateRow state={state}/>}
                        </div>
                        <div className="px-5 py-4">
                            <SecretSaveBar state={state} {...noteKeys} lang={lang}/>
                        </div>
                    </div>
                ) : (
                    <p className="px-5 py-4 text-xs leading-relaxed text-on-surface-variant" data-testid="note-keys-loading">
                        {credentialLoad === 'error'
                            ? (zh ? '读不到 Key 的状态，刷新页面再试。' : 'Could not read the key status. Reload the page.')
                            : (zh ? '正在读取 Key 的状态…' : 'Reading the key status…')}
                    </p>
                )}
            </Section>

            <Section
                id="note-style"
                title={lang === 'zh' ? '我的笔记风格' : 'My note style'}
                description={lang === 'zh'
                    ? 'Claude 写笔记时照这份写法，你在编辑器里的修改会变成候选规则。'
                    : 'Claude writes notes this way; your edits in the editor become candidate rules.'}
            >
                {credentialLoad !== 'ready' ? null : claudeWritesNote ? <NoteStylePanel/> : (
                    <p className="px-5 py-4 text-xs leading-relaxed text-on-surface-variant">
                        {lang === 'zh' ? '由 Claude 写笔记时才用得上，填好上面的 Anthropic API Key 后可以调整。' : 'Used when Claude writes the note; add an Anthropic API key above to adjust it.'}
                    </p>
                )}
            </Section>

            <Section
                id="transcription"
                title={lang === 'zh' ? '转录' : 'Transcription'}
                description={lang === 'zh'
                    ? '把音视频变成文字的方式。转录在本机完成。'
                    : 'How media becomes text. Transcription runs on this device.'}
            >
                <div className="grid gap-3 p-5 md:grid-cols-2">
                    <SpeakerDiarizationRow state={state} status={diarizationStatus}/>
                    <VoiceEnhanceRow state={state}/>
                    <VideoCookiesRow state={state}/>
                </div>
            </Section>

            <Section
                id="cpu-usage"
                title={lang === 'zh' ? '剪辑' : 'Cutting'}
                description={lang === 'zh'
                    ? '剪掉气口时更看重什么，以及最多占用多少处理器。'
                    : 'What cutting the gaps favours, and how much of the processor it may take.'}
            >
                <div className="divide-y divide-[#ece8e8] dark:divide-white/[0.1]">
                    <ProcessingPresetRow state={state}/>
                    <CpuUsageRow state={state}/>
                </div>
            </Section>

            <Section
                id="intake"
                title={lang === 'zh' ? '开始处理' : 'Starting a job'}
                description={lang === 'zh' ? '打开首页时，默认停在哪一种来源上。' : 'Which source the start page opens on.'}
            >
                <DefaultSourceRow state={state}/>
            </Section>


            <Section id="export" title={lang === 'zh' ? '导出' : 'Export'} description={lang === 'zh' ? '把笔记同步到飞书云文档。' : 'Sync notes to Feishu cloud docs.'}>
                <div className="grid gap-3 p-5 md:grid-cols-2">
                    <AutoExportRow state={state}/>
                    <LarkExportRouteRow state={state}/>
                    {larkExportRoute !== LARK_EXPORT_ROUTE_LOCAL_CLI && <LarkFolderRow state={state}/>}
                </div>
            </Section>

            {(showFeishuAppRows || showPyannoteToken) && (
                <AdvancedKeysFold description={advancedDescription}>
                    {showFeishuAppRows && <FeishuAppCredentialRows state={state} keyGroup={advancedKeys}/>}
                    {showPyannoteToken && <PyannoteTokenRow state={state} keyGroup={advancedKeys}/>}
                    <div className="px-5 py-4">
                        <SecretSaveBar state={state} {...advancedKeys} lang={lang}/>
                    </div>
                </AdvancedKeysFold>
            )}
        </SettingsPageShell>
    );
};

export default Settings;
