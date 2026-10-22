// @vitest-environment jsdom

// Prototype of the slim header (界面优化清单线二第 8 条), behind ?layout=v2.
// Written from the checklist:
//   - One thin row: 处理记录 / task name on the left, one status in the middle
//     only when something needs the user (clicking it goes there), 打开文件夹
//     on the right.
//   - Gone: the big title, transcription time, the output path line, 重写笔记,
//     导出到飞书, the more menu, the transcript's 导出, the note's 下载笔记 and
//     the 处理记录 button at the bottom right.
//   - Without a note, the note pane says so with a 写笔记 button; with a note,
//     no such button.
//   - Without the parameter the page is as before.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let jobs = {};
let getJobCalls = [];

vi.mock('../app/AppContext.jsx', async () => {
    const React = await vi.importActual('react');
    const Ctx = React.createContext(null);
    return {AppCtx: Ctx, useApp: () => React.useContext(Ctx)};
});

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({}), saveSettings: () => {}}),
        // One object for the whole page, as the real useApi gives (useMemo).
        useApi: () => (globalThis.__headerV2TestApi ||= {
            processVideoSSE: async () => ({}),
            fetchJobArtifactFile: async () => new File(['x'], 'x'),
            downloadJobMedia: async () => {},
            recordEvent: () => {},
            getJob: async (taskId) => {
                getJobCalls.push(taskId);
                if (!jobs[taskId]) {
                    const err = new Error('Job not found');
                    err.status = 404;
                    throw err;
                }
                return jobs[taskId];
            },
            saveTranscriptEdit: async () => ({}),
            saveSummaryEdit: async () => ({}),
            getCredentialsStatus: async () => null,
        }),
    };
});

const {AppCtx} = await import('../app/AppContext.jsx');
const {default: Editor} = await import('./editor.jsx');

const Address = () => {
    const location = useLocation();
    return <output data-testid="address">{location.pathname + location.search}</output>;
};

const Harness = ({initial}) => {
    const [lastResult, setLastResult] = useState(initial);
    const value = {
        lastResult,
        setLastResult,
        lastSourceFile: null,
        setLastSourceFile: () => {},
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        addLarkExport: () => {},
        runtimeConfig,
    };
    return (
        <AppCtx.Provider value={value}>
            <Editor/>
            <Address/>
        </AppCtx.Provider>
    );
};

const mount = (url, initial = null) => render(
    <MemoryRouter initialEntries={[url]}>
        <Routes><Route path="/editor" element={<Harness initial={initial}/>}/></Routes>
    </MemoryRouter>,
);

const address = () => screen.getByTestId('address').textContent;

const task = (id, extra = {}) => ({
    task_id: id,
    filename: `${id}.mp4`,
    display_title: `课程 ${id}`,
    transcript_text: 'hello there, this is long enough to be a whole transcript. '.repeat(8),
    segments: [{start: 0, end: 3, text: 'hello'}],
    summary_markdown: 'note',
    summary_status: 'completed',
    ...extra,
});

// A cut whose report asks the user about two places, one answered already.
const judgedCut = (verdicts = {a: 'agree'}) => ({
    status: 'completed',
    survey: {
        report: {
            before_seconds: 600,
            after_seconds: 420,
            sections: [
                {judge: true, title: '需要你判断', items: [
                    {key: 'a', ref: 'r-a', action: 'removed', start: 10, end: 14, seconds: 4, text: '一段停顿', doubt: '可能是在思考'},
                    {key: 'b', ref: 'r-b', action: 'removed', start: 30, end: 33, seconds: 3, text: '一句口误', doubt: '也可能是重点'},
                ]},
                {title: '剪掉的空白', items: [{key: 'c', ref: 'r-c', action: 'removed', start: 40, end: 42, seconds: 2, text: '空白'}]},
            ],
        },
        verdicts,
    },
});

beforeEach(() => {
    jobs = {};
    getJobCalls = [];
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => {};
    globalThis.fetch = vi.fn(async (url) => {
        const match = String(url).match(/\/jobs\/([^/?#]+)$/);
        if (match && jobs[match[1]]) {
            return new Response(JSON.stringify(jobs[match[1]]), {status: 200, headers: {'content-type': 'application/json'}});
        }
        const type = String(url).includes('playback_audio') ? 'audio/mpeg' : 'video/mp4';
        return new Response('x', {status: 206, headers: {'content-type': type}});
    });
});

afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
});


const withFolder = (extra = {}) => task('t1', {output_folder: {path: '/Users/me/Movies/课程 t1'}, stt_elapsed_seconds: 30, ...extra});

describe('the slim header (?layout=v2)', () => {
    it('is one row: records / task name and the folder, nothing else', async () => {
        mount('/editor?layout=v2', withFolder());
        const header = await screen.findByTestId('editor-header-v2');
        expect(header.textContent).toContain('处理记录');
        expect(header.textContent).toContain('课程 t1');
        expect(screen.getByRole('button', {name: /打开文件夹/})).toBeTruthy();
        for (const gone of ['edit.regenerate', 'edit.export', 'edit.sttElapsed', '产出在']) {
            expect(document.body.textContent).not.toContain(gone);
        }
        expect(screen.queryByRole('button', {name: '更多操作'})).toBeNull();
        expect(screen.queryByRole('button', {name: '导出'})).toBeNull();
        expect(screen.queryByText('dl.summary')).toBeNull();
        expect(screen.getAllByRole('link', {name: '处理记录'})).toHaveLength(1);
        expect(screen.queryByTestId('editor-header-status')).toBeNull();
        expect(screen.queryByTestId('write-note')).toBeNull();
        await waitFor(() => expect(address()).toBe('/editor?layout=v2&task=t1'));
    });

    it('shows the cut places waiting for a judgement and opens them', async () => {
        const result = withFolder({debreath: judgedCut()});
        jobs.t1 = {task_id: 't1', result};
        mount('/editor?layout=v2', result);
        const status = await screen.findByTestId('editor-header-status');
        expect(status.textContent).toBe('剪辑有 1 处需要你判断');
        fireEvent.click(status);
        expect(await screen.findByRole('dialog')).toBeTruthy();
    });

    it('says when there is no note, with a 写笔记 button in the note pane', async () => {
        const result = withFolder({summary_markdown: '', summary_status: 'failed', summary_error: 'AI summarization returned empty result'});
        jobs.t1 = {task_id: 't1', result};
        mount('/editor?layout=v2', result);
        expect((await screen.findByTestId('editor-header-status')).textContent).toBe('笔记没生成');
        fireEvent.click(await screen.findByTestId('write-note'));
        const dialog = await screen.findByRole('dialog');
        expect(dialog.textContent).toContain('写笔记');
        expect(dialog.textContent).toContain('开始写');
    });

    it('leaves the page as it was without the parameter', async () => {
        mount('/editor', withFolder());
        await screen.findByRole('heading', {name: '课程 t1'});
        expect(screen.queryByTestId('editor-header-v2')).toBeNull();
        expect(screen.getByText('edit.regenerate')).toBeTruthy();
    });
});
