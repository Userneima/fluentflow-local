// @vitest-environment jsdom

// The transcript in paragraphs (decision 6.35), edited sentence by sentence.
// Written from what the reader must be able to count on:
//   - Every sentence is on the page exactly once, in order, numbered 1 to N,
//     whatever paragraph it falls in.
//   - Typing in a sentence changes that sentence and no other, also in the
//     middle of a later paragraph.
//   - A sentence emptied and typed in again stays that sentence: the one
//     after it is not taken over, and no sentence goes missing.
//   - When a save is refused because the transcript changed elsewhere, the
//     sentence being edited shows the newer text and is left, so the old text
//     cannot be typed on and saved over it.
//   - Pasted lines join with spaces; nothing is saved while an input method is
//     still composing.
// The service here answers like the real one: it keeps every sentence it is
// sent, empty ones included, and refuses a save made on an older version.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}, writesItsOwnNote: false};

const SENTENCES = [
    ['S0', '大家好'], ['S0', '今天聊模型'], ['S0', '先说背景'],
    ['S1', '我补充一句'], ['S1', '算力很关键'], ['S1', '后训练也是'],
    ['S0', '那我们继续'], ['S0', '说说组织'], ['S0', '就到这里'],
];
let server = null;
let saves = [];

const taskFrom = ({segments, version}) => ({
    task_id: 't1',
    filename: 't1.mp4',
    display_title: '访谈',
    transcript_text: segments.map((seg) => seg.text).filter(Boolean).join('\n'),
    raw_segments: segments,
    segments,
    summary_markdown: '# 笔记\n\n正文。',
    summary_status: 'completed',
    note_version: 'n1',
    transcript_version: version,
});

vi.mock('../app/AppContext.jsx', async () => {
    const React = await vi.importActual('react');
    const Ctx = React.createContext(null);
    return {AppCtx: Ctx, useApp: () => React.useContext(Ctx)};
});

vi.mock('../components/NoteEditor.jsx', () => ({default: () => null}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({aiProvider: 'deepseek'}), saveSettings: () => {}}),
        useApi: () => (globalThis.__paragraphsApi ||= {
            processVideoSSE: async () => ({}),
            fetchJobArtifactFile: async () => new File(['x'], 'x'),
            downloadJobMedia: async () => {},
            recordEvent: () => {},
            getJob: async () => ({task_id: 't1', status: 'completed', result: taskFrom(server)}),
            saveTranscriptEdit: async (taskId, payload) => {
                saves.push(payload);
                if (payload.base_version !== server.version) {
                    throw Object.assign(new Error('字幕在别处被改过，已换成最新的版本。'), {stale: true, status: 409});
                }
                server = {segments: payload.segments.map((seg) => ({...seg})), version: `v${saves.length + 1}`};
                return {result: taskFrom(server)};
            },
            saveSummaryEdit: async () => ({}),
            getCredentialsStatus: async () => ({deepseek_api_key_configured: true}),
            getVisualNoteAvailability: async () => ({available: false, reason: 'no frames'}),
        }),
    };
});

const {AppCtx} = await import('../app/AppContext.jsx');
const {default: Editor} = await import('./editor.jsx');

const Harness = ({initial}) => {
    const [lastResult, setLastResult] = useState(initial);
    const value = {
        lastResult, setLastResult,
        lastSourceFile: null, setLastSourceFile: () => {},
        addToHistory: () => {}, currentJob: null, setCurrentJob: () => {},
        addLarkExport: () => {}, runtimeConfig,
    };
    return <AppCtx.Provider value={value}><Editor/></AppCtx.Provider>;
};

const mount = async () => {
    render(
        <MemoryRouter initialEntries={['/editor']}>
            <Routes><Route path="/editor" element={<Harness initial={taskFrom(server)}/>}/></Routes>
        </MemoryRouter>,
    );
    const first = await screen.findByRole('textbox', {name: '第 1 句'});
    await waitFor(() => expect(first.textContent).toBe('大家好'));
};
const sentence = (n) => screen.getByRole('textbox', {name: `第 ${n} 句`});
const type = (n, text) => {
    const box = sentence(n);
    box.textContent = text;
    fireEvent.input(box);
};
const pause = async (ms = 1200) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const original = SENTENCES.map(([, text]) => text);

beforeEach(() => {
    server = {segments: SENTENCES.map(([speaker, text], i) => ({start: i * 3, end: i * 3 + 3, speaker, text})), version: 'v1'};
    saves = [];
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => {};
});
afterEach(cleanup);

describe('the paragraphs on the page', () => {
    it('show every sentence once, in order, numbered 1 to N', async () => {
        await mount();
        expect(document.querySelectorAll('[data-testid="transcript-paragraph"]').length).toBe(3);
        const boxes = [...document.querySelectorAll('[data-transcript-segment]')];
        expect(boxes.map((box) => box.getAttribute('aria-label'))).toEqual(original.map((_, i) => `第 ${i + 1} 句`));
        expect(boxes.map((box) => box.textContent)).toEqual(original);
        expect(screen.getAllByText(/^说话人 [AB]$/).map((node) => node.textContent)).toEqual(['说话人 A', '说话人 B', '说话人 A']);
    });

    it('change only the sentence typed in, also in the middle of a later paragraph', async () => {
        await mount();
        type(5, '算力非常关键');
        type(8, '说说团队');
        await waitFor(() => expect(saves.length).toBe(1), {timeout: 3000});
        const expected = [...original];
        expected[4] = '算力非常关键';
        expected[7] = '说说团队';
        expect(saves[0].segments.map((seg) => seg.text)).toEqual(expected);
    });
});

describe('a sentence emptied and typed in again', () => {
    it('stays that sentence; the next one is untouched and none goes missing', async () => {
        await mount();
        const box = sentence(4);
        act(() => { box.focus(); });
        type(4, '');
        await waitFor(() => expect(saves.length).toBe(1), {timeout: 3000});
        await pause(300);
        expect(document.activeElement).toBe(box);
        type(4, '新');
        await waitFor(() => expect(saves.length).toBe(2), {timeout: 3000});
        const expected = [...original];
        expected[3] = '新';
        expect(saves[1].segments.map((seg) => seg.text)).toEqual(expected);
        expect(sentence(5).textContent).toBe('算力很关键');
    });
});

describe('a save refused because the transcript changed elsewhere', () => {
    it('shows the newer sentence where the user was typing, and leaves it', async () => {
        await mount();
        const box = sentence(2);
        act(() => { box.focus(); });
        type(2, '今天聊模型。');
        await waitFor(() => expect(saves.length).toBe(1), {timeout: 3000});
        await pause(300);
        // An agent changes the same sentence; the version moves on.
        server = {segments: server.segments.map((seg, i) => (i === 1 ? {...seg, text: '今天聊大模型'} : seg)), version: 'elsewhere'};
        type(2, '今天聊模型。。');
        await waitFor(() => expect(saves.length).toBe(2), {timeout: 3000});
        await waitFor(() => expect(sentence(2).textContent).toBe('今天聊大模型'));
        expect(document.activeElement).not.toBe(sentence(2));
        await pause(1500);
        // Nothing older was saved on the newer version.
        expect(saves.filter((save) => save.base_version === 'elsewhere')).toEqual([]);
        expect(server.segments[1].text).toBe('今天聊大模型');
    });
});

describe('pasting and input methods', () => {
    it('joins pasted lines with spaces', async () => {
        await mount();
        type(3, '先说\n背景');
        await waitFor(() => expect(saves.length).toBe(1), {timeout: 3000});
        expect(saves[0].segments[2].text).toBe('先说 背景');
        expect(sentence(3).textContent).toBe('先说 背景');
    });

    it('saves nothing while the input method is still composing', async () => {
        await mount();
        const box = sentence(6);
        fireEvent.compositionStart(box);
        type(6, '后训练也是h');
        await pause(1500);
        expect(saves).toEqual([]);
        box.textContent = '后训练也是好';
        fireEvent.compositionEnd(box);
        fireEvent.input(box);
        await waitFor(() => expect(saves.length).toBe(1), {timeout: 3000});
        expect(saves[0].segments[5].text).toBe('后训练也是好');
    });
});
