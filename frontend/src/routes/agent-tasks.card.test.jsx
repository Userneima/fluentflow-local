// @vitest-environment jsdom

// A finished record on the records page, mounted. Written from the walkthrough
// of 2026-10-22:
// - the badge says what actually came out: a task whose note was not written
//   does not wear a green 已完成 (item 9);
// - "删除记录" is not a button beside "查看结果" at the same weight; it sits in a
//   "更多" menu and still asks before deleting (item 14);
// - "查看结果" opens the editor with the task in the address, and a task whose
//   cut has places left to judge says so there, so the editor can take the
//   owner to them (item 10; the editor side is the editor's own work).

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';

let tasks = [];
const deleteJob = vi.fn(async () => ({}));

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        tasks,
        currentJob: null,
        setCurrentJob: () => {},
        setLastResult: () => {},
        addToHistory: () => {},
        removeFromHistory: () => {},
        restoreTask: () => {},
        ingestJobs: () => {},
        markCancelled: () => {},
        revertCancelled: () => {},
        abortPendingUpload: () => {},
        runtimeConfig: {},
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useApi: () => ({
            getJob: async () => ({}),
            getJobs: async () => [],
            cancelJob: async () => ({}),
            deleteJob,
            retryJob: async () => ({}),
            createVideoSourceJob: async () => ({}),
            downloadJobMedia: async () => ({}),
            openOutputFolder: async () => ({}),
        }),
    };
});

const {default: AgentTasks} = await import('./agent-tasks.jsx');

const finished = (id, result) => ({
    task_id: id,
    status: 'completed',
    source_type: 'video_file',
    source_filename: `${id}.mp4`,
    created_at: '2026-10-20T10:00:00Z',
    updated_at: '2026-10-20T10:00:00Z',
    metadata: {display_title: `录像 ${id}`},
    result: {task_id: id, summary_status: 'completed', summary_chars: 1200, transcript_text: '有转录', ...result},
});

const EditorAddress = () => {
    const location = useLocation();
    return <p data-testid="editor-address">{`${location.pathname}${location.search}`}</p>;
};

const mount = () => render(
    <MemoryRouter initialEntries={['/agent']}>
        <Routes>
            <Route path="/agent" element={<AgentTasks/>}/>
            <Route path="/editor" element={<EditorAddress/>}/>
        </Routes>
    </MemoryRouter>,
);

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    deleteJob.mockClear();
});

describe('a finished record', () => {
    it('says the note was not written instead of 已完成', () => {
        tasks = [finished('a', {summary_status: 'failed', summary_chars: 0, summary_error: 'timeout'})];
        mount();
        expect(screen.getByTestId('task-outcome').textContent).toBe('笔记没生成');
    });

    it('keeps 删除记录 in the 更多 menu, and still asks before deleting', async () => {
        tasks = [finished('a', {})];
        mount();
        expect(screen.queryByRole('button', {name: '删除记录'})).toBeNull();
        fireEvent.click(screen.getByRole('button', {name: '更多'}));
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
        fireEvent.click(screen.getByRole('menuitem', {name: '删除记录'}));
        expect(confirm).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(deleteJob).toHaveBeenCalledWith('a', {}));
    });

    it('opens the editor with the task in the address', async () => {
        tasks = [finished('a', {debreath: {status: 'completed', survey: {has_report: true, to_judge: 0}}})];
        mount();
        fireEvent.click(screen.getByRole('button', {name: '查看结果'}));
        expect((await screen.findByTestId('editor-address')).textContent).toBe('/editor?task=a');
    });

    it('tells the editor there are places left to judge', async () => {
        tasks = [finished('b', {debreath: {status: 'completed', survey: {has_report: true, to_judge: 4}}})];
        mount();
        fireEvent.click(screen.getByRole('button', {name: '查看结果'}));
        expect((await screen.findByTestId('editor-address')).textContent).toBe('/editor?task=b&review=cut');
    });
});

// Walkthrough 2026-10-22, items 7, 8 and 13: a record is one compact row with
// its facts on one line and the output folder behind a button, the three stat
// tiles are gone, and a hundred records can be searched and filtered.
describe('the list of records', () => {
    const withFolder = (id, result) => finished(id, {output_folder: {path: `/Users/me/Movies/${id}/很长很长的产出文件夹路径`}, ...result});

    it('puts a finished record on one line of facts and keeps the folder path behind a button', () => {
        tasks = [withFolder('a', {audio_duration_seconds: 3110, debreath: {status: 'completed', plan: {cut_count: 501, removed_seconds: 1136}}})];
        mount();
        const facts = screen.getByTestId('task-facts').textContent;
        expect(facts).toMatch(/51 分 50 秒/);
        expect(facts).toMatch(/去气口 剪掉 18 分 56 秒/);
        expect(facts).toMatch(/笔记 1200 字/);
        expect(screen.queryByText(/很长很长的产出文件夹路径/)).toBeNull();
        expect(screen.getByRole('button', {name: '打开文件夹'}).getAttribute('title')).toMatch(/很长很长的产出文件夹路径/);
    });

    it('has no stat tiles', () => {
        tasks = [finished('a', {})];
        mount();
        expect(screen.queryByText('历史记录')).toBeNull();
        expect(screen.queryByText('排队中')).toBeNull();
    });

    it('says what is running only while something is', () => {
        tasks = [finished('a', {})];
        const {unmount} = mount();
        expect(screen.queryByTestId('live-summary')).toBeNull();
        unmount();
        tasks = [finished('a', {}), {task_id: 'r', status: 'running', stage: 'stt', progress: 40, created_at: '2026-10-22T10:00:00Z', metadata: {display_title: '在转的'}}];
        mount();
        expect(screen.getByTestId('live-summary').textContent).toBe('1 个在处理');
    });

    // Review 2026-10-22: a finished task still writing its note, or redoing its
    // cut, is in progress for the filter and the count alike.
    it('counts a task still writing its note or redoing its cut as in progress', () => {
        tasks = [
            finished('ok', {}),
            finished('writing', {summary_status: 'pending', summary_chars: 0}),
            finished('recut', {debreath: {status: 'running'}}),
        ];
        mount();
        expect(screen.getByTestId('live-summary').textContent).toBe('2 个在处理');
        fireEvent.click(screen.getByRole('button', {name: /进行中/}));
        const titles = screen.queryAllByRole('heading', {level: 2}).map((node) => node.textContent).sort();
        expect(titles).toEqual(['录像 recut', '录像 writing']);
    });

    it('filters to records waiting for a judgement, and to records with a problem', () => {
        tasks = [
            finished('ok', {}),
            finished('judge', {debreath: {status: 'completed', survey: {to_judge: 2}}}),
            finished('nonote', {summary_status: 'failed', summary_chars: 0}),
            {...finished('bad', {}), status: 'failed', result: null},
        ];
        mount();
        const titles = () => screen.queryAllByRole('heading', {level: 2}).map((node) => node.textContent);
        expect(titles()).toHaveLength(4);
        fireEvent.click(screen.getByRole('button', {name: /待你判断/}));
        expect(titles()).toEqual(['录像 judge']);
        fireEvent.click(screen.getByRole('button', {name: /有问题/}));
        expect(titles().sort()).toEqual(['录像 bad', '录像 nonote']);
        fireEvent.click(screen.getByRole('button', {name: /全部/}));
        expect(titles()).toHaveLength(4);
    });

    it('finds records by title', () => {
        tasks = [finished('a', {}), {...finished('b', {}), metadata: {display_title: '罗福莉访谈'}}];
        mount();
        fireEvent.change(screen.getByRole('searchbox'), {target: {value: '福莉'}});
        expect(screen.queryAllByRole('heading', {level: 2}).map((node) => node.textContent)).toEqual(['罗福莉访谈']);
        fireEvent.change(screen.getByRole('searchbox'), {target: {value: '没有这个'}});
        expect(screen.getByText('没有符合的记录。')).toBeTruthy();
    });

    it('opens the processing report from the 更多 menu', () => {
        tasks = [finished('a', {debreath: {status: 'completed', survey: {has_report: true, to_judge: 0}}})];
        mount();
        expect(screen.queryByTestId('cut-report-panel')).toBeNull();
        fireEvent.click(screen.getByRole('button', {name: '更多'}));
        fireEvent.click(screen.getByRole('menuitem', {name: '处理说明'}));
        expect(screen.getByTestId('cut-report-panel')).toBeTruthy();
    });
});
