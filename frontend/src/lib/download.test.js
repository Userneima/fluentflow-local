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

// Bytes of an image file as far as its header goes. Exporting never decodes the
// picture, it only copies it and reads the size the header declares.
const pngHeader = (width, height) => {
    const bytes = new Uint8Array(33);
    bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
    const view = new DataView(bytes.buffer);
    view.setUint32(16, width);
    view.setUint32(20, height);
    bytes.set([8, 2, 0, 0, 0], 24);
    return bytes;
};
const jpegHeader = (width, height) => new Uint8Array([
    0xff, 0xd8,
    0xff, 0xe0, 0x00, 0x04, 0x00, 0x00,
    0xff, 0xc0, 0x00, 0x11, 0x08, height >> 8, height & 0xff, width >> 8, width & 0xff, 0x03,
    0, 0, 0, 0, 0, 0, 0, 0, 0,
]);

const blobBytes = (blob) => (typeof blob.arrayBuffer === 'function'
    ? blob.arrayBuffer()
    : new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.readAsArrayBuffer(blob);
    }));

// The app's screenshot endpoint, served from memory. Any other address fails.
const serveScreenshots = (files) => {
    const asked = [];
    vi.stubGlobal('fetch', vi.fn(async (input) => {
        const url = String(input);
        asked.push(url);
        const name = new URL(url, 'http://app.test').searchParams.get('file');
        if (!files[name]) return new Response('missing', {status: 404});
        return new Response(files[name], {headers: {'content-type': 'image/png'}});
    }));
    return asked;
};

describe('a Markdown note with screenshots', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it('downloads a folder whose Markdown points at its own copies of the screenshots', async () => {
        const shot = pngHeader(1280, 720);
        serveScreenshots({'f_0012.png': shot, 'f_0040.png': pngHeader(720, 1280)});
        const md = '# 讲座\n\n要点一\n\n![白板上的公式](/jobs/t1/artifacts/frame?file=f_0012.png)\n\n'
            + '要点二 ![竖屏](/jobs/t1/artifacts/frame?file=f_0040.png)\n';
        const out = await dlSummaryMd(md, '讲座.mp4');
        expect(out).toMatchObject({format: 'zip', images: 2, missing: 0});

        const file = await lastExport();
        expect(file.name).toBe('讲座_summary.zip');
        const {default: JSZip} = await import('jszip');
        const zip = await JSZip.loadAsync(await blobBytes(saved.blob));
        expect(Object.keys(zip.files).sort()).toEqual(['images/', 'images/f_0012.png', 'images/f_0040.png', '讲座.md']);

        const note = await zip.file('讲座.md').async('string');
        expect(note).toContain('![白板上的公式](images/f_0012.png)');
        expect(note).toContain('要点二 ![竖屏](images/f_0040.png)');
        expect(note).not.toContain('/jobs/');
        // Everything other than the links is the note as written.
        expect(note.replace(/images\/f_00(12|40)\.png/g, 'X')).toBe(
            md.replace(/\/jobs\/t1\/artifacts\/frame\?file=f_00(12|40)\.png/g, 'X'),
        );
        expect(new Uint8Array(await zip.file('images/f_0012.png').async('uint8array'))).toEqual(shot);
    });

    it('keeps a full address for a screenshot it could not fetch and says how many', async () => {
        serveScreenshots({'ok.png': pngHeader(10, 10)});
        const md = '![有](/jobs/t1/artifacts/frame?file=ok.png)\n![没有](/jobs/t1/artifacts/frame?file=gone.png)\n';
        const out = await dlSummaryMd(md, '讲座');
        expect(out).toMatchObject({format: 'zip', images: 1, missing: 1});

        const {default: JSZip} = await import('jszip');
        const zip = await JSZip.loadAsync(await blobBytes(saved.blob));
        const note = await zip.file('讲座.md').async('string');
        expect(note).toContain('![有](images/ok.png)');
        // Not the bare app path, which resolves nowhere outside the app.
        expect(note).toMatch(/!\[没有\]\(https?:\/\/[^)]+\/jobs\/t1\/artifacts\/frame\?file=gone\.png\)/);
    });

    it('stays a single .md file when the only images are on the web', async () => {
        const asked = serveScreenshots({});
        const md = '# 笔记\n\n![图](https://example.com/a.png)\n';
        const out = await dlSummaryMd(md, '讲座');
        expect(out.format).toBe('md');
        const file = await lastExport();
        expect(file.name).toBe('讲座_summary.md');
        expect(file.text).toBe(md);
        expect(asked).toEqual([]);
    });
});

describe('screenshots in a Word export', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    // The drawn size of every picture in the document, in pixels (EMU / 9525).
    const drawnSizes = async (md) => {
        const docx = await import('docx');
        const doc = await buildSummaryDocxDocument(md);
        const buffer = await docx.Packer.toBuffer(doc);
        const {default: JSZip} = await import('jszip');
        const xml = await (await JSZip.loadAsync(buffer)).file('word/document.xml').async('string');
        return [...xml.matchAll(/<wp:extent cx="(\d+)" cy="(\d+)"/g)]
            .map(([, cx, cy]) => ({width: Math.round(cx / 9525), height: Math.round(cy / 9525)}));
    };

    it('keeps each screenshot at its own proportions instead of squashing it into 16:9', async () => {
        serveScreenshots({
            'wide.png': pngHeader(1920, 1080),
            'tall.png': pngHeader(720, 1280),
            'square.png': pngHeader(800, 800),
        });
        const sizes = await drawnSizes([
            '![横屏](/jobs/t1/artifacts/frame?file=wide.png)',
            '![竖屏](/jobs/t1/artifacts/frame?file=tall.png)',
            '![方形](/jobs/t1/artifacts/frame?file=square.png)',
        ].join('\n\n'));
        expect(sizes).toHaveLength(3);
        const [wide, tall, square] = sizes;
        expect(wide).toEqual({width: 480, height: 270});
        expect(square).toEqual({width: 480, height: 480});
        // A portrait frame keeps its shape and is held to a page-friendly height.
        expect(tall.height / tall.width).toBeCloseTo(1280 / 720, 2);
        expect(tall.height).toBeLessThanOrEqual(640);
    });

    it('reads the size from JPEG and PNG headers', () => {
        expect(download.imagePixelSize(jpegHeader(1080, 1920))).toEqual({width: 1080, height: 1920});
        expect(download.imagePixelSize(pngHeader(1280, 720))).toEqual({width: 1280, height: 720});
        expect(download.imagePixelSize(new Uint8Array([1, 2, 3]))).toBeNull();
    });

    it('falls back to the 16:9 box when the size cannot be read', () => {
        expect(download.docxImageSize(null)).toEqual({width: 480, height: 270});
    });
});
