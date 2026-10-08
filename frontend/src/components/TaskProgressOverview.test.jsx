// @vitest-environment jsdom

// The card at the top of a task page. Written from what the person reads
// there: which step the task is on in plain Chinese, how far along it is, and
// for a failed task what went wrong in words they can act on.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

let currentJob = null;
const cancelCalls = [];

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => actual.msgs.zh[key] || key, lang: 'zh'}),
        useApi: () => ({cancelJobRecord: async (job) => { cancelCalls.push(job); }}),
    };
});

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({currentJob, setCurrentJob: () => {}}),
}));

const {default: TaskProgressOverview} = await import('./TaskProgressOverview.jsx');

const mount = (pageData, props = {}) => render(
    <MemoryRouter>
        <TaskProgressOverview pageData={pageData} {...props}/>
    </MemoryRouter>
);

// The stage name under the "当前阶段" caption and the big percentage beside it.
// The same stage word can also appear in the route tile, so the caption is
// the anchor, not the word.
const stageShown = () => screen.getByText('当前阶段').nextElementSibling.textContent;
const percentShown = () => screen.getByText('当前阶段').parentElement.nextElementSibling.textContent;

const pageFor = (task, extra = {}) => ({
    task: {task_id: 'task-1', title: '5.投资人视角下的AI浪潮', ...task},
    ...extra,
});

beforeEach(() => {
    currentJob = null;
    cancelCalls.length = 0;
});

afterEach(cleanup);

describe('a task that is still queued', () => {
    it('says it is waiting in line and has not started', () => {
        mount(pageFor({stage: 'queued', status: 'queued', progress: 0}));
        expect(stageShown()).toBe('排队中');
        expect(percentShown()).toBe('0%');
        expect(screen.getByText('任务记录')).toBeTruthy();
        expect(screen.getByText(/当前阶段：排队中 · 进度：0%/)).toBeTruthy();
    });
});

describe('a task that is downloading', () => {
    it('names the video download step and shows how far it got', () => {
        mount(pageFor({stage: 'downloading', status: 'running', progress: 35, source_type: 'video_link'}));
        expect(stageShown()).toBe('下载视频');
        expect(percentShown()).toBe('35%');
    });

    it('says audio, not video, when the recording is an audio file', () => {
        mount(pageFor({stage: 'downloading', status: 'running', progress: 12, filename: '讲座.mp3'}));
        expect(stageShown()).toBe('下载音频');
        expect(screen.queryAllByText('下载视频')).toHaveLength(0);
    });
});

describe('a task that is transcribing', () => {
    it('shows the transcribing step with its progress', () => {
        mount(pageFor({stage: 'stt', status: 'running', progress: 58}));
        expect(stageShown()).toBe('转录中');
        expect(percentShown()).toBe('58%');
    });

    it('says the step is working rather than inventing a number while the engine has not measured anything', () => {
        currentJob = {taskId: 'task-1', fileName: '讲座.mp4', stage: 'stt', progress: 40, sttProgress: 0, sttStatus: 'loading_model', startedAt: Date.now()};
        mount(pageFor({stage: 'stt', status: 'running', progress: 40}));
        expect(screen.getByText('当前任务')).toBeTruthy();
        expect(stageShown()).toBe('转录中');
        expect(percentShown()).toBe('处理中');
        expect(screen.getByText('正在加载本地模型')).toBeTruthy();
        expect(screen.queryByText('40%')).toBeNull();
        expect(screen.getByRole('button', {name: '取消任务'})).toBeTruthy();
    });
});

describe('a task that is writing the note', () => {
    it('names the note step', () => {
        mount(pageFor({stage: 'summary', status: 'running', progress: 90}));
        expect(stageShown()).toBe('生成笔记');
        expect(percentShown()).toBe('90%');
    });
});

describe('a finished task', () => {
    it('reads as complete at 100% with its title, even when the stored progress lags', () => {
        mount(pageFor({stage: 'done', status: 'completed', progress: 97}));
        expect(stageShown()).toBe('已完成');
        expect(percentShown()).toBe('100%');
        expect(screen.getByText('已完成记录')).toBeTruthy();
        expect(screen.getByText('5.投资人视角下的AI浪潮')).toBeTruthy();
        expect(screen.getByText('处理已完成，可以打开结果继续复查。')).toBeTruthy();
    });

    it('still reads as complete when only the status says so', () => {
        mount(pageFor({status: 'completed'}));
        expect(stageShown()).toBe('已完成');
        expect(percentShown()).toBe('100%');
    });
});

describe('a failed task', () => {
    it('shows the friendly reason from the diagnosis instead of a percentage', () => {
        mount(pageFor(
            {stage: 'failed', status: 'failed', progress: 42},
            {diagnosis: {detail: 'DeepSeek 的 API Key 不被接受，请到设置页重新填写。', next_action: '到设置页检查 DeepSeek API Key。'}},
        ));
        expect(stageShown()).toBe('失败');
        expect(screen.getByText('处理失败')).toBeTruthy();
        expect(percentShown()).toBe('-');
        expect(screen.queryByText('42%')).toBeNull();
        expect(screen.getByText('DeepSeek 的 API Key 不被接受，请到设置页重新填写。')).toBeTruthy();
    });

    it('falls back to the next step when the diagnosis has no detail', () => {
        mount(pageFor({stage: 'failed', status: 'failed', progress: 0}, {diagnosis: {next_action: '重新打开 FluentFlow Local 后再试一次。'}}));
        expect(screen.getByText('重新打开 FluentFlow Local 后再试一次。')).toBeTruthy();
    });

    it('still tells the person to look at the reason when there is no diagnosis at all', () => {
        mount(pageFor({stage: 'error', status: 'failed', progress: 0}));
        expect(stageShown()).toBe('失败');
        expect(screen.getByText('查看失败原因，按建议重新处理。')).toBeTruthy();
    });
});

describe('a link download of unknown size', () => {
    it('shows how much has arrived, not the stage starting 10%', () => {
        mount(pageFor(
            {stage: 'downloading', status: 'running', progress: 10, source_type: 'video_link'},
            {source: {video_source_progress: {message: '正在下载视频', loaded_bytes: 10485760}}},
        ));
        expect(percentShown()).toMatch(/已下载 10(\.0)? MB/);
    });
});
