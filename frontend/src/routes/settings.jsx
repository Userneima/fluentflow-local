import {Section} from '../components/settingsPrimitives.jsx';
import {
    AdvancedKeysFold,
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

            {!uploadWritesItsOwnNote && (
                <Section
                    id="notes"
                    title={lang === 'zh' ? '笔记' : 'Notes'}
                    description={lang === 'zh' ? 'AI 生成笔记时的偏好。' : 'Preferences for AI-generated notes.'}
                >
                    <div className="p-5">
                        <AutoIllustrateRow state={state}/>
                    </div>
                </Section>
            )}

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
                        ? '文本模型和它的密钥。上传后自动生成的笔记不经过这里——那份由本机登录的 Claude 写。这里只管编辑器里手动「重生笔记」和英文素材的翻译。'
                        : 'The text model and its key. The note an upload writes for itself does not come from here — that one is written by the Claude login on this machine. This steers the editor’s manual rewrite and the translation of English material.')
                    : (lang === 'zh' ? '服务商、模型和 API 密钥，进阶才需要展开。普通用户可忽略。' : 'Providers, models, and API keys. Open only if you know you need it.')}
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
