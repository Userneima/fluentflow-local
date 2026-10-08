// @vitest-environment jsdom

// Exporting a note to Feishu from the editor, as the user sees it:
// - a task that was already exported shows "已导出 · 打开" next to the button;
// - exporting it again asks first, because every export makes a new document;
// - a finished export says "已导出到飞书" and how many screenshots did not make it;
// - a failed automatic export says so next to the button.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let lastResult = null;
let exportResponse = null;
const exportCalls = [];

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        lastResult,
        setLastResult: (next) => { lastResult = typeof next === 'function' ? next(lastResult) : next; },
        lastSourceFile: null,
        setLastSourceFile: () => {},
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        addLarkExport: () => {},
        runtimeConfig,
    }),
}));

const strings = {
    'edit.export': '导出到飞书',
    'edit.exportDone': '已导出到飞书',
    'edit.exportOpen': '打开',
    'edit.exportedOnce': '已导出',
    'edit.exportAgainConfirm': '已经导出过一次，再导出会新建一篇文档。继续吗？',
    'edit.exportImagesMissing': '{n} 张截图没传上去',
    'edit.autoExportFailed': '自动导出到飞书没成功',
};

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        apiFetch: async (url, init) => {
            if (String(url).endsWith('/export-lark')) {
                exportCalls.push(Object.fromEntries(init.body.entries()));
                return {ok: true, json: async () => exportResponse};
            }
            return {ok: true, json: async () => ({})};
        },
        useI18n: () => ({t: (key) => strings[key] || key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({larkExportRoute: 'auto', larkFolder: 'https://x.feishu.cn/drive/folder/fldA'}), saveSettings: () => {}}),
        useApi: () => ({
            processVideoSSE: async () => ({}),
            fetchJobSourceFile: async () => { throw new Error('no source'); },
            fetchJobArtifactFile: async () => { throw new Error('no artifact'); },
            uploadJobPlaybackAudio: async () => ({}),
            recordEvent: () => {},
            getJob: async () => ({}),
            saveTranscriptEdit: async () => ({}),
            saveSummaryEdit: async () => ({}),
        }),
    };
});

const {default: Editor} = await import('./editor.jsx');

const noteResult = (extra = {}) => ({
    task_id: 't-feishu',
    filename: 'lecture.mp4',
    transcript_text: 'hello',
    segments: [{start: 0, end: 3, text: 'hello'}],
    summary_markdown: '# 笔记\n\n正文',
    ...extra,
});

const mount = () => render(<MemoryRouter><Editor/></MemoryRouter>);
const exportButton = () => screen.getByRole('button', {name: /导出到飞书/});

describe('exporting a note to Feishu', () => {
    beforeEach(() => {
        lastResult = null;
        exportResponse = null;
        exportCalls.length = 0;
    });
    afterEach(() => {
        cleanup();
        vi.restoreAllMocks();
    });

    it('shows the saved link on a task that was already exported', () => {
        lastResult = noteResult({lark_response: {url: 'https://x.feishu.cn/wiki/old'}});
        mount();

        const link = screen.getByTestId('lark-export-link');
        expect(link.textContent).toContain('已导出');
        expect(link.textContent).toContain('打开');
        expect(link.getAttribute('href')).toBe('https://x.feishu.cn/wiki/old');
    });

    it('asks before exporting a second time, and does nothing when the user says no', async () => {
        lastResult = noteResult({lark_response: {url: 'https://x.feishu.cn/wiki/old'}});
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
        mount();

        fireEvent.click(exportButton());

        expect(confirm).toHaveBeenCalledWith('已经导出过一次，再导出会新建一篇文档。继续吗？');
        expect(exportCalls).toEqual([]);
    });

    it('says the export landed and how many screenshots did not upload', async () => {
        lastResult = noteResult();
        exportResponse = {ok: true, url: 'https://x.feishu.cn/wiki/new', image_count: 3, image_upload_count: 1, doc_title: '笔记'};
        const confirm = vi.spyOn(window, 'confirm');
        mount();

        fireEvent.click(exportButton());

        await waitFor(() => expect(screen.getAllByText(/已导出到飞书/).length).toBeGreaterThan(0));
        expect(confirm).not.toHaveBeenCalled();
        expect(screen.getAllByText(/2 张截图没传上去/).length).toBeGreaterThan(0);
        expect(screen.queryByText(/导出请求已发送/)).toBeNull();
        expect(exportCalls[0].folder_token).toBe('https://x.feishu.cn/drive/folder/fldA');
        expect(exportCalls[0].lark_export_route).toBe('auto');
        expect(lastResult.lark_response.url).toBe('https://x.feishu.cn/wiki/new');
    });

    it('tells the user when the automatic export failed', () => {
        lastResult = noteResult({lark_error: '这台电脑上的 lark-cli 没有可用的登录身份（没登录或登录已过期）。'});
        mount();

        expect(screen.getByText(/自动导出到飞书没成功/)).toBeTruthy();
    });
});
