// @vitest-environment jsdom

// The records card says one line per task, and it has already been wrong once: a
// task whose cut had just succeeded reported "未剪（旧任务）", because the list
// payload did not carry the cut block. These cases are pinned here so the four
// answers stay distinguishable — never cut, cut nothing, arrived already cut, and
// declined — since three of them look alike and only one of them is good news.

import {describe, expect, it} from 'vitest';
import {cutFileDelivery, debreathTile} from './agent-tasks.jsx';

const job = (debreath) => ({result: {debreath}});

describe('debreathTile', () => {
    it('separates a task that was never cut from one that found nothing', () => {
        expect(debreathTile({result: {}}, 'zh').value).toBe('未剪（旧任务）');
        expect(debreathTile(job({status: 'completed', plan: {cut_count: 0}}), 'zh').value)
            .toBe('没有可剪的空白');
    });

    it('says a file that arrived already cut was left alone', () => {
        const tile = debreathTile(
            job({
                status: 'completed',
                already_cut: true,
                used_for_transcription: false,
                not_used_reason: '这份文件是 FluentFlow 剪过的（文件里有标记），不再剪第二遍。',
            }),
            'zh'
        );
        expect(tile.value).toBe('本来就是剪后版本');
    });

    it('says a tiny saving was not worth re-exporting, which is not a failure', () => {
        const tile = debreathTile(
            job({
                status: 'completed',
                not_worth_rendering: true,
                used_for_transcription: false,
                not_used_reason: '可剪的空白只有 0.3 秒（占 0.0%），重新导出一遍整个文件不值得。',
                plan: {cut_count: 2, removed_seconds: 0.3},
            }),
            'zh'
        );
        expect(tile.value).toBe('几乎没空白可剪');
    });

    it('still says "used the original" when the cut declined for safety', () => {
        const tile = debreathTile(
            job({status: 'completed', used_for_transcription: false, not_used_reason: '分离度不够。'}),
            'zh'
        );
        expect(tile.value).toBe('按原片处理');
    });

    it('reports the saving when the cut was used', () => {
        const tile = debreathTile(
            job({status: 'completed', used_for_transcription: true, plan: {cut_count: 642, removed_seconds: 245}}),
            'zh'
        );
        expect(tile.value).toMatch(/642/);
    });
});


// ── where the cut file ended up ────────────────────────────────────────────
//
// The card speaks for two of the three states and stays silent for the third.
// Silence is the design, not an omission: a line saying "saved next to the
// original" on every record is a column of the same words, and the name is the
// recording's own with a suffix. What the owner did not know was that this
// happens at all, and that belongs where they choose the entry — not repeated on
// a hundred records.

const delivered = (extra) => ({result: {debreath: {status: 'completed', rendered: true, ...extra}}});

describe('cutFileDelivery', () => {
    it('says nothing when the cut file landed where it belongs', () => {
        expect(cutFileDelivery(delivered({delivered: true, delivered_name: 'a_debreath.mp4'}), 'zh')).toBeNull();
    });

    it('warns, with the reason, when the copy beside the original failed', () => {
        const notice = cutFileDelivery(delivered({delivered: false, delivery_error: '目标文件夹只读'}), 'zh');

        expect(notice.tone).toBe('error');
        expect(notice.text).toBe('目标文件夹只读');
    });

    it('still warns when the failure arrived without a reason attached', () => {
        const notice = cutFileDelivery(delivered({delivered: false}), 'zh');

        expect(notice.tone).toBe('error');
        expect(notice.text).toMatch(/没能存到原文件旁边/);
    });

    it('tells an uploaded task its cut file stayed in the app', () => {
        // No "beside the original" exists for an upload: the copy FluentFlow holds
        // *is* the original as far as it knows. The owner's folder gets nothing,
        // and they will go looking for it there.
        const notice = cutFileDelivery(delivered({}), 'zh');

        expect(notice.tone).toBe('note');
        expect(notice.text).toMatch(/留在应用里/);
    });

    it('does not call an old in-place record an upload', () => {
        // Absence of the field could equally mean "recorded before delivery was
        // built". Sending the owner to the app for a file that is already in their
        // folder is the wrong answer, so the note needs the folder to be missing.
        expect(cutFileDelivery({...delivered({}), metadata: {folder_intake: {folder: '/x'}}}, 'zh')).toBeNull();
    });

    it('says nothing about a task that never rendered a cut file', () => {
        expect(cutFileDelivery({result: {}}, 'zh')).toBeNull();
        expect(cutFileDelivery(delivered({used_for_transcription: false}), 'zh')).toBeNull();
        expect(cutFileDelivery(delivered({already_cut: true}), 'zh')).toBeNull();
        expect(cutFileDelivery(delivered({not_worth_rendering: true}), 'zh')).toBeNull();
    });

    it('says nothing while the cut is still running', () => {
        expect(cutFileDelivery({result: {debreath: {status: 'running'}}}, 'zh')).toBeNull();
    });
});
