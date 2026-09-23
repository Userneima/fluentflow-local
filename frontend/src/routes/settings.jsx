import {Section} from '../components/settingsPrimitives.jsx';
import {
    AdvancedKeysFold,
    AnthropicKeyField,
    AutoExportRow,
    AutoIllustrateRow,
    ClearHistoryDialog,
    DashscopeKeyField,
    DefaultSourceRow,
    FeishuAppCredentialRows,
    LarkExportHistory,
    LarkExportRouteRow,
    LocalHistoryRow,
    LocalSttSpeedRow,
    PyannoteTokenRow,
    SettingsPageShell,
    SpeakerDiarizationRow,
    VoiceEnhanceRow,
    TextModelKeyRows,
    VideoCookiesRow,
} from '../components/settingsRows.jsx';
import {LARK_EXPORT_ROUTE_OPENAPI} from '../app/shared.jsx';
import {useSettingsPageState} from './settings-state.js';

// The local edition's settings page.
//
// It lists the sections this edition actually has. There is one transcription
// route and it runs here, so there is no route picker and no "cloud or local"
// copy; every device-side control (speed, browser login, the diarization model
// token) is simply present instead of asking whether local transcription is
// allowed. The only thing still asked at runtime is whether an upload writes
// its own note, because that is a switch the person running the server can
// turn off, not a fact about the edition.
const Settings = () => {
    const state = useSettingsPageState();
    const {lang, runtimeConfig, diarizationStatus, aiProvider, larkExportRoute} = state;
    // Off means the pipeline writes the note when asked, so its note settings
    // are real again. On means that stage never runs and the controls that only
    // steer it would be wired to nothing.
    const uploadWritesItsOwnNote = !!runtimeConfig.writesItsOwnNote;

    return (
        <SettingsPageShell overlays={<ClearHistoryDialog state={state}/>}>
            <Section
                id="notes"
                title={lang === 'zh' ? '笔记' : 'Notes'}
                description={lang === 'zh'
                    ? '写笔记要调用模型，用的是你自己的模型账号。转录不需要。'
                    : 'Writing a note calls a model on your own account. Transcription does not.'}
            >
                <div className="grid gap-3 p-5">
                    <AnthropicKeyField state={state}/>
                    {!uploadWritesItsOwnNote && <AutoIllustrateRow state={state}/>}
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
                    <SpeakerDiarizationRow state={state} available={!!diarizationStatus?.available}/>
                    <VoiceEnhanceRow state={state}/>
                    <LocalSttSpeedRow state={state}/>
                    <VideoCookiesRow state={state}/>
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
                    <LarkExportHistory state={state}/>
                </div>
            </Section>

            <Section id="data" title={lang === 'zh' ? '数据' : 'Data'} description={lang === 'zh' ? '本机保存的记录。' : 'Records stored on this device.'}>
                <LocalHistoryRow state={state}/>
            </Section>

            <AdvancedKeysFold
                description={uploadWritesItsOwnNote
                    ? (lang === 'zh'
                        ? '文本模型和它的密钥。没填 Anthropic API Key 时，上传后的笔记由这里的模型写成纯文字版；编辑器里手动「重生笔记」和英文素材的翻译也用它。'
                        : 'The text model and its key. Without an Anthropic API Key, the note an upload writes comes from this model as text only; the editor’s manual rewrite and the translation of English material use it too.')
                    : (lang === 'zh' ? '写笔记用的文本模型和它的密钥。默认 DeepSeek，Key 在 platform.deepseek.com 创建。' : 'The text model that writes notes, and its key. DeepSeek by default; create a key at platform.deepseek.com.')}
            >
                <TextModelKeyRows
                    state={state}
                    extraKey={aiProvider !== 'qwen' && !uploadWritesItsOwnNote && (
                        <DashscopeKeyField
                            state={state}
                            description={lang === 'zh'
                                ? '用于 Qwen 视觉模型给笔记挑选截图（「给笔记自动配图」需要它）；摘要仍可使用 DeepSeek 或 OpenAI。'
                                : 'Used by the Qwen vision model to pick screenshots for a note (needed by auto-illustrate). Summaries can still use DeepSeek or OpenAI.'}
                        />
                    )}
                />
                {larkExportRoute === LARK_EXPORT_ROUTE_OPENAPI && <FeishuAppCredentialRows state={state}/>}
                <PyannoteTokenRow state={state}/>
            </AdvancedKeysFold>
        </SettingsPageShell>
    );
};

export default Settings;
