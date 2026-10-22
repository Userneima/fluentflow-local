// @vitest-environment jsdom

// The start page's first-run note setup, mounted. Requirements:
//   - A new user with no key sees it on first open, instead of only being told
//     "no key, no note".
//   - Closing it is remembered; the banner can open it again.
//   - Pasting a key and pressing 检查并保存 saves it through the credentials
//     API, checks it, says notes will now be written, closes, and the "no
//     note" banner goes away.
//   - A key the provider rejects is said to be wrong, is not kept, and the
//     setup stays open.
//   - Saving a DeepSeek key makes DeepSeek the note writer even if another text
//     model was picked before.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';
import {NOTE_KEY_ONBOARDING_DISMISSED} from '../lib/noteKeyOnboarding.js';

let credentials = {};
let checkResult = {ok: true, reason: 'ok', message: 'Key 可以用。'};
const saveCredentials = vi.fn(async (patch) => {
    for (const [name, value] of Object.entries(patch)) credentials = {...credentials, [`${name}_configured`]: !!value};
    return credentials;
});
const checkCredential = vi.fn(async () => checkResult);
// The service's saved choices (decision 6.31): the note's text model.
let prefs = {};
const getPreferences = vi.fn(async () => prefs);
const savePreferences = vi.fn(async (patch) => { prefs = {...prefs, ...patch}; return {preferences: prefs}; });
// GET /workflow-status as the service answers it here: nothing can write a
// note until the saved text model's key is saved (no Claude channel).
const keyName = (provider) => (provider === 'qwen' ? 'dashscope' : provider);
const getWorkflowStatus = vi.fn(async () => ({
    steps: [],
    attention: credentials[`${keyName(prefs.ai_provider || 'deepseek')}_api_key_configured`] ? [] : [
        {id: 'no-writer', target: 'accounts', text: {zh: '现在只出转录稿：把笔记交给你的 agent 写，或在「账号和 Key」里填一个 Key。', en: ''}},
    ],
}));
const stableApi = {
    getCredentialsStatus: async () => credentials,
    getWorkflowStatus,
    getPreferences,
    savePreferences,
    saveCredentials,
    checkCredential,
    summarizeTranscriptFile: vi.fn(),
    createVideoSourceJob: vi.fn(),
    getJob: vi.fn(),
};

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        history: [],
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
        setLastSourceFile: () => {},
        addLarkExport: () => {},
        runtimeConfig: {allowedSttProviders: ['local'], defaultSttProvider: 'local', writesItsOwnNote: true, limits: {}},
        setPendingUploadAbort: () => {},
        abortPendingUpload: () => {},
        backendDown: false,
        reportBackendError: () => {},
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {...actual, useI18n: () => ({t: (key) => key, lang: 'zh'}), useApi: () => stableApi};
});

const {default: MediaText} = await import('./media-text.jsx');

const mount = () => render(
    <MemoryRouter initialEntries={['/media-text']}>
        <Routes>
            <Route path="/media-text" element={<MediaText/>}/>
        </Routes>
    </MemoryRouter>,
);

const INTRO = /笔记可以交给你自己的 agent/;
const NO_NOTE = /现在只出转录稿/;

const enterKey = (value, provider = 'DeepSeek') => {
    if (provider === 'DeepSeek') fireEvent.click(screen.getByRole('radio', {name: /^DeepSeek/}));
    fireEvent.change(screen.getByLabelText(`${provider} 的 API Key`), {target: {value}});
    fireEvent.click(screen.getByRole('button', {name: '检查并保存'}));
};

describe('the first-run note setup on the start page', () => {
    beforeEach(() => {
        localStorage.clear();
        credentials = {};
        prefs = {};
        checkResult = {ok: true, reason: 'ok', message: 'Key 可以用。'};
        saveCredentials.mockClear();
        checkCredential.mockClear();
    });
    afterEach(() => {
        cleanup();
        localStorage.clear();
    });

    it('opens on first visit when nothing can write a note', async () => {
        mount();
        expect(await screen.findByText(INTRO)).toBeTruthy();
        // The person's own agent first; among keys Qwen is recommended and picked.
        const card = screen.getByTestId('note-key-onboarding');
        expect(card.textContent.indexOf('交给你自己的 agent')).toBeLessThan(card.textContent.indexOf('通义千问'));
        expect(screen.getByRole('radio', {name: /通义千问（阿里云百炼）（推荐）/}).getAttribute('aria-checked')).toBe('true');
        for (const name of [/^DeepSeek/, /Kimi/, /MiMo/, /Anthropic/]) {
            expect(screen.getByRole('radio', {name})).toBeTruthy();
        }
    });

    it('does not open when a key is already saved', async () => {
        credentials = {openai_api_key_configured: true};
        mount();
        expect(await screen.findByText(NO_NOTE)).toBeTruthy();
        expect(screen.queryByText(INTRO)).toBeNull();
    });

    it('remembers being closed, and the banner opens it again', async () => {
        mount();
        fireEvent.click(await screen.findByRole('button', {name: /以后再说/}));
        expect(screen.queryByText(INTRO)).toBeNull();
        expect(localStorage.getItem(NOTE_KEY_ONBOARDING_DISMISSED)).toBe('1');
        expect(screen.getByText(NO_NOTE)).toBeTruthy();

        cleanup();
        mount();
        expect(await screen.findByText(NO_NOTE)).toBeTruthy();
        expect(screen.queryByText(INTRO)).toBeNull();

        fireEvent.click(screen.getByRole('button', {name: '怎么拿 Key？'}));
        expect(screen.getByText(INTRO)).toBeTruthy();
    });

    it('saves and checks a pasted key, says notes are on, closes, and drops the banner', async () => {
        mount();
        await screen.findByText(INTRO);
        enterKey('  sk-good  ');

        expect(await screen.findByText('好了，处理完的录像会自动写笔记。')).toBeTruthy();
        expect(saveCredentials).toHaveBeenCalledWith({deepseek_api_key: 'sk-good'});
        expect(checkCredential).toHaveBeenCalledWith('deepseek');
        await waitFor(() => expect(screen.queryByTestId('note-key-onboarding')).toBeNull(), {timeout: 4000});
        expect(screen.queryByText(NO_NOTE)).toBeNull();
    });

    it('says a rejected key is wrong, does not keep it, and stays open', async () => {
        checkResult = {ok: false, reason: 'invalid_key', message: '这个 Key 不对，DeepSeek 不认它。'};
        mount();
        await screen.findByText(INTRO);
        enterKey('sk-bad');

        expect(await screen.findByText(/这个 Key 不对/)).toBeTruthy();
        expect(saveCredentials).toHaveBeenLastCalledWith({deepseek_api_key: ''});
        expect(screen.getByTestId('note-key-onboarding')).toBeTruthy();
        expect(screen.queryByText(/Key 已经保存/)).toBeNull();
    });

    it('makes the provider whose key was saved the note writer', async () => {
        prefs = {ai_provider: 'deepseek'};
        mount();
        await screen.findByText(INTRO);
        enterKey('sk-qwen', '通义千问（阿里云百炼）');

        expect(await screen.findByText('好了，处理完的录像会自动写笔记。')).toBeTruthy();
        expect(saveCredentials).toHaveBeenCalledWith({dashscope_api_key: 'sk-qwen'});
        expect(checkCredential).toHaveBeenCalledWith('qwen');
        expect(prefs.ai_provider).toBe('qwen');
    });

    it('makes DeepSeek the note writer when another text model was picked', async () => {
        prefs = {ai_provider: 'openai', ai_model: 'gpt-5.4'};
        mount();
        await screen.findByText(INTRO);
        enterKey('sk-good');

        expect(await screen.findByText('好了，处理完的录像会自动写笔记。')).toBeTruthy();
        expect(prefs).toMatchObject({ai_provider: 'deepseek', ai_model: ''});
    });
});
