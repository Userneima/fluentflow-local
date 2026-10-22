import {describe, expect, it} from 'vitest';
import {displayTitleForUser, fileNameStem, fmtDuration, jobProgressLabel} from './format.js';
import {_baseName} from './download.js';
import {noteTileValue} from '../routes/agent-tasks.jsx';

describe('titles named "number.title"', () => {
    it('keeps everything after the first dot', () => {
        expect(displayTitleForUser('5.投资人视角下的AI浪潮')).toBe('5.投资人视角下的AI浪潮');
        expect(displayTitleForUser('1.2-1.3 批判性思维_合并')).toBe('1.2-1.3 批判性思维_合并');
    });

    it('still removes a real extension', () => {
        expect(fileNameStem('5.投资人视角下的AI浪潮.m4a')).toBe('5.投资人视角下的AI浪潮');
        expect(fileNameStem('lecture.MP4')).toBe('lecture');
    });

    it('names downloads after the whole title', () => {
        expect(_baseName('5.投资人视角下的AI浪潮')).toBe('5.投资人视角下的AI浪潮');
        expect(_baseName('')).toBe('FluentFlow');
    });
});

describe('note length on the records card', () => {
    it('uses the real length the list sends, not the 240-character preview', () => {
        const job = {result: {summary_markdown: 'x'.repeat(240), summary_chars: 3523}};
        expect(noteTileValue(job, 'zh')).toBe('3523 字');
    });

    it('counts the note itself when it has the whole thing', () => {
        expect(noteTileValue({result: {summary_markdown: '一二三'}}, 'zh')).toBe('3 字');
    });
});

describe('removing the breath gaps shows how far it got', () => {
    const t = (key) => (key === 'dash.progressUnknown' ? '进行中' : key);
    it('shows the percentage once the render reports one', () => {
        const job = {stage: 'prepare_media', progress: 10, metadata: {prepare_fraction: 0.5}};
        expect(jobProgressLabel(job, t)).toBe('10%');
    });
    it('shows no number while it is still finding the silences', () => {
        const job = {stage: 'prepare_media', progress: 2, metadata: {prepare_fraction: null}};
        expect(jobProgressLabel(job, t)).toBe('进行中');
    });
});

// Requirement (2026-10-18): codes and raw system errors are not shown as they are.
import {diagnoseTaskError as diagnoseFailure} from './format.js';
import {describe as describeFailures, expect as expectFailures, it as itFailure} from 'vitest';
describeFailures('a raw failure on the page', () => {
    itFailure('comes out in Chinese with a next step', () => {
        for (const raw of ['user_cancelled', 'client_disconnect', "[Errno 1] Operation not permitted: '/x/a.mp4'"]) {
            const found = diagnoseFailure(raw, 'zh');
            expectFailures(found.detail).toMatch(/[一-鿿]/);
            expectFailures(found.nextAction).toMatch(/[一-鿿]/);
            expectFailures(found.detail + found.nextAction).not.toMatch(/Errno|_cancelled|client_disconnect/);
        }
    });
});

// Walkthrough 2026-10-22: lengths read "12min43s", "11m 24s", "3:34:38" and
// "02m28s" on neighbouring pages. One wording for every length.
describe('fmtDuration', () => {
    it('gives minutes and seconds under an hour', () => {
        expect(fmtDuration(763, 'zh')).toBe('12 分 43 秒');
        expect(fmtDuration(148, 'zh')).toBe('2 分 28 秒');
        expect(fmtDuration(763, 'en')).toBe('12m 43s');
    });

    it('drops the seconds past an hour', () => {
        expect(fmtDuration(3 * 3600 + 34 * 60 + 38, 'zh')).toBe('3 小时 34 分');
        expect(fmtDuration(3 * 3600 + 34 * 60 + 38, 'en')).toBe('3h 34m');
    });

    it('does not write a zero part', () => {
        expect(fmtDuration(600, 'zh')).toBe('10 分钟');
        expect(fmtDuration(7200, 'zh')).toBe('2 小时');
        expect(fmtDuration(45, 'zh')).toBe('45 秒');
        expect(fmtDuration(0, 'zh')).toBe('0 秒');
    });

    it('rounds to the nearest second and survives junk', () => {
        expect(fmtDuration(59.6, 'zh')).toBe('1 分钟');
        expect(fmtDuration(undefined, 'zh')).toBe('0 秒');
        expect(fmtDuration(-5, 'zh')).toBe('0 秒');
    });
});
