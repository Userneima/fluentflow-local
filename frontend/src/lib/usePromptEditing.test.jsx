// @vitest-environment jsdom

import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {usePromptEditing} from './usePromptEditing.js';

afterEach(cleanup);

/**
 * Settings are the source of truth for this cluster; the state is a mirror.
 * So the thing worth pinning is that every edit reaches storage immediately,
 * and that the panel re-reads storage instead of trusting what it started with.
 */
function setup(initial = {}) {
    let stored = {...initial};
    const saveSettings = vi.fn((next) => {
        stored = next;
    });
    const loadSettings = vi.fn(() => stored);
    const showToast = vi.fn();
    const view = renderHook(() =>
        usePromptEditing({
            loadSettings,
            saveSettings,
            t: (key) => key,
            lang: 'zh',
            showToast,
        }),
    );
    return {view, saveSettings, showToast, read: () => stored};
}

beforeEach(() => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('usePromptEditing', () => {
    it('writes a custom prompt edit straight through to settings', () => {
        const {view, read} = setup();
        act(() => view.result.current.dialogProps.handleCustomTextChange('write me a summary'));
        expect(read().customPromptText).toBe('write me a summary');
        expect(view.result.current.dialogProps.customText).toBe('write me a summary');
    });

    it('records the chosen preset as soon as it is picked', () => {
        const {view, read} = setup();
        act(() => view.result.current.dialogProps.handlePromptKeyChange('meeting'));
        expect(read().promptPreset).toBe('meeting');
        expect(view.result.current.promptKey).toBe('meeting');
    });

    it('starts on the preset the settings already named', () => {
        const {view} = setup({promptPreset: 'research'});
        expect(view.result.current.promptKey).toBe('research');
    });

    it('falls back to the default when the saved preset has been hidden', () => {
        const {view} = setup({promptPreset: 'meeting', hiddenPromptPresets: ['meeting']});
        expect(view.result.current.promptKey).toBe('default');
    });

    it('re-reads settings when the panel opens', () => {
        const {view, read} = setup({promptPreset: 'default'});
        // Stand in for the settings page having been used in another tab.
        act(() => {
            Object.assign(read(), {promptPreset: 'research', customPromptText: 'edited elsewhere'});
        });
        act(() => view.result.current.setPromptOpen(true));
        expect(view.result.current.promptKey).toBe('research');
        expect(view.result.current.dialogProps.customText).toBe('edited elsewhere');
    });

    it('keeps a builtin override under its own key', () => {
        const {view, read} = setup();
        act(() => view.result.current.dialogProps.handleBuiltinExtraChange('meeting', 'my meeting prompt'));
        expect(read().promptOverrides.meeting).toBe('my meeting prompt');
        expect(view.result.current.dialogProps.meetingEdit).toBe('my meeting prompt');
    });

    it('moves off a builtin template that is being hidden', () => {
        const {view, read} = setup({promptPreset: 'meeting'});
        act(() => view.result.current.dialogProps.resetBuiltinExtra('meeting'));
        expect(read().hiddenPromptPresets).toContain('meeting');
        expect(read().promptPreset).toBe('default');
        expect(view.result.current.promptKey).toBe('default');
    });

    it('moves off a saved preset that is being deleted', () => {
        const preset = {id: 'user_1', nameZh: 'mine', nameEn: 'mine', prompt: 'body'};
        const {view, read} = setup({promptPreset: 'user_1', userPromptPresets: [preset]});
        expect(view.result.current.promptKey).toBe('user_1');
        act(() =>
            view.result.current.dialogProps.handleDeleteUserPreset('user_1', {
                stopPropagation() {},
                preventDefault() {},
            }),
        );
        expect(read().userPromptPresets).toHaveLength(0);
        expect(view.result.current.promptKey).toBe('default');
    });

    it('leaves the selection alone when some other preset is deleted', () => {
        const presets = [
            {id: 'user_1', nameZh: 'a', nameEn: 'a', prompt: 'a'},
            {id: 'user_2', nameZh: 'b', nameEn: 'b', prompt: 'b'},
        ];
        const {view, read} = setup({promptPreset: 'user_1', userPromptPresets: presets});
        act(() =>
            view.result.current.dialogProps.handleDeleteUserPreset('user_2', {
                stopPropagation() {},
                preventDefault() {},
            }),
        );
        expect(view.result.current.promptKey).toBe('user_1');
        expect(read().promptPreset).toBe('user_1');
    });

    it('refuses to save a preset with no name', () => {
        const {view, showToast, read} = setup();
        act(() => view.result.current.dialogProps.handleCustomTextChange('some prompt'));
        act(() => view.result.current.dialogProps.saveCustomAsPresetFromEditor());
        expect(showToast).toHaveBeenCalledWith(expect.stringContaining('请填写'), false);
        expect(read().userPromptPresets).toBeUndefined();
    });

    it('refuses to save a preset with no prompt text', () => {
        const {view, showToast, read} = setup();
        act(() => view.result.current.dialogProps.setPresetNameInput('named'));
        act(() => view.result.current.dialogProps.saveCustomAsPresetFromEditor());
        expect(showToast).toHaveBeenCalledWith(expect.stringContaining('请填写'), false);
        expect(read().userPromptPresets).toBeUndefined();
    });

    it('saves a named preset at the front of the list and clears the name field', () => {
        const {view, read} = setup();
        act(() => view.result.current.dialogProps.handleCustomTextChange('body text'));
        act(() => view.result.current.dialogProps.setPresetNameInput('my preset'));
        act(() => view.result.current.dialogProps.saveCustomAsPresetFromEditor());
        expect(read().userPromptPresets[0]).toMatchObject({nameZh: 'my preset', prompt: 'body text'});
        expect(view.result.current.dialogProps.presetNameInput).toBe('');
    });

    it('edits a saved preset in place', () => {
        const preset = {id: 'user_1', nameZh: 'mine', nameEn: 'mine', prompt: 'old'};
        const {view, read} = setup({promptPreset: 'user_1', userPromptPresets: [preset]});
        act(() => view.result.current.dialogProps.handleUserPresetChange('new body'));
        expect(read().userPromptPresets[0].prompt).toBe('new body');
        expect(view.result.current.dialogProps.userPresetEdit).toBe('new body');
    });

    it('does not delete anything when the confirmation is declined', () => {
        window.confirm.mockReturnValue(false);
        const preset = {id: 'user_1', nameZh: 'mine', nameEn: 'mine', prompt: 'body'};
        const {view, read} = setup({promptPreset: 'user_1', userPromptPresets: [preset]});
        act(() =>
            view.result.current.dialogProps.handleDeleteUserPreset('user_1', {
                stopPropagation() {},
                preventDefault() {},
            }),
        );
        expect(read().userPromptPresets).toHaveLength(1);
        expect(view.result.current.promptKey).toBe('user_1');
    });
});
