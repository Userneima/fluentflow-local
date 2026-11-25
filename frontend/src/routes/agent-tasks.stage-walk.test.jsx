// @vitest-environment jsdom

// A link task walked through every stage the service moves it through, with
// the row shaped the way the service sends it at each point: metadata keeps
// whatever earlier stages wrote (the download's progress line, its bytes),
// while the top-level progress_message is only sent during the stage that
// wrote it (job_store._progress_message).
//
// Written from the owner's run of 2026-11-21 (docs/01-待办.md, 「界面如实、及时
// 显示后台在做什么」): the card must say what the current stage is doing and
// never a line an earlier stage left behind. It read 「正在保存视频信息」 from the
// end of the download through transcription and correction, and 「已完成」 with
// an empty note while the note still took 7 minutes.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useApi: () => ({openOutputFolder: async () => ({})}),
    };
});

const {default: AgentTaskCard} = await import('./agent-tasks-card.jsx');
const {liveStageDetail, stageLabel} = await import('./agent-tasks-labels.js');
const {taskOutcome} = await import('../lib/taskState.js');

const NOW = Date.parse('2026-11-21T20:20:00+08:00');
const ago = (seconds) => new Date(NOW - seconds * 1000).toISOString();

// What the download stages write, in order; each later stage keeps them in
// metadata, as the service's metadata merge does.
const DOWNLOAD_LINES = {
    resolving: '正在解析分享链接',
    downloading: '正在下载视频',
    saving: '正在保存视频信息',
};

const linkRow = ({stage, status = 'running', progress = 50, metadata = {}, result = null, activity = null}) => {
    const lastDownload = {message: DOWNLOAD_LINES.saving, percent: 96, loaded_bytes: 104857600, total_bytes: 104857600};
    const merged = {
        display_title: '一条链接视频',
        progress_message: DOWNLOAD_LINES.saving,
        progress_message_stage: 'saving',
        video_source_progress: lastDownload,
        ...metadata,
    };
    // The service sends the line only while the stage that wrote it lasts.
    const top = ['queued', 'running'].includes(status) && merged.progress_message_stage === stage
        ? merged.progress_message : null;
    return {
        task_id: 'walk',
        status,
        stage,
        progress,
        source_type: 'video_link',
        source_filename: 'walk.mp4',
        created_at: ago(900),
        updated_at: ago(1),
        metadata: merged,
        progress_message: top,
        result,
        activity,
    };
};

const transcribed = {transcript_text: '有转录', summary_status: 'pending', summary_chars: 0, debreath: {status: 'completed', plan: {cut_count: 3, removed_seconds: 12}}};

// Every stage, what the card must say there (a phrase of it), and what it
// must not say: anything a stage before it wrote.
const WALK = [
    {
        name: 'resolving',
        row: linkRow({stage: 'resolving', progress: 8, metadata: {
            progress_message: DOWNLOAD_LINES.resolving, progress_message_stage: 'resolving', video_source_progress: {message: DOWNLOAD_LINES.resolving},
        }}),
        says: ['解析链接', DOWNLOAD_LINES.resolving],
    },
    {
        name: 'downloading',
        row: linkRow({stage: 'downloading', progress: 40, metadata: {
            progress_message: DOWNLOAD_LINES.downloading,
            progress_message_stage: 'downloading',
            video_source_progress: {
                message: DOWNLOAD_LINES.downloading, percent: 40, loaded_bytes: 41943040, total_bytes: 104857600,
                speed_bps: 3355443, eta_seconds: 80,
            },
        }}),
        says: ['下载视频', '40.0 MB / 100.0 MB', '3.20 MB/s', '剩余约 1 分 20 秒'],
        never: [DOWNLOAD_LINES.resolving],
    },
    {
        name: 'downloading, speed unknown',
        row: linkRow({stage: 'downloading', progress: 40, metadata: {
            progress_message: DOWNLOAD_LINES.downloading,
            progress_message_stage: 'downloading',
            video_source_progress: {message: DOWNLOAD_LINES.downloading, percent: 40, loaded_bytes: 41943040, total_bytes: 104857600},
        }}),
        says: ['40.0 MB / 100.0 MB'],
        // No speed is said as none, not as 0.
        never: ['/s', '剩余', '0 B'],
    },
    {
        name: 'saving',
        row: linkRow({stage: 'saving', progress: 96}),
        says: [DOWNLOAD_LINES.saving],
        never: [DOWNLOAD_LINES.downloading, DOWNLOAD_LINES.resolving],
    },
    {name: 'audio', row: linkRow({stage: 'audio', progress: 10}), says: ['提取音频']},
    {name: 'stt', row: linkRow({stage: 'stt', progress: 30}), says: ['转录']},
    {
        name: 'subtitle_correction',
        row: linkRow({stage: 'subtitle_correction', progress: 60, metadata: {stage_started_at: ago(45)}}),
        says: ['正在按画面字幕校正转录稿（已等 45 秒）'],
    },
    {
        name: 'glossary_correction',
        row: linkRow({stage: 'glossary_correction', progress: 60, metadata: {stage_started_at: ago(130)}}),
        says: ['正在按词表校正转录稿（已等 2 分 10 秒）'],
        never: ['较慢'],
    },
    {
        // One read may take up to 300 seconds (ai_client.REQUEST_TIMEOUT):
        // a legal wait, said as one before it runs out.
        name: 'glossary_correction, slow model',
        row: linkRow({stage: 'glossary_correction', progress: 60, metadata: {stage_started_at: ago(260)}}),
        says: ['正在按词表校正转录稿（已等 4 分 20 秒）', '模型响应较慢，仍在等待'],
    },
    {
        // Review, 2026-11-26: the correction asks in batches, four at a time
        // (backend glossary_correction, 3000 characters each); a lecture's 14
        // batches pass four minutes with every answer on time. The wait that
        // can be slow is the one since the last answer, and how far it has
        // got is said instead.
        name: 'glossary_correction, batches answering on time',
        row: linkRow({stage: 'glossary_correction', progress: 60, metadata: {
            stage_started_at: ago(300),
            model_batches: {stage: 'glossary_correction', done: 12, total: 14, last_answer_at: ago(40), updated_at: ago(40)},
        }}),
        says: ['正在按词表校正转录稿（已校正 12/14 段，上一段 40 秒前返回）'],
        never: ['较慢', '已等 5 分钟'],
    },
    {
        // No batch has answered yet: the wait is since the stage began.
        name: 'glossary_correction, first batches still out',
        row: linkRow({stage: 'glossary_correction', progress: 60, metadata: {
            stage_started_at: ago(50),
            model_batches: {stage: 'glossary_correction', done: 0, total: 14, updated_at: ago(50)},
        }}),
        says: ['正在按词表校正转录稿（已校正 0/14 段，已等 50 秒）'],
        never: ['较慢'],
    },
    {
        // One request really stuck: nothing has answered for over 4 minutes.
        name: 'glossary_correction, one batch slow',
        row: linkRow({stage: 'glossary_correction', progress: 60, metadata: {
            stage_started_at: ago(600),
            model_batches: {stage: 'glossary_correction', done: 13, total: 14, last_answer_at: ago(250), updated_at: ago(250)},
        }}),
        says: ['已校正 13/14 段，上一段 4 分 10 秒前返回', '模型响应较慢，仍在等待'],
    },
    {
        // Left by an earlier run of the stage, before it was carried on, or
        // by another stage: not read.
        name: 'glossary_correction, batches from an earlier run',
        row: linkRow({stage: 'glossary_correction', progress: 60, metadata: {
            stage_started_at: ago(30),
            model_batches: {stage: 'glossary_correction', done: 9, total: 14, last_answer_at: ago(900), updated_at: ago(900)},
        }}),
        says: ['正在按词表校正转录稿（已等 30 秒）'],
        never: ['9/14'],
    },
    {
        name: 'translation, batches',
        row: linkRow({stage: 'translation', progress: 61, metadata: {
            stage_started_at: ago(400),
            model_batches: {stage: 'translation', done: 3, total: 5, last_answer_at: ago(15), updated_at: ago(15)},
        }}),
        says: ['正在把转录稿翻译成中文字幕（已翻译 3/5 段，上一段 15 秒前返回）'],
        never: ['较慢'],
    },
    {
        name: 'translation, a count the correction left behind',
        row: linkRow({stage: 'translation', progress: 61, metadata: {
            stage_started_at: ago(20),
            model_batches: {stage: 'glossary_correction', done: 14, total: 14, last_answer_at: ago(25), updated_at: ago(25)},
        }}),
        says: ['已等 20 秒'],
        never: ['14/14'],
    },
    {
        name: 'translation',
        row: linkRow({stage: 'translation', progress: 61, metadata: {stage_started_at: ago(20)}}),
        says: ['翻译', '已等 20 秒'],
    },
    {name: 'transcript_ready', row: linkRow({stage: 'transcript_ready', progress: 90}), says: ['整理转录']},
    {
        // The transcript is stored (the service says completed) and the cut
        // is being judged; the note waits for it. The pipeline left
        // summary_status "skipped" on the result: the note step owns it now.
        name: 'cut, after the transcript',
        row: linkRow({
            stage: 'done',
            status: 'completed',
            progress: 100,
            result: {transcript_text: '有转录', summary_status: 'skipped', summary_skipped: true},
            activity: {
                busy: true, label: '正在剪辑', current_step: 'cut', open_steps: ['cut', 'note', 'compress'], failed_steps: [],
                stopped: false, stopped_steps: [], resume_from: null, note_progress: null,
            },
        }),
        says: ['去气口 正在剪', '笔记 剪完再写'],
        never: ['旧任务', '已跳过'],
        outcome: 'live',
    },
    {
        // The transcript is stored (the service says completed) and the note
        // is being written by FluentFlow's own writer, which reports how far.
        name: 'note, with the writer reporting',
        row: linkRow({
            stage: 'note',
            status: 'completed',
            progress: 100,
            result: transcribed,
            activity: {
                busy: true, label: '正在写笔记', current_step: 'note', open_steps: ['note'], failed_steps: [],
                stopped: false, stopped_steps: [], resume_from: null,
                note_progress: {stage: 'writing', chapters: [{done: true}, {done: true}, {done: false}, {done: false}, {done: false}], elapsed_seconds: 200},
            },
        }),
        says: ['转录已完成。正在写各章：已写好 2/5 章，已用 3 分 20 秒'],
        never: ['已完成记录'],
        outcome: 'live',
    },
    {
        // Written by Claude or the user's agent: nothing reports how far.
        name: 'note, nothing reporting',
        row: linkRow({
            stage: 'note',
            status: 'completed',
            progress: 100,
            result: transcribed,
            activity: {
                busy: true, label: '正在写笔记', current_step: 'note', open_steps: ['note'], failed_steps: [],
                stopped: false, stopped_steps: [], resume_from: null, note_progress: null,
            },
        }),
        says: ['转录已完成，正在写笔记'],
        outcome: 'live',
    },
    {
        // Carried on in place after the service restarted under it.
        name: 'stt, resumed after a restart',
        row: linkRow({stage: 'stt', progress: 30, metadata: {restart_resumed: {step: 'transcribe', at: ago(30)}}}),
        says: ['服务重启后已从断点恢复', '转录'],
    },
];

// Lines every stage after the download must leave behind.
const AFTER_DOWNLOAD = ['audio', 'stt', 'subtitle_correction', 'glossary_correction', 'translation', 'transcript_ready', 'done', 'note'];

const cardText = (row) => {
    const {container} = render(
        <MemoryRouter>
            <AgentTaskCard
                job={row}
                lang="zh"
                onCancel={() => {}} onDelete={() => {}} onDownloadCut={() => {}} onOpenResult={() => {}}
                onOpenSteps={() => {}} onPin={() => {}} onRetry={() => {}} onResume={() => {}} onStop={() => {}}
            />
        </MemoryRouter>,
    );
    return container.textContent;
};

describe('the card through every stage of a link task', () => {
    afterEach(() => {
        cleanup();
        vi.useRealTimers();
    });

    for (const step of WALK) {
        it(`says what ${step.name} is doing, and nothing an earlier stage left`, () => {
            vi.useFakeTimers({toFake: ['Date']});
            vi.setSystemTime(NOW);
            const text = cardText(step.row);
            for (const phrase of step.says) expect(text).toContain(phrase);
            const stale = [...(step.never || [])];
            if (AFTER_DOWNLOAD.some((stage) => step.row.stage === stage)) stale.push(...Object.values(DOWNLOAD_LINES), '100.0 MB / 100.0 MB');
            for (const phrase of stale) expect(text).not.toContain(phrase);
            if (step.outcome) expect(taskOutcome(step.row, 'zh').kind).toBe(step.outcome);
        });
    }

    it('names every stage it walks through, never the generic 处理中', () => {
        for (const step of WALK.filter((each) => each.row.status !== 'completed')) {
            expect(stageLabel(step.row, 'zh'), step.name).not.toBe('处理中');
        }
    });

    it('drops a stale download line a row written by an older service still carries', () => {
        // Older rows have no progress_message_stage: the line cannot be tied
        // to a stage, so a stage that is not the download does not show it.
        const row = linkRow({stage: 'stt', progress: 30, metadata: {progress_message_stage: undefined}});
        expect(liveStageDetail(row, 'zh')).not.toContain(DOWNLOAD_LINES.saving);
    });
});
