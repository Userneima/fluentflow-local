import {Section} from '../components/settingsPrimitives.jsx';
import {
    AdvancedKeysFold,
    AnthropicKeyField,
    AutoExportRow,
    AutoIllustrateRow,
    DashscopeKeyField,
    DefaultSourceRow,
    DouyinFallbackRow,
    FeishuAppCredentialRows,
    LarkExportHistory,
    LarkExportRouteRow,
    LarkFolderRow,
    LocalSttSpeedRow,
    PyannoteTokenRow,
    SettingsPageShell,
    SpeakerDiarizationRow,
    VoiceEnhanceRow,
    TextModelKeyRows,
    VideoCookiesRow,
} from '../components/settingsRows.jsx';
import {LARK_EXPORT_ROUTE_LOCAL_CLI} from '../app/shared.jsx';
import {useSettingsPageState} from './settings-state.js';
import {noteWriterSentence, resolveNoteWriter} from '../lib/noteWriter.js';

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
    const {lang, runtimeConfig, diarizationStatus, aiProvider, larkExportRoute, credentialStatus, settings} = state;
    // The same sentence the start page shows, from the same rule, so the two
    // pages never disagree about whose model writes the note.
    const noteWriter = resolveNoteWriter({runtimeConfig, credentialStatus, settings});
    const writerSentence = credentialStatus ? noteWriterSentence(noteWriter, lang) : '';
    // Off means the pipeline writes the note when asked, so its note settings
    // are real again. On means that stage never runs and the controls that only
    // steer it would be wired to nothing.
    const uploadWritesItsOwnNote = !!runtimeConfig.writesItsOwnNote;
    // The first sentence states only what is always true; who writes the note
    // is the writer sentence's job, so the two cannot disagree.
    const notesIntro = `${lang === 'zh'
        ? '转录在本机完成，不需要 Key。'
        : 'Transcription runs on this machine and needs no key.'}${writerSentence ? ` ${writerSentence}` : ''}`;
    // The token only fetches the model, so it is asked for only where the
    // component is installed and the model files are not already here.
    const showPyannoteToken = !!diarizationStatus
        && diarizationStatus.dependency_installed !== false
        && !diarizationStatus.models_local;
    const showFeishuAppRows = larkExportRoute !== LARK_EXPORT_ROUTE_LOCAL_CLI;
    const advancedDescription = (() => {
        const zh = lang === 'zh';
        if (showFeishuAppRows && showPyannoteToken) return zh ? '飞书应用凭证和讲话人区分令牌，用到时再展开。' : 'Feishu app credentials and the speaker separation token. Open when you need them.';
        if (showFeishuAppRows) return zh ? '飞书应用凭证，用到时再展开。' : 'Feishu app credentials. Open when you need them.';
        return zh ? '讲话人区分令牌，用到时再展开。' : 'The speaker separation token. Open when you need it.';
    })();

    return (
        <SettingsPageShell>
            <Section
                id="notes"
                title={lang === 'zh' ? '笔记' : 'Notes'}
                description={notesIntro}
            >
                <div className="divide-y divide-[#ece8e8] dark:divide-white/[0.1]">
                    <TextModelKeyRows
                        state={state}
                        extraKey={aiProvider !== 'qwen' && !uploadWritesItsOwnNote && (
                            <DashscopeKeyField
                                state={state}
                                description={lang === 'zh'
                                    ? '用于通义千问的看图模型给笔记挑选截图（「给笔记自动配图」需要它）；笔记仍可由 DeepSeek 或 OpenAI 来写。'
                                    : 'Used by the Qwen vision model to pick screenshots for a note (needed by auto-illustrate). Notes can still be written by DeepSeek or OpenAI.'}
                            />
                        )}
                    />
                    <div className="grid gap-3 px-5 py-4">
                        <AnthropicKeyField state={state}/>
                        {!uploadWritesItsOwnNote && <AutoIllustrateRow state={state}/>}
                    </div>
                </div>
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
                    <LocalSttSpeedRow state={state}/>
                    <VideoCookiesRow state={state}/>
                    <DouyinFallbackRow state={state}/>
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
                    <LarkExportHistory state={state}/>
                </div>
            </Section>

            {(showFeishuAppRows || showPyannoteToken) && (
                <AdvancedKeysFold description={advancedDescription}>
                    {showFeishuAppRows && <FeishuAppCredentialRows state={state}/>}
                    {showPyannoteToken && <PyannoteTokenRow state={state}/>}
                </AdvancedKeysFold>
            )}
        </SettingsPageShell>
    );
};

export default Settings;
