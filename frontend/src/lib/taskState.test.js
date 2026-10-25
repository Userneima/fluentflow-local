import {describe, expect, it} from 'vitest';
import {taskStatusLabel, isNoteStillBeingWritten, taskOutcome} from './taskState.js';

// Requirement (2026-10-14): while a finished transcript's note is still being
// written, the task does not read "已完成"; it reads "写笔记中" until the note
// is written, failed or skipped.
describe('task status while the note is being written', () => {
    const transcriptDone = (summaryStatus) => ({status: 'completed', result: {summary_status: summaryStatus}});

    it('says the note is being written, not completed', () => {
        expect(taskStatusLabel(transcriptDone('pending'), 'zh')).toBe('写笔记中');
        expect(isNoteStillBeingWritten(transcriptDone('pending'))).toBe(true);
    });

    it('reads the list row shape too', () => {
        expect(taskStatusLabel({status: 'completed', summary_status: 'pending'}, 'zh')).toBe('写笔记中');
    });

    it.each(['completed', 'failed', 'skipped', undefined])('reads completed once the note is %s', (summaryStatus) => {
        expect(taskStatusLabel(transcriptDone(summaryStatus), 'zh')).toBe('已完成');
    });

    it('leaves running and failed tasks alone', () => {
        expect(taskStatusLabel({status: 'running', result: {summary_status: 'pending'}}, 'zh')).toBe('处理中');
        expect(taskStatusLabel({status: 'failed'}, 'zh')).toBe('失败');
    });
});

// Walkthrough 2026-10-22 and the to-do "任务「完成」指每一步都做完": a record
// whose cut or note did not happen does not wear the green 已完成. Until the
// step model lands, the badge says what is missing; only a task with nothing
// missing and nothing left for the owner reads 已完成.
describe('what a record badge says', () => {
    const done = (result) => ({status: 'completed', result: {summary_status: 'completed', summary_markdown: '# 笔记', ...result}});

    it('reads 已完成 only when the cut and the note both happened', () => {
        expect(taskOutcome(done({debreath: {status: 'completed'}}), 'zh')).toMatchObject({tone: 'done', label: '已完成'});
    });

    it('says the note was not written', () => {
        const outcome = taskOutcome(done({summary_status: 'failed', summary_error: 'timeout', summary_markdown: ''}), 'zh');
        expect(outcome.label).toBe('笔记没生成');
        expect(outcome.tone).toBe('warn');
    });

    it('reads the list row, which carries the note length instead of the note', () => {
        expect(taskOutcome({status: 'completed', result: {summary_chars: 0, summary_status: 'failed'}}, 'zh').label).toBe('笔记没生成');
        expect(taskOutcome({status: 'completed', result: {summary_chars: 900, summary_status: 'completed'}}, 'zh').label).toBe('已完成');
    });

    it('says there is only a transcript when no model key was filled in', () => {
        expect(taskOutcome(done({summary_markdown: '', summary_status: 'skipped', summary_skipped_reason: 'no_model_key'}), 'zh').label).toBe('只有转录稿');
    });

    it('does not flag a note the person chose to skip', () => {
        expect(taskOutcome(done({summary_markdown: '', summary_status: 'skipped', summary_skipped: true}), 'zh').tone).toBe('done');
    });

    it('says the breath-gap cut failed', () => {
        const outcome = taskOutcome(done({debreath: {status: 'failed'}}), 'zh');
        expect(outcome.label).toBe('没去成气口');
        expect(outcome.tone).toBe('warn');
    });

    it('names how many places wait for the owner to judge', () => {
        expect(taskOutcome(done({debreath: {status: 'completed', survey: {to_judge: 6}}}), 'zh').label).toBe('6 处待你判断');
    });

    it('keeps failed, cancelled and live tasks as they were', () => {
        expect(taskOutcome({status: 'failed'}, 'zh')).toMatchObject({tone: 'failed', label: '失败'});
        expect(taskOutcome({status: 'cancelled'}, 'zh')).toMatchObject({tone: 'failed', label: '已取消'});
        expect(taskOutcome({status: 'running'}, 'zh')).toMatchObject({tone: 'live', label: '处理中'});
        expect(taskOutcome({status: 'completed', result: {summary_status: 'pending'}}, 'zh')).toMatchObject({tone: 'live', label: '写笔记中'});
    });
});

// Walkthrough review 2026-10-22: a finished task whose cut is being redone is
// still producing a result, so it is not a green 已完成.
describe('a finished task whose cut is being redone', () => {
    it('reads as in progress', () => {
        const outcome = taskOutcome({status: 'completed', result: {summary_status: 'completed', summary_chars: 900, debreath: {status: 'running'}}}, 'zh');
        expect(outcome.kind).toBe('live');
        expect(outcome.label).toBe('正在剪气口');
    });
});

// Step list (docs/方案/2026-10-25-任务步骤清单和模型用量记账.md): the backend
// says per task whether it is still working and on what, from its steps. A
// task whose transcript is stored but whose cut, note, compressed copy or
// Feishu export is still to come reads as in progress, in those words.
describe('a task the backend says is still working', () => {
    const working = (label, openSteps) => ({
        status: 'completed',
        result: {summary_status: 'skipped'},
        activity: {busy: true, label, current_step: openSteps[0], open_steps: openSteps, waiting_for_agent: false, failed_steps: []},
    });

    it('reads what it is doing, not 已完成', () => {
        expect(taskStatusLabel(working('正在剪辑', ['cut', 'note']), 'zh')).toBe('正在剪辑');
        expect(taskOutcome(working('正在压缩副本', ['compress']), 'zh')).toMatchObject({kind: 'live', label: '正在压缩副本'});
    });

    it('reads 写笔记中 while the note step is open', () => {
        expect(taskStatusLabel(working('正在写笔记', ['note']), 'zh')).toBe('写笔记中');
    });

    it('is finished once the backend says nothing is left, whatever an old field says', () => {
        const done = {
            status: 'completed',
            result: {summary_status: 'pending', summary_chars: 900},
            activity: {busy: false, label: null, open_steps: [], waiting_for_agent: false, failed_steps: []},
        };
        expect(taskStatusLabel(done, 'zh')).toBe('已完成');
        expect(taskOutcome(done, 'zh').kind).toBe('done');
    });
});
