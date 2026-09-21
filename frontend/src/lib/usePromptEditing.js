import {useEffect, useState} from 'react';
import {
    DEFAULT_PROMPT_PRESET,
    getBuiltinExtraPromptBody,
    getDefaultPromptBody,
    isBuiltinPromptPresetHidden,
    normalizeUserPresets,
    presetDisplayLabel,
} from './promptPresets.js';

/**
 * Everything the prompt-template dialog needs, kept out of the page that opens it.
 *
 * This cluster was ten pieces of state, an effect and seven handlers living in
 * the editor page, which owns none of it: nothing outside the dialog reads a
 * single one of them. Settings are the source of truth here — each handler
 * writes through to storage immediately and the state is a mirror — which is
 * why `loadSettings` / `saveSettings` are passed in rather than imported: the
 * hook stays testable without a settings provider around it.
 */
export function usePromptEditing({loadSettings, saveSettings, t, lang, showToast}) {
    const initSettings = loadSettings();
    let initPk = initSettings.promptPreset || DEFAULT_PROMPT_PRESET;
    if (isBuiltinPromptPresetHidden(initPk, initSettings)) initPk = 'default';

    const [promptKey, setPromptKey] = useState(initPk);
    const [customText, setCustomText] = useState(initSettings.customPromptText || '');
    const [defaultPromptEdit, setDefaultPromptEdit] = useState(() => getDefaultPromptBody(initSettings));
    const [autoTranscriptNotesEdit, setAutoTranscriptNotesEdit] = useState(() => getBuiltinExtraPromptBody('autoTranscriptNotes', initSettings));
    const [meetingEdit, setMeetingEdit] = useState(() => getBuiltinExtraPromptBody('meeting', initSettings));
    const [researchEdit, setResearchEdit] = useState(() => getBuiltinExtraPromptBody('research', initSettings));
    const [quickBulletsEdit, setQuickBulletsEdit] = useState(() => getBuiltinExtraPromptBody('quickBullets', initSettings));
    const [userPresetEdit, setUserPresetEdit] = useState(() => {
        if (initPk.startsWith('user_')) {
            const p = (initSettings.userPromptPresets || []).find((x) => x.id === initPk);
            return p?.prompt || '';
        }
        return '';
    });
    const [presetNameInput, setPresetNameInput] = useState('');
    const [, setPresetListTick] = useState(0);
    const [promptOpen, setPromptOpen] = useState(false);

    // Settings can have been edited on the settings page since this page loaded,
    // so the panel re-reads them every time it opens rather than trusting the
    // copy it started with.
    useEffect(() => {
        if (!promptOpen) return;
        const s = loadSettings();
        const pkRaw = s.promptPreset || DEFAULT_PROMPT_PRESET;
        const pk = isBuiltinPromptPresetHidden(pkRaw, s) ? 'default' : pkRaw;
        setPromptKey(pk);
        setDefaultPromptEdit(getDefaultPromptBody(s));
        setAutoTranscriptNotesEdit(getBuiltinExtraPromptBody('autoTranscriptNotes', s));
        setMeetingEdit(getBuiltinExtraPromptBody('meeting', s));
        setResearchEdit(getBuiltinExtraPromptBody('research', s));
        setQuickBulletsEdit(getBuiltinExtraPromptBody('quickBullets', s));
        setCustomText(s.customPromptText || '');
        if (pk.startsWith('user_')) {
            const p = (s.userPromptPresets || []).find((x) => x.id === pk);
            setUserPresetEdit(p?.prompt || '');
        }
    }, [promptOpen]);

    const presetLabel = (key) => presetDisplayLabel(key, loadSettings(), lang);

    const handlePromptKeyChange = (newKey) => {
        setPromptKey(newKey);
        const s = loadSettings();
        saveSettings({...s, promptPreset: newKey});
        if (newKey === 'default') setDefaultPromptEdit(getDefaultPromptBody({...s, promptPreset: newKey}));
        if (newKey === 'autoTranscriptNotes') setAutoTranscriptNotesEdit(getBuiltinExtraPromptBody('autoTranscriptNotes', {...s, promptPreset: newKey}));
        if (newKey === 'meeting') setMeetingEdit(getBuiltinExtraPromptBody('meeting', {...s, promptPreset: newKey}));
        if (newKey === 'research') setResearchEdit(getBuiltinExtraPromptBody('research', {...s, promptPreset: newKey}));
        if (newKey === 'quickBullets') setQuickBulletsEdit(getBuiltinExtraPromptBody('quickBullets', {...s, promptPreset: newKey}));
        if (newKey.startsWith('user_')) {
            const p = (s.userPromptPresets || []).find((x) => x.id === newKey);
            setUserPresetEdit(p?.prompt || '');
        }
    };

    const handleCustomTextChange = (val) => {
        setCustomText(val);
        const s = loadSettings();
        saveSettings({...s, customPromptText: val});
    };

    const handleDefaultPromptChange = (val) => {
        setDefaultPromptEdit(val);
        const s = loadSettings();
        saveSettings({...s, defaultPromptOverride: val});
    };

    const handleBuiltinExtraChange = (key, val) => {
        if (key === 'autoTranscriptNotes') setAutoTranscriptNotesEdit(val);
        else if (key === 'meeting') setMeetingEdit(val);
        else if (key === 'research') setResearchEdit(val);
        else if (key === 'quickBullets') setQuickBulletsEdit(val);
        const s = loadSettings();
        saveSettings({...s, promptOverrides: {...(s.promptOverrides || {}), [key]: val}});
    };

    const resetBuiltinExtra = (key) => {
        if (!window.confirm(t('set.deleteBuiltinPromptConfirm'))) return;
        const s = loadSettings();
        const hidden = new Set(Array.isArray(s.hiddenPromptPresets) ? s.hiddenPromptPresets : []);
        hidden.add(key);
        const next = {...s, hiddenPromptPresets: Array.from(hidden)};
        if (next.promptPreset === key) next.promptPreset = 'default';
        saveSettings(next);
        // 如果当前选中该模板，则切回默认，避免面板状态与选中项不一致
        if (promptKey === key) {
            setPromptKey('default');
            setDefaultPromptEdit(getDefaultPromptBody(next));
        }
        // 触发面板重新渲染：否则 hiddenPromptPresets 更新了但 UI 不会立刻消失
        setPresetListTick((x) => x + 1);
    };

    const handleUserPresetChange = (val) => {
        setUserPresetEdit(val);
        const s = loadSettings();
        const ups = (s.userPromptPresets || []).map((p) => (p.id === promptKey ? {...p, prompt: val} : p));
        saveSettings({...s, userPromptPresets: ups});
    };

    const saveCustomAsPresetFromEditor = () => {
        const name = presetNameInput.trim();
        if (!name || !customText.trim()) {
            showToast(lang === 'zh' ? '请填写预设名称和提示词内容' : 'Enter a name and prompt text', false);
            return;
        }
        const s = loadSettings();
        const id = 'user_' + Date.now();
        const next = {
            ...s,
            userPromptPresets: [{id, nameZh: name, nameEn: name, prompt: customText}, ...(s.userPromptPresets || [])],
        };
        saveSettings(next);
        setPresetNameInput('');
        setPresetListTick((x) => x + 1);
        showToast(t('set.presetSaved'));
    };

    const handleDeleteUserPreset = (id, e) => {
        e.stopPropagation();
        e.preventDefault();
        if (!window.confirm(t('set.deletePresetConfirm'))) return;
        const s = loadSettings();
        const ups = normalizeUserPresets(s).filter((p) => p.id !== id);
        const next = {...s, userPromptPresets: ups};
        if (next.promptPreset === id) next.promptPreset = 'default';
        saveSettings(next);
        if (promptKey === id) {
            setPromptKey('default');
            setDefaultPromptEdit(getDefaultPromptBody(next));
        }
        setPresetListTick((x) => x + 1);
        showToast(lang === 'zh' ? '已删除预设' : 'Preset deleted', true);
    };

    return {
        promptOpen,
        setPromptOpen,
        promptKey,
        presetLabel,
        dialogProps: {
            promptKey,
            presetLabel,
            handlePromptKeyChange,
            handleDeleteUserPreset,
            resetBuiltinExtra,
            defaultPromptEdit,
            handleDefaultPromptChange,
            userPresetEdit,
            handleUserPresetChange,
            customText,
            handleCustomTextChange,
            presetNameInput,
            setPresetNameInput,
            saveCustomAsPresetFromEditor,
            autoTranscriptNotesEdit,
            meetingEdit,
            researchEdit,
            quickBulletsEdit,
            handleBuiltinExtraChange,
        },
    };
}
