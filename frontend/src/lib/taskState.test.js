import {describe, expect, it} from 'vitest';
import {taskStatusLabel, isNoteStillBeingWritten} from './taskState.js';

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
