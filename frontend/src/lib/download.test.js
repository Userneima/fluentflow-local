// @vitest-environment jsdom

// Exporting a transcript or note. Written from what the person gets on disk:
// a file named after the recording, in the format they picked, whose content a
// subtitle player or Markdown reader accepts. The browser's actual save dialog
// is not exercised; the anchor click is captured instead and the blob read back.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

// Whether the Word library has been pulled in yet. It is a large dependency
// that only a .docx export needs, so loading it when the editor opens would
// slow every page for a button most people never press.
const docxLoad = vi.hoisted(() => ({count: 0}));
vi.mock('docx', async (importOriginal) => {
    docxLoad.count += 1;
    return await importOriginal();
});

const download = await import('./download.js');
const {
    _baseName,
    _fmtSrtTime,
    _fmtVttTime,
    buildSummaryDocxDocument,
    dlBilingualTranscriptSrt,
    dlBilingualTranscriptVtt,
    dlSummaryMd,
    dlSummaryTxt,
    dlTranscriptSrt,
    dlTranscriptTxt,
    dlTranscriptVtt,
} = download;

// What the last export handed to the browser.
let saved = null;
const readBlob = (blob) => (typeof blob.text === 'function'
    ? blob.text()
    : new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.readAsText(blob);
    }));
const lastExport = async () => ({name: saved.name, type: saved.blob.type, text: await readBlob(saved.blob)});

beforeEach(() => {
    saved = null;
    URL.createObjectURL = vi.fn((blob) => { saved = {blob, name: null}; return 'blob:fluentflow-test'; });
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
        if (saved) saved.name = this.download;
    });
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('the exported file name', () => {
    it('keeps a recording title that contains dots, dropping only a real media extension', () => {
        expect(_baseName('5.投资人视角下的AI浪潮')).toBe('5.投资人视角下的AI浪潮');
        expect(_baseName('5.投资人视角下的AI浪潮.mp4')).toBe('5.投资人视角下的AI浪潮');
        expect(_baseName('第3讲.v2.final.MOV')).toBe('第3讲.v2.final');
    });

    it('falls back to the product name when the record has no title', () => {
        expect(_baseName('')).toBe('FluentFlow');
        expect(_baseName(undefined)).toBe('FluentFlow');
        expect(_baseName('.mp4')).toBe('FluentFlow');
    });

    it('replaces characters a file system rejects', () => {
        const name = _baseName('AI浪潮: 投资人/创业者的视角?');
        expect(name).not.toMatch(/[/\\:*?"<>|]/);
        expect(name).toContain('AI浪潮');
    });

    it('matches the extension to the format the person picked', async () => {
        const segments = [{start: 0, end: 1, text: 'hi'}];
        const cases = [
            [() => dlTranscriptTxt('hi', '讲座.mp4'), '讲座.txt'],
            [() => dlTranscriptSrt(segments, '讲座.mp4'), '讲座.srt'],
            [() => dlTranscriptVtt(segments, '讲座.mp4'), '讲座.vtt'],
            [() => dlBilingualTranscriptSrt(segments, [], '讲座.mp4'), '讲座_bilingual_zh.srt'],
            [() => dlBilingualTranscriptVtt(segments, [], '讲座.mp4'), '讲座_bilingual_zh.vtt'],
            [() => dlSummaryTxt('# 笔记', '讲座.mp4'), '讲座_summary.txt'],
            [() => dlSummaryMd('# 笔记', '讲座.mp4'), '讲座_summary.md'],
        ];
        for (const [run, expected] of cases) {
            run();
            expect((await lastExport()).name).toBe(expected);
        }
    });
});

describe('subtitle timestamps', () => {
    it('writes SRT time as HH:MM:SS,mmm and VTT time as HH:MM:SS.mmm', () => {
        expect(_fmtSrtTime(0)).toBe('00:00:00,000');
        expect(_fmtSrtTime(3661.5)).toBe('01:01:01,500');
        expect(_fmtSrtTime(59.25)).toBe('00:00:59,250');
        expect(_fmtVttTime(3661.5)).toBe('01:01:01.500');
        expect(_fmtVttTime(7.007)).toBe('00:00:07.007');
    });

    // Milliseconds are rounded on their own, so 1.9995s becomes "00:00:01,1000":
    it('carries a rounded-up millisecond into the seconds field', () => {
        expect(_fmtSrtTime(1.9995)).toBe('00:00:02,000');
        expect(_fmtVttTime(59.9996)).toBe('00:01:00.000');
    });
});

describe('an SRT export', () => {
    it('numbers cues from 1 in order with a blank line between them', async () => {
        dlTranscriptSrt([
            {start: 0, end: 2.5, text: '  大家好 '},
            {start: 2.5, end: 5, text: '今天讲 AI'},
        ], '讲座');
        const out = await lastExport();
        expect(out.text).toBe('1\n00:00:00,000 --> 00:00:02,500\n大家好\n\n2\n00:00:02,500 --> 00:00:05,000\n今天讲 AI\n');
    });

    it('skips a segment whose text is blank', async () => {
        dlTranscriptSrt([
            {start: 0, end: 1, text: '第一句'},
            {start: 1, end: 2, text: '   '},
            {start: 2, end: 3, text: '第三句'},
        ], '讲座');
        const out = await lastExport();
        expect(out.text).not.toContain('00:00:01,000 --> 00:00:02,000');
        expect(out.text).toContain('2\n00:00:02,000 --> 00:00:03,000\n第三句');
    });
});

describe('a VTT export', () => {
    it('starts with the WEBVTT header and lists cues in order', async () => {
        dlTranscriptVtt([
            {start: 0, end: 1.2, text: 'one'},
            {start: 1.2, end: 3, text: 'two'},
        ], '讲座');
        const out = await lastExport();
        expect(out.type).toContain('text/vtt');
        expect(out.text).toBe('WEBVTT\n\n00:00:00.000 --> 00:00:01.200\none\n\n00:00:01.200 --> 00:00:03.000\ntwo\n');
    });
});

describe('a bilingual subtitle export', () => {
    it('puts the Chinese line under the original and skips segments with no text at all', async () => {
        dlBilingualTranscriptSrt([
            {start: 0, end: 1, text: 'Hello', text_zh: '你好'},
            {start: 1, end: 2, text: '', text_zh: ''},
            {start: 2, end: 3, text: 'Bye'},
        ], [null, null, {text: '再见'}], '讲座');
        const out = await lastExport();
        expect(out.text).toBe('1\n00:00:00,000 --> 00:00:01,000\nHello\n你好\n\n2\n00:00:02,000 --> 00:00:03,000\nBye\n再见\n');
    });
});

describe('a note export', () => {
    it('writes the Markdown exactly as the person sees it', async () => {
        const md = '# 投资人视角\n\n- 要点一\n- 要点二\n\n| 列 | 值 |\n|---|---|\n| a | 1 |\n';
        dlSummaryMd(md, '讲座.mp4');
        const out = await lastExport();
        expect(out.type).toContain('text/markdown');
        expect(out.text).toBe(md);
    });

    it('writes the plain-text note with the same content as the Markdown one', async () => {
        const md = '# 标题\n\n正文一段。';
        dlSummaryTxt(md, '讲座.mp4');
        const out = await lastExport();
        expect(out.type).toContain('text/plain');
        expect(out.text).toBe(md);
    });
});

describe('a Word export', () => {
    it('loads the Word library only when a .docx is actually built', async () => {
        expect(docxLoad.count).toBe(0);
        const doc = await buildSummaryDocxDocument('# 标题\n\n正文一段，**加粗**和 `代码`。\n\n- 要点\n\n> 引用\n');
        expect(docxLoad.count).toBe(1);
        const docx = await import('docx');
        expect(doc).toBeInstanceOf(docx.Document);
    });
});
