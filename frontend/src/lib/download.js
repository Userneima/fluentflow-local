import {isPipeTableRow, looksLikeLoosePipeTable, looksLikeMdTable, simpleMd, splitMdTableRow} from './markdown.js';
import {API_BASE, apiFetch} from '../app/apiConfig.js';

export const _dl = (blob, name) => { const u=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=u; a.download=name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(u); };
export const _baseName = (fn) => (fn||'FluentFlow').replace(/\.[^/.]+$/,'');

const DOCX_FONT = 'PingFang SC';
const DOCX_TEXT = '1A1A1A';
const DOCX_MUTED = '667085';
const DOCX_BORDER = {style: 'single', size: 6, color: 'D9DDE3'};
const DOCX_CELL_MARGIN = {top: 120, bottom: 120, left: 140, right: 140};

const sanitizeDocxText = (value) => String(value || '')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1 ($2)')
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '$1 ($2)')
    .trim();

const docxRuns = (TextRun, text, options={}) => {
    const normalized = sanitizeDocxText(text);
    if(!normalized) return [new TextRun({text: '', font: DOCX_FONT})];
    const runs = [];
    const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let cursor = 0;
    for(const match of normalized.matchAll(pattern)){
        if(match.index > cursor){
            runs.push(new TextRun({
                text: normalized.slice(cursor, match.index),
                font: DOCX_FONT,
                size: options.size || 22,
                color: options.color || DOCX_TEXT,
                bold: options.bold || false,
            }));
        }
        const token = match[0];
        const isCode = token.startsWith('`');
        runs.push(new TextRun({
            text: isCode ? token.slice(1, -1) : token.slice(2, -2),
            font: DOCX_FONT,
            size: options.size || 22,
            color: isCode ? '344054' : (options.color || DOCX_TEXT),
            bold: isCode ? false : true,
        }));
        cursor = match.index + token.length;
    }
    if(cursor < normalized.length){
        runs.push(new TextRun({
            text: normalized.slice(cursor),
            font: DOCX_FONT,
            size: options.size || 22,
            color: options.color || DOCX_TEXT,
            bold: options.bold || false,
        }));
    }
    return runs.length ? runs : [new TextRun({text: normalized, font: DOCX_FONT})];
};

const paragraph = (docx, text, options={}) => new docx.Paragraph({
    children: docxRuns(docx.TextRun, text, options),
    heading: options.heading,
    bullet: options.bullet,
    numbering: options.numbering,
    spacing: options.spacing || {after: 120, line: 300},
    indent: options.indent,
    border: options.border,
    shading: options.shading,
});

const emptyParagraph = (docx) => new docx.Paragraph({children: [new docx.TextRun({text: '', font: DOCX_FONT})], spacing: {after: 80}});

const docxTable = (docx, headerCells, bodyRows) => {
    const rows = [];
    const columnCount = Math.max(headerCells?.length || 0, ...bodyRows.map(row => row.length), 0);
    const pad = (cells) => Array.from({length: columnCount}, (_, index) => cells[index] || '');
    const makeCell = (cell, isHeader=false) => new docx.TableCell({
        children: [paragraph(docx, cell, {bold: isHeader, size: 20, spacing: {after: 0, line: 276}})],
        margins: DOCX_CELL_MARGIN,
        shading: isHeader ? {fill: 'F2F4F7'} : undefined,
        borders: {top: DOCX_BORDER, bottom: DOCX_BORDER, left: DOCX_BORDER, right: DOCX_BORDER},
    });
    if(headerCells?.length){
        rows.push(new docx.TableRow({children: pad(headerCells).map(cell => makeCell(cell, true)), cantSplit: true}));
    }
    bodyRows.forEach((row) => {
        rows.push(new docx.TableRow({children: pad(row).map(cell => makeCell(cell, false)), cantSplit: true}));
    });
    return new docx.Table({
        rows,
        width: {size: 100, type: docx.WidthType.PERCENTAGE},
        layout: docx.TableLayoutType.FIXED,
        borders: {
            top: DOCX_BORDER,
            bottom: DOCX_BORDER,
            left: DOCX_BORDER,
            right: DOCX_BORDER,
            insideHorizontal: DOCX_BORDER,
            insideVertical: DOCX_BORDER,
        },
    });
};

const imageTypeFromSource = (src, contentType='') => {
    const type = String(contentType || '').toLowerCase();
    if(type.includes('png')) return 'png';
    if(type.includes('gif')) return 'gif';
    if(type.includes('bmp')) return 'bmp';
    const path = String(src || '').split('?')[0].toLowerCase();
    if(path.endsWith('.png')) return 'png';
    if(path.endsWith('.gif')) return 'gif';
    if(path.endsWith('.bmp')) return 'bmp';
    return 'jpg';
};

// Artifact URLs the backend emits relative to the API root ('jobs/...').
const API_RELATIVE_PATH_PREFIX = 'jobs/';
const isApiRelativePath = (raw) => raw.startsWith(API_RELATIVE_PATH_PREFIX);

const docxImageTarget = (src) => {
    let raw = String(src || '').trim();
    if(isApiRelativePath(raw)) raw = `/${raw}`;
    if(/^\/[^/]/.test(raw)){
        if(API_BASE) return `${API_BASE}${raw}`;
        if(typeof window !== 'undefined') return new URL(raw, window.location.origin).toString();
    }
    return raw;
};

const rewriteExportImageSources = (md) => String(md || '').replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, src) => {
    const target = docxImageTarget(src);
    return `![${alt}](${target})`;
});

const fetchDocxImage = async (src) => {
    const raw = String(src || '').trim();
    if(!raw) return null;
    if(typeof fetch !== 'function') return null;
    const target = docxImageTarget(raw);
    const useApiFetch = /^\/[^/]/.test(raw)
        || isApiRelativePath(raw)
        || (!!API_BASE && String(target).startsWith(API_BASE));
    try {
        const response = useApiFetch
            ? await apiFetch(target)
            : await fetch(target, {credentials: 'include'});
        if(!response.ok) return null;
        const contentType = response.headers.get('content-type') || '';
        if(contentType && !contentType.toLowerCase().startsWith('image/')) return null;
        const buffer = await response.arrayBuffer();
        if(!buffer.byteLength) return null;
        return {
            data: buffer,
            type: imageTypeFromSource(raw, contentType),
        };
    } catch {
        return null;
    }
};

const docxImageBlocks = async (docx, alt, src) => {
    const image = await fetchDocxImage(src);
    const caption = sanitizeDocxText(alt || 'Screenshot');
    if(!image){
        return [paragraph(docx, `${caption}${src ? `: ${src}` : ''}`, {
            size: 19,
            color: DOCX_MUTED,
            spacing: {after: 100, line: 260},
        })];
    }
    return [
        new docx.Paragraph({
            children: [new docx.ImageRun({
                type: image.type,
                data: image.data,
                transformation: {width: 480, height: 270},
                altText: {name: caption || 'FluentFlow screenshot', description: caption || 'Video screenshot evidence'},
            })],
            spacing: {before: 120, after: caption ? 60 : 140},
        }),
        ...(caption ? [paragraph(docx, caption, {
            size: 18,
            color: DOCX_MUTED,
            spacing: {after: 140, line: 240},
        })] : []),
    ];
};

export const buildSummaryDocxDocument = async (md) => {
    const docx = await import('docx');
    const lines = String(md || '').replace(/\r\n/g, '\n').split('\n');
    const children = [];
    let i = 0;
    while(i < lines.length){
        const raw = lines[i] || '';
        const trimmed = raw.trim();
        if(!trimmed){
            i += 1;
            continue;
        }

        if(trimmed.startsWith('```')){
            const codeLines = [];
            i += 1;
            while(i < lines.length && !(lines[i] || '').trim().startsWith('```')){
                codeLines.push(lines[i] || '');
                i += 1;
            }
            if(i < lines.length) i += 1;
            children.push(paragraph(docx, codeLines.join('\n'), {
                size: 19,
                color: '344054',
                spacing: {before: 100, after: 160, line: 260},
                shading: {fill: 'F4F6F8'},
            }));
            continue;
        }

        if(looksLikeMdTable(lines, i)){
            const headerCells = splitMdTableRow(lines[i]);
            const bodyRows = [];
            i += 2;
            while(i < lines.length){
                const row = (lines[i] || '').trim();
                if(!row || !row.includes('|')) break;
                bodyRows.push(splitMdTableRow(row));
                i += 1;
            }
            children.push(docxTable(docx, headerCells, bodyRows), emptyParagraph(docx));
            continue;
        }

        if(looksLikeLoosePipeTable(lines, i)){
            const bodyRows = [];
            while(i < lines.length){
                const row = (lines[i] || '').trim();
                if(!row || !isPipeTableRow(row)) break;
                bodyRows.push(splitMdTableRow(row));
                i += 1;
            }
            children.push(docxTable(docx, null, bodyRows), emptyParagraph(docx));
            continue;
        }

        const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
        if(heading){
            const level = Math.min(heading[1].length, 6);
            const headingMap = [
                docx.HeadingLevel.HEADING_1,
                docx.HeadingLevel.HEADING_2,
                docx.HeadingLevel.HEADING_3,
                docx.HeadingLevel.HEADING_4,
                docx.HeadingLevel.HEADING_5,
                docx.HeadingLevel.HEADING_6,
            ];
            children.push(paragraph(docx, heading[2], {
                heading: headingMap[level - 1],
                size: level === 1 ? 32 : level === 2 ? 28 : 24,
                bold: true,
                spacing: {before: level === 1 ? 120 : 180, after: 100, line: 300},
            }));
            i += 1;
            continue;
        }

        const unordered = trimmed.match(/^[-*]\s+(.+)$/);
        if(unordered){
            children.push(paragraph(docx, unordered[1], {
                bullet: {level: 0},
                indent: {left: 520, hanging: 240},
                spacing: {after: 80, line: 300},
            }));
            i += 1;
            continue;
        }

        const ordered = trimmed.match(/^\d+[.）]\s+(.+)$/);
        if(ordered){
            children.push(paragraph(docx, ordered[1], {
                numbering: {reference: 'ff-numbering', level: 0},
                indent: {left: 560, hanging: 280},
                spacing: {after: 80, line: 300},
            }));
            i += 1;
            continue;
        }

        if(trimmed.startsWith('> ')){
            children.push(paragraph(docx, trimmed.slice(2), {
                color: '344054',
                spacing: {before: 80, after: 140, line: 300},
                border: {left: {style: 'single', size: 12, color: 'B7C4D4'}},
                shading: {fill: 'F8FAFC'},
            }));
            i += 1;
            continue;
        }

        const imageMatch = trimmed.match(/^!\[(.*?)\]\((.*?)\)$/);
        if(imageMatch){
            const alt = imageMatch[1] || 'Image';
            const src = imageMatch[2] || '';
            children.push(...await docxImageBlocks(docx, alt, src));
            i += 1;
            continue;
        }

        if(trimmed === '---' || trimmed === '***' || trimmed === '___'){
            children.push(new docx.Paragraph({
                children: [new docx.TextRun({text: '', font: DOCX_FONT})],
                border: {bottom: {style: 'single', size: 6, color: 'D0D5DD'}},
                spacing: {before: 120, after: 160},
            }));
            i += 1;
            continue;
        }

        children.push(paragraph(docx, trimmed));
        i += 1;
    }

    return new docx.Document({
        creator: 'FluentFlow',
        title: 'FluentFlow study note',
        styles: {
            default: {
                document: {
                    run: {font: DOCX_FONT, size: 22, color: DOCX_TEXT},
                    paragraph: {spacing: {after: 120, line: 300}},
                },
                heading1: {run: {font: DOCX_FONT, size: 32, bold: true, color: '111111'}, paragraph: {spacing: {before: 120, after: 120}}},
                heading2: {run: {font: DOCX_FONT, size: 28, bold: true, color: '111111'}, paragraph: {spacing: {before: 180, after: 100}}},
                heading3: {run: {font: DOCX_FONT, size: 24, bold: true, color: '111111'}, paragraph: {spacing: {before: 160, after: 80}}},
            },
        },
        numbering: {
            config: [{
                reference: 'ff-numbering',
                levels: [{
                    level: 0,
                    format: 'decimal',
                    text: '%1.',
                    alignment: docx.AlignmentType.LEFT,
                    style: {paragraph: {indent: {left: 560, hanging: 280}}},
                }],
            }],
        },
        sections: [{
            properties: {
                page: {
                    margin: {top: 960, right: 900, bottom: 960, left: 900},
                },
            },
            children: children.length ? children : [paragraph(docx, '')],
        }],
    });
};

export const _fmtSrtTime = (sec) => {
    const h=Math.floor(sec/3600), m=Math.floor((sec%3600)/60), s=Math.floor(sec%60), ms=Math.round((sec%1)*1000);
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')},${String(ms).padStart(3,'0')}`;
};
export const _fmtVttTime = (sec) => {
    const h=Math.floor(sec/3600), m=Math.floor((sec%3600)/60), s=Math.floor(sec%60), ms=Math.round((sec%1)*1000);
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(ms).padStart(3,'0')}`;
};

export const dlTranscriptTxt = (text, filename) => {
    _dl(new Blob([text],{type:'text/plain;charset=utf-8'}), _baseName(filename)+'.txt');
};
export const dlTranscriptSrt = (segments, filename) => {
    const lines = segments.map((s,i) => `${i+1}\n${_fmtSrtTime(s.start)} --> ${_fmtSrtTime(s.end)}\n${s.text.trim()}\n`);
    _dl(new Blob([lines.join('\n')],{type:'text/plain;charset=utf-8'}), _baseName(filename)+'.srt');
};
export const dlTranscriptVtt = (segments, filename) => {
    const lines = ['WEBVTT\n', ...segments.map(s => `${_fmtVttTime(s.start)} --> ${_fmtVttTime(s.end)}\n${s.text.trim()}\n`)];
    _dl(new Blob([lines.join('\n')],{type:'text/vtt;charset=utf-8'}), _baseName(filename)+'.vtt');
};
const bilingualSegments = (segments, translatedSegments) => (
    (segments || []).map((segment, index) => {
        const text = String(segment?.text || '').trim();
        const zh = String(
            segment?.text_zh
            || translatedSegments?.[index]?.text
            || translatedSegments?.[index]?.text_zh
            || ''
        ).trim();
        return {...segment, text: zh ? `${text}\n${zh}` : text};
    }).filter((segment) => String(segment.text || '').trim())
);
export const dlBilingualTranscriptSrt = (segments, translatedSegments, filename) => {
    const merged = bilingualSegments(segments, translatedSegments);
    const lines = merged.map((s,i) => `${i+1}\n${_fmtSrtTime(s.start)} --> ${_fmtSrtTime(s.end)}\n${s.text.trim()}\n`);
    _dl(new Blob([lines.join('\n')],{type:'text/plain;charset=utf-8'}), _baseName(filename)+'_bilingual_zh.srt');
};
export const dlBilingualTranscriptVtt = (segments, translatedSegments, filename) => {
    const merged = bilingualSegments(segments, translatedSegments);
    const lines = ['WEBVTT\n', ...merged.map(s => `${_fmtVttTime(s.start)} --> ${_fmtVttTime(s.end)}\n${s.text.trim()}\n`)];
    _dl(new Blob([lines.join('\n')],{type:'text/vtt;charset=utf-8'}), _baseName(filename)+'_bilingual_zh.vtt');
};

export const dlSummaryTxt = (md, filename) => {
    _dl(new Blob([md],{type:'text/plain;charset=utf-8'}), _baseName(filename)+'_summary.txt');
};

export const dlSummaryMd = (md, filename) => {
    _dl(new Blob([md],{type:'text/markdown;charset=utf-8'}), _baseName(filename)+'_summary.md');
};

export const dlSummaryWord = async (md, filename) => {
    const {Packer} = await import('docx');
    const doc = await buildSummaryDocxDocument(md);
    const blob = await Packer.toBlob(doc);
    _dl(blob, _baseName(filename)+'_summary.docx');
};

const buildPrintableSummaryHtml = (md, title='FluentFlow Summary') => `<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>${String(title || 'FluentFlow Summary').replace(/</g, '&lt;')}</title>
    <style>
        @page {
            size: A4;
            margin: 18mm 16mm;
        }
        html,
        body {
            margin: 0;
            padding: 0;
            background: #ffffff;
            color: #171717;
            font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", Arial, sans-serif;
            font-size: 11pt;
            line-height: 1.72;
        }
        body {
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }
        .ff-print-summary-export {
            box-sizing: border-box;
            width: 100%;
            max-width: 176mm;
            margin: 0 auto;
            background: #ffffff;
            color: #171717;
            letter-spacing: 0;
        }
        .ff-print-summary-export * {
            box-sizing: border-box;
            color: inherit !important;
            opacity: 1 !important;
            text-shadow: none !important;
        }
        .ff-print-summary-export h2,
        .ff-print-summary-export h3,
        .ff-print-summary-export h4,
        .ff-print-summary-export h5 {
            break-after: avoid;
            page-break-after: avoid;
            margin: 18pt 0 8pt;
            color: #111111 !important;
            font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", Arial, sans-serif;
            font-weight: 700;
            line-height: 1.35;
        }
        .ff-print-summary-export h2 { font-size: 19pt; margin-top: 0; }
        .ff-print-summary-export h3 { font-size: 16pt; }
        .ff-print-summary-export h4 { font-size: 13.5pt; }
        .ff-print-summary-export h5 { font-size: 11.5pt; }
        .ff-print-summary-export p {
            margin: 0 0 7pt;
            color: #252525 !important;
        }
        .ff-print-summary-export ul,
        .ff-print-summary-export ol {
            margin: 6pt 0 10pt 18pt;
            padding: 0;
        }
        .ff-print-summary-export li {
            display: list-item !important;
            margin: 0 0 5pt;
            padding-left: 1pt;
            color: #252525 !important;
            break-inside: avoid;
            page-break-inside: avoid;
        }
        .ff-print-summary-export li > span:first-child {
            display: none !important;
        }
        .ff-print-summary-export li > span {
            display: inline !important;
        }
        .ff-print-summary-export strong {
            font-weight: 700;
            color: #111111 !important;
        }
        .ff-print-summary-export code {
            display: inline;
            padding: 1pt 3pt;
            border-radius: 3pt;
            background: #f1f3f5 !important;
            color: #111111 !important;
            font-family: "PingFang SC", "SFMono-Regular", Consolas, monospace;
            font-size: 0.92em;
        }
        .ff-print-summary-export pre,
        .ff-print-summary-export blockquote,
        .ff-print-summary-export figure,
        .ff-print-summary-export table {
            break-inside: avoid;
            page-break-inside: avoid;
        }
        .ff-print-summary-export pre {
            overflow-wrap: anywhere;
            white-space: pre-wrap;
            margin: 9pt 0 12pt;
            padding: 9pt 10pt;
            border-radius: 6pt;
            background: #f6f8fa !important;
            color: #171717 !important;
            font-size: 9.5pt;
            line-height: 1.55;
        }
        .ff-print-summary-export blockquote {
            margin: 9pt 0 12pt;
            padding: 8pt 10pt;
            border-left: 3pt solid #7c3aed;
            background: #f7f3ff !important;
            color: #2d2540 !important;
        }
        .ff-print-summary-export hr {
            margin: 14pt 0;
            border: 0;
            border-top: 1pt solid #dedede;
        }
        .ff-print-summary-export img {
            display: block;
            max-width: 100%;
            height: auto;
            margin: 6pt 0;
            border-radius: 6pt;
        }
        .ff-print-summary-export figcaption {
            margin-top: 3pt;
            color: #666666 !important;
            font-size: 9pt;
            line-height: 1.45;
        }
        .ff-print-summary-export table {
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            margin: 10pt 0 12pt;
            font-size: 9.5pt;
        }
        .ff-print-summary-export th,
        .ff-print-summary-export td {
            border: 1pt solid #dedede;
            padding: 5pt 6pt;
            vertical-align: top;
            color: #222222 !important;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .ff-print-summary-export th {
            background: #f5f5f5 !important;
            color: #111111 !important;
            font-weight: 700;
        }
        .ff-print-summary-export br + br {
            display: none;
        }
    </style>
</head>
<body>
    <main class="ff-print-summary-export">
        <div class="ff-print-summary-body">${simpleMd(rewriteExportImageSources(md), {renderImages: true, renderManualListMarkers: false})}</div>
    </main>
</body>
</html>`;

export const createPdfPrintFrame = (html) => {
    const frame = document.createElement('iframe');
    frame.setAttribute('title', 'FluentFlow PDF Export');
    frame.style.position = 'fixed';
    frame.style.right = '0';
    frame.style.bottom = '0';
    frame.style.width = '0';
    frame.style.height = '0';
    frame.style.border = '0';
    frame.style.opacity = '0';
    frame.style.pointerEvents = 'none';
    frame.srcdoc = html;
    document.body.appendChild(frame);
    return frame;
};

const waitForPrintAssets = async (printWindow) => {
    const doc = printWindow?.document;
    if(!doc) return;
    await doc.fonts?.ready?.catch?.(() => {});
    const images = Array.from(doc.images || []);
    await Promise.all(images.map((image) => {
        if(image.complete) return Promise.resolve();
        return new Promise((resolve) => {
            image.addEventListener('load', resolve, {once: true});
            image.addEventListener('error', resolve, {once: true});
        });
    }));
};

export const dlSummaryPdf = async (summaryMdOrRef, filename) => {
    const markdown = typeof summaryMdOrRef === 'string'
        ? summaryMdOrRef
        : summaryMdOrRef?.current?.innerText || '';
    if(!markdown) return;
    const frame = createPdfPrintFrame(buildPrintableSummaryHtml(markdown, _baseName(filename)+'_summary'));
    await new Promise((resolve) => {
        const done = () => resolve();
        frame.addEventListener('load', done, {once: true});
        setTimeout(done, 250);
    });
    const printWindow = frame.contentWindow;
    if(!printWindow) {
        frame.remove();
        throw new Error('Unable to open print frame');
    }
    await waitForPrintAssets(printWindow);
    const cleanup = () => setTimeout(() => frame.remove(), 1000);
    printWindow.addEventListener?.('afterprint', cleanup, {once: true});
    printWindow.focus();
    printWindow.print();
    setTimeout(() => {
        if(frame.isConnected) cleanup();
    }, 60000);
};

export const dlSummaryImage = async () => {
    throw new Error('PNG export disabled');
};
