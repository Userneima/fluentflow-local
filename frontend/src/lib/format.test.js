import {describe, expect, it} from 'vitest';
import {displayTitleForUser, fileNameStem, jobProgressLabel} from './format.js';
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
