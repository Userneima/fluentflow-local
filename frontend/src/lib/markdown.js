export const MD_TABLE_ALIGN_RE = /^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$/;

export const splitMdTableRow = (line) => {
    let text = (line || '').trim();
    if(text.startsWith('|')) text = text.slice(1);
    if(text.endsWith('|')) text = text.slice(0, -1);
    return text.split('|').map(cell => cell.trim());
};

export const isPipeTableRow = (line) => {
    const text = (line || '').trim();
    return text.startsWith('|') && text.endsWith('|') && splitMdTableRow(text).length >= 2;
};

export const looksLikeMdTable = (lines, index) => {
    if(index + 1 >= lines.length) return false;
    const head = (lines[index] || '').trim();
    const align = (lines[index + 1] || '').trim();
    return head.includes('|') && align.includes('|') && MD_TABLE_ALIGN_RE.test(align);
};

export const looksLikeLoosePipeTable = (lines, index) => {
    let rowCount = 0;
    let cols = -1;
    for(let i = index; i < lines.length; i += 1){
        const text = (lines[i] || '').trim();
        if(!text) break;
        if(!isPipeTableRow(text)) break;
        const cells = splitMdTableRow(text);
        if(cols === -1) cols = cells.length;
        if(cells.length !== cols) break;
        rowCount += 1;
    }
    return rowCount >= 2;
};

export const renderTableHtml = (headerCells, bodyRows, renderInline) => {
    const columnCount = Math.max(
        headerCells?.length || 0,
        ...bodyRows.map(row => row.length),
        0
    );
    if(!columnCount) return '';
    const pad = (cells) => Array.from({length: columnCount}, (_, idx) => cells[idx] || '');
    const thead = headerCells && headerCells.length ? `<thead class="bg-slate-50">
        <tr>${pad(headerCells).map(cell => `<th class="px-3 py-2 text-left text-xs font-bold uppercase tracking-wide text-slate-500 border-b border-slate-200">${renderInline(cell)}</th>`).join('')}</tr>
    </thead>` : '';
    const tbody = `<tbody>
        ${bodyRows.map((row, rowIdx) => `<tr class="${rowIdx > 0 ? 'border-t border-slate-200' : ''}">
            ${pad(row).map((cell, cellIdx) => `<td class="px-3 py-2 align-top text-sm ${!headerCells && cellIdx === 0 ? 'font-semibold text-on-surface' : 'text-on-surface-variant'}">${renderInline(cell)}</td>`).join('')}
        </tr>`).join('')}
    </tbody>`;
    return `<div class="my-4 overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table class="min-w-full border-collapse">${thead}${tbody}</table>
    </div>`;
};

export const simpleMd = (md, options={}) => {
    if(!md) return '';
    const renderImages = options.renderImages !== false;
    const renderManualListMarkers = options.renderManualListMarkers !== false;
    const esc = (s) => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
    const renderInline = (s) => esc(s)
        .replace(/`([^`]+)`/g,'<code class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 text-[0.92em]">$1</code>')
        .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,'$1<em>$2</em>');

    const lines = md.replace(/\r\n/g, '\n').split('\n');
    let html = '';
    let inUl = false;
    let inOl = false;

    const closeLists = () => {
        if(inUl){ html += '</ul>'; inUl = false; }
        if(inOl){ html += '</ol>'; inOl = false; }
    };

    let i = 0;
    while(i < lines.length){
        const raw = lines[i] || '';
        const line = raw.trimEnd();
        const trimmed = line.trim();

        if(!trimmed){
            closeLists();
            html += '<br/>';
            i += 1;
            continue;
        }

        if(trimmed.startsWith('```')){
            closeLists();
            const codeLines = [];
            i += 1;
            while(i < lines.length && !lines[i].trim().startsWith('```')){
                codeLines.push(lines[i]);
                i += 1;
            }
            if(i < lines.length) i += 1;
            html += `<pre class="my-4 overflow-x-auto rounded-lg bg-slate-100 px-4 py-3 text-sm leading-relaxed text-on-surface"><code>${esc(codeLines.join('\n'))}</code></pre>`;
            continue;
        }

        if(looksLikeMdTable(lines, i)){
            closeLists();
            const headerCells = splitMdTableRow(lines[i]);
            const bodyRows = [];
            i += 2;
            while(i < lines.length){
                const row = (lines[i] || '').trim();
                if(!row || !row.includes('|')) break;
                bodyRows.push(splitMdTableRow(row));
                i += 1;
            }
            html += renderTableHtml(headerCells, bodyRows, renderInline);
            continue;
        }

        if(looksLikeLoosePipeTable(lines, i)){
            closeLists();
            const bodyRows = [];
            while(i < lines.length){
                const row = (lines[i] || '').trim();
                if(!row || !isPipeTableRow(row)) break;
                bodyRows.push(splitMdTableRow(row));
                i += 1;
            }
            html += renderTableHtml(null, bodyRows, renderInline);
            continue;
        }

        if(/^#{1,6}\s+/.test(trimmed)){
            closeLists();
            const level = Math.min((trimmed.match(/^#+/) || ['#'])[0].length, 6);
            const content = trimmed.slice(level + 1);
            const tag = level <= 1 ? 'h2' : level === 2 ? 'h3' : level === 3 ? 'h4' : 'h5';
            const klass = level <= 1
                ? 'text-xl font-headline font-bold mt-6 mb-2'
                : level === 2
                    ? 'text-lg font-headline font-bold mt-5 mb-2'
                    : level === 3
                        ? 'text-base font-headline font-bold mt-4 mb-1'
                        : 'text-sm font-headline font-bold mt-3 mb-1';
            html += `<${tag} class="${klass}">${renderInline(content)}</${tag}>`;
            i += 1;
            continue;
        }

        if(/^[-*] /.test(trimmed)){
            if(inOl){ html += '</ol>'; inOl = false; }
            if(!inUl){ html += renderManualListMarkers ? '<ul class="space-y-1 my-2">' : '<ul>'; inUl = true; }
            html += renderManualListMarkers
                ? `<li class="flex gap-2 text-sm text-on-surface"><span class="text-tertiary mt-0.5">•</span><span>${renderInline(trimmed.slice(2))}</span></li>`
                : `<li>${renderInline(trimmed.slice(2))}</li>`;
            i += 1;
            continue;
        }

        const ordered = trimmed.match(/^\d+[.）]\s+(.+)/);
        if(ordered){
            if(inUl){ html += '</ul>'; inUl = false; }
            if(!inOl){ html += '<ol class="list-decimal space-y-1 my-2 pl-5">'; inOl = true; }
            html += `<li class="text-sm text-on-surface">${renderInline(ordered[1])}</li>`;
            i += 1;
            continue;
        }

        if(trimmed.startsWith('> ')){
            closeLists();
            html += `<blockquote class="my-3 border-l-4 border-tertiary/40 bg-purple-50 px-4 py-2 text-sm leading-relaxed text-on-surface-variant">${renderInline(trimmed.slice(2))}</blockquote>`;
            i += 1;
            continue;
        }

        const imageMatch = trimmed.match(/^!\[(.*?)\]\((.*?)\)$/);
        if(imageMatch){
            closeLists();
            if(!renderImages){
                i += 1;
                continue;
            }
            const alt = imageMatch[1] || '';
            const src = imageMatch[2] || '';
            html += `<figure class="my-3"><img src="${esc(src)}" alt="${esc(alt)}" class="rounded-lg max-w-full" loading="lazy"/><figcaption class="text-xs text-on-surface-variant mt-1">${esc(alt)}</figcaption></figure>`;
            i += 1;
            continue;
        }

        if(trimmed === '---' || trimmed === '***' || trimmed === '___'){
            closeLists();
            html += '<hr class="my-4 border-slate-200"/>';
            i += 1;
            continue;
        }

        closeLists();
        html += `<p class="text-sm text-on-surface-variant leading-relaxed mb-1">${renderInline(trimmed)}</p>`;
        i += 1;
    }

    closeLists();
    return html;
};
