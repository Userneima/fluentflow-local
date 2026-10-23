// The note editor's Obsidian-style live preview over the note's markdown
// source, built on CodeMirror 6 (decision 5.34). The document is the markdown itself;
// everything here only decides how each line looks. Lines the cursor is on show
// their markdown symbols, every other line shows the formatted result, the way
// Obsidian's live preview does. Nothing is converted, so whatever is typed
// (callouts, heading links, crops) stays exactly as written.

import {EditorState, StateEffect, StateField} from '@codemirror/state';
import {Decoration, EditorView, WidgetType} from '@codemirror/view';

const CALLOUT_LABELS = {
    question: '问题', tip: '提示', note: '笔记', todo: '待办', info: '信息', warning: '注意',
    important: '重要', example: '例子', quote: '引用', summary: '摘要', abstract: '摘要',
};
const CALLOUT_ICONS = {question: '?', tip: '✦', note: '✎', todo: '☐', warning: '!', important: '!', info: 'i'};
const CALLOUT_TYPES = new Set(['question', 'tip', 'note', 'todo']);

const escapeHtml = (text) => String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

const indentWidth = (ws) => ws.replace(/\t/g, '    ').length;

const TABLE_SEPARATOR = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])(\s+)/;
const HEADING = /^(#{1,6})(\s+)/;
const RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const QUOTE = /^(\s*)>( ?)/;
const CALLOUT_HEAD = /^\[!(\w+)\][+-]?( ?)/;
const FENCE = /^\s*(`{3,}|~{3,})/;

// ---- Images: frame names, materialized crops and `#crop=x0,y0,x1,y1` ----

export const parseCrop = (src) => {
    const match = String(src || '').match(/#crop=([^#\s)]*)$/);
    if (!match) return {src, crop: null};
    const values = match[1].split(',').map(Number);
    const base = src.slice(0, match.index);
    if (values.length !== 4 || values.some((v) => !Number.isFinite(v))) return {src: base, crop: null};
    const [x0, y0, x1, y1] = values;
    if (!(x0 >= 0 && x0 < x1 && x1 <= 1 && y0 >= 0 && y0 < y1 && y1 <= 1)) return {src: base, crop: null};
    return {src: base, crop: {x0, y0, x1, y1}};
};

export const resolveImageSrc = (src, {apiBase = '', taskId = ''} = {}) => {
    const value = String(src || '').trim();
    if (/^(https?:|data:image\/|blob:)/i.test(value)) return value;
    if (value.startsWith('/')) return `${apiBase}${value}`;
    // A bare screenshot name, the way the md file in the output folder cites it.
    if (taskId && /^[\w.-]+\.(?:jpe?g|png|webp|gif)$/i.test(value)) {
        return `${apiBase}/jobs/${encodeURIComponent(taskId)}/artifacts/frame?file=${encodeURIComponent(value)}`;
    }
    return value;
};

class ImageWidget extends WidgetType {
    constructor(src, alt, crop) {
        super();
        this.src = src;
        this.alt = alt;
        this.crop = crop;
    }

    eq(other) {
        return other.src === this.src && other.alt === this.alt && JSON.stringify(other.crop) === JSON.stringify(this.crop);
    }

    toDOM() {
        const figure = document.createElement('span');
        figure.className = 'cm-ob-image';
        const img = document.createElement('img');
        img.alt = this.alt;
        img.loading = 'lazy';
        img.src = this.src;
        if (this.crop) {
            const {x0, y0, x1, y1} = this.crop;
            const frame = document.createElement('span');
            frame.className = 'cm-ob-crop';
            img.style.width = `${100 / (x1 - x0)}%`;
            img.style.left = `${-100 * x0 / (x1 - x0)}%`;
            img.style.top = `${-100 * y0 / (y1 - y0)}%`;
            img.addEventListener('load', () => {
                if (!img.naturalWidth) return;
                frame.style.aspectRatio = `${img.naturalWidth * (x1 - x0)} / ${img.naturalHeight * (y1 - y0)}`;
            });
            frame.appendChild(img);
            figure.appendChild(frame);
        } else {
            figure.appendChild(img);
        }
        if (this.alt) {
            const caption = document.createElement('span');
            caption.className = 'cm-ob-caption';
            caption.textContent = this.alt;
            figure.appendChild(caption);
        }
        return figure;
    }

    ignoreEvent() { return false; }
}

// ---- Heading links `[[#标题|第五章]]` ----

const normalizeHeading = (text) => String(text || '')
    .replace(/[*_`]/g, '')
    .replace(/\s+/g, '')
    .trim();

export const findHeadingLine = (doc, target) => {
    const wanted = String(target || '').replace(/^#+/, '').split('#').pop().trim();
    if (!wanted) return null;
    let loose = null;
    for (let n = 1; n <= doc.lines; n += 1) {
        const line = doc.line(n);
        const match = line.text.match(/^#{1,6}\s+(.*)$/);
        if (!match) continue;
        const heading = match[1].trim();
        if (heading === wanted) return line;
        if (!loose && normalizeHeading(heading) === normalizeHeading(wanted)) loose = line;
    }
    return loose;
};

const flashHeading = StateEffect.define();
const flashField = StateField.define({
    create: () => null,
    update(value, tr) {
        for (const effect of tr.effects) if (effect.is(flashHeading)) return effect.value;
        return value === null ? null : tr.changes.mapPos(value);
    },
});

export const jumpToHeading = (view, target) => {
    const line = findHeadingLine(view.state.doc, target);
    if (!line) return false;
    view.dispatch({effects: [
        EditorView.scrollIntoView(line.from, {y: 'start', yMargin: 24}),
        flashHeading.of(line.from),
    ]});
    window.setTimeout(() => view.dispatch({effects: flashHeading.of(null)}), 1600);
    return true;
};

class LinkWidget extends WidgetType {
    constructor(label, target, kind) {
        super();
        this.label = label;
        this.target = target;
        this.kind = kind;
    }

    eq(other) { return other.label === this.label && other.target === this.target && other.kind === this.kind; }

    toDOM(view) {
        const link = document.createElement('a');
        link.className = this.kind === 'wiki' ? 'cm-ob-wikilink' : 'cm-ob-link';
        link.textContent = this.label;
        if (this.kind === 'wiki') {
            link.title = `跳到「${this.target.replace(/^#/, '')}」`;
            link.addEventListener('mousedown', (event) => {
                event.preventDefault();
                event.stopPropagation();
                jumpToHeading(view, this.target);
            });
        } else {
            link.href = this.target;
            link.target = '_blank';
            link.rel = 'noreferrer';
            link.addEventListener('mousedown', (event) => event.stopPropagation());
        }
        return link;
    }

    ignoreEvent() { return true; }
}

class BulletWidget extends WidgetType {
    constructor(text, ordered) {
        super();
        this.text = text;
        this.ordered = ordered;
    }

    eq(other) { return other.text === this.text && other.ordered === this.ordered; }

    toDOM() {
        const span = document.createElement('span');
        span.className = `cm-ob-marker ${this.ordered ? 'cm-ob-marker-ordered' : 'cm-ob-marker-bullet'}`;
        span.textContent = this.ordered ? this.text : '•';
        return span;
    }
}

class RuleWidget extends WidgetType {
    eq() { return true; }

    toDOM() {
        const span = document.createElement('span');
        span.className = 'cm-ob-rule';
        return span;
    }
}

class CalloutLabelWidget extends WidgetType {
    constructor(type, showName) {
        super();
        this.type = type;
        this.showName = showName;
    }

    eq(other) { return other.type === this.type && other.showName === this.showName; }

    toDOM() {
        const span = document.createElement('span');
        span.className = 'cm-ob-callout-label';
        const icon = document.createElement('span');
        icon.className = 'cm-ob-callout-icon';
        icon.textContent = CALLOUT_ICONS[this.type] || '•';
        span.appendChild(icon);
        if (this.showName) {
            const name = document.createElement('span');
            name.textContent = CALLOUT_LABELS[this.type] || this.type;
            span.appendChild(name);
        }
        return span;
    }
}

// Inline markdown inside a table cell, rendered to HTML (cells are shown as a
// table widget, not as editable lines, until the cursor goes into the table).
export const renderInlineHtml = (text) => {
    let html = '';
    let rest = String(text || '');
    const pattern = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]|\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*([^*\s][^*]*?)\*/;
    while (rest) {
        const match = rest.match(pattern);
        if (!match) { html += escapeHtml(rest); break; }
        html += escapeHtml(rest.slice(0, match.index));
        if (match[1] !== undefined) {
            const label = match[2] || match[1].replace(/^#/, '');
            html += `<a class="cm-ob-wikilink" data-target="${escapeHtml(match[1])}">${escapeHtml(label)}</a>`;
        } else if (match[3] !== undefined) {
            html += `<strong>${renderInlineHtml(match[3])}</strong>`;
        } else if (match[4] !== undefined) {
            html += `<code>${escapeHtml(match[4])}</code>`;
        } else if (match[5] !== undefined) {
            html += `<a class="cm-ob-link" href="${escapeHtml(match[6])}" target="_blank" rel="noreferrer">${escapeHtml(match[5])}</a>`;
        } else {
            html += `<em>${renderInlineHtml(match[7])}</em>`;
        }
        rest = rest.slice(match.index + match[0].length);
    }
    return html;
};

const splitRow = (row) => {
    let text = row.trim();
    if (text.startsWith('|')) text = text.slice(1);
    if (text.endsWith('|') && !text.endsWith('\\|')) text = text.slice(0, -1);
    const cells = [];
    let current = '';
    for (let i = 0; i < text.length; i += 1) {
        if (text[i] === '\\' && text[i + 1] === '|') { current += '|'; i += 1; continue; }
        if (text[i] === '|') { cells.push(current.trim()); current = ''; continue; }
        current += text[i];
    }
    cells.push(current.trim());
    return cells;
};

// Where one cell's text sits in a table row's source line, so an edit in the
// rendered table replaces that cell's text and nothing else: the rest of the
// line (pipes, padding, other cells, a quote prefix) keeps its exact bytes.
// Returns {from, to} offsets into the line, or null when the row has no such
// cell.
export const tableCellRange = (lineText, cellIndex) => {
    const prefix = (lineText.match(/^(?:\s*>\s?)*/) || [''])[0];
    let i = prefix.length;
    while (i < lineText.length && /\s/.test(lineText[i])) i += 1;
    if (lineText[i] === '|') i += 1;
    let cell = 0;
    let start = i;
    for (; i <= lineText.length; i += 1) {
        const ch = lineText[i];
        if (ch === '\\' && lineText[i + 1] === '|') { i += 1; continue; }
        if (ch === '|' || i === lineText.length) {
            if (cell === cellIndex) {
                let from = start;
                let to = i;
                while (from < to && /\s/.test(lineText[from])) from += 1;
                while (to > from && /\s/.test(lineText[to - 1])) to -= 1;
                // The trailing pipe ends the last cell; nothing after it is a cell.
                if (i === lineText.length && start >= lineText.length) return null;
                return {from, to, empty: from === to, start, end: i};
            }
            cell += 1;
            start = i + 1;
        }
    }
    return null;
};

// A cell's text as markdown source: a pipe inside it is escaped.
export const cellSource = (text) => String(text || '').replace(/\r?\n/g, ' ').replace(/(?<!\\)\|/g, '\\|').trim();

// Which table (by the position of its first line) has a cell being edited in
// place. While it is set, the table stays rendered even though the selection
// sits inside it, so the cell being typed in is not swapped for source.
const setEditingTable = StateEffect.define();
const editingTableField = StateField.define({
    create: () => null,
    update(value, tr) {
        for (const effect of tr.effects) if (effect.is(setEditingTable)) return effect.value;
        if (value !== null && tr.docChanged) return tr.changes.mapPos(value);
        return value;
    },
});

class TableWidget extends WidgetType {
    constructor(rows, from, quote) {
        super();
        this.rows = rows;
        this.from = from;
        this.quote = quote;
    }

    eq(other) {
        return other.from === this.from && JSON.stringify(other.rows) === JSON.stringify(this.rows)
            && JSON.stringify(other.quote) === JSON.stringify(this.quote);
    }

    toDOM(view) {
        const wrap = document.createElement('div');
        wrap.className = 'cm-ob-table-wrap';
        if (this.quote) {
            wrap.classList.add(...quoteClasses(this.quote, {last: this.quote.endsHere}));
            wrap.style.marginLeft = `${this.quote.level * 1.6}em`;
        }
        const scroller = document.createElement('div');
        scroller.className = 'cm-ob-table-scroll';
        const table = document.createElement('table');
        const [header, ...body] = this.rows;
        const head = document.createElement('thead');
        const tbody = document.createElement('tbody');
        // Source line of each rendered row: the header, then the body rows
        // after the separator line.
        const rowLine = (r) => (r === 0 ? 0 : r + 1);
        const caretToEnd = (cell) => {
            const selection = cell.ownerDocument.getSelection?.();
            if (!selection) return;
            const range = cell.ownerDocument.createRange();
            range.selectNodeContents(cell);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
        };
        const makeCell = (tag, text, r, c) => {
            let justOpened = false;
            const cell = document.createElement(tag);
            cell.innerHTML = renderInlineHtml(text);
            cell.contentEditable = 'true';
            cell.spellcheck = false;
            cell.dataset.row = String(r);
            cell.dataset.col = String(c);
            cell.addEventListener('focus', () => {
                // Edited as its markdown source, shown formatted again on leaving.
                // Swapping the text loses the caret, so it goes to the end.
                cell.textContent = text;
                caretToEnd(cell);
                justOpened = true;
                view.dispatch({effects: setEditingTable.of(this.from)});
            });
            // The click that opened the cell would put the caret where it
            // landed in the formatted text, which is somewhere else in the source.
            cell.addEventListener('mouseup', () => {
                if (!justOpened) return;
                justOpened = false;
                caretToEnd(cell);
            });
            cell.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); cell.blur(); }
                if (event.key === 'Escape') { event.preventDefault(); cell.textContent = text; cell.blur(); }
            });
            cell.addEventListener('blur', () => {
                const next = cellSource(cell.textContent);
                cell.innerHTML = renderInlineHtml(text);
                const effects = setEditingTable.of(null);
                if (next === cellSource(text)) { view.dispatch({effects}); return; }
                const line = view.state.doc.line(view.state.doc.lineAt(this.from).number + rowLine(r));
                const range = tableCellRange(line.text, c);
                if (!range) { view.dispatch({effects}); return; }
                // An empty cell gets a space on each side, as a typed one would.
                const insert = range.empty && range.end > range.start ? ` ${next} ` : next;
                const from = range.empty ? line.from + range.start : line.from + range.from;
                const to = range.empty ? line.from + range.end : line.from + range.to;
                view.dispatch({changes: {from, to, insert}, effects, userEvent: 'input.table'});
            });
            return cell;
        };
        const headRow = document.createElement('tr');
        header.forEach((cell, c) => headRow.appendChild(makeCell('th', cell, 0, c)));
        head.appendChild(headRow);
        body.forEach((row, n) => {
            const tr = document.createElement('tr');
            header.forEach((_, c) => tr.appendChild(makeCell('td', row[c] || '', n + 1, c)));
            tbody.appendChild(tr);
        });
        table.append(head, tbody);
        scroller.appendChild(table);
        wrap.appendChild(scroller);
        wrap.addEventListener('mousedown', (event) => {
            const link = event.target.closest?.('a');
            if (link?.classList.contains('cm-ob-wikilink')) {
                event.preventDefault();
                jumpToHeading(view, link.dataset.target);
                return;
            }
            if (link) return;
            // A click on a cell edits that cell in place (the browser focuses it).
            if (event.target.closest?.('th, td')) return;
            event.preventDefault();
        });
        return wrap;
    }

    ignoreEvent() { return true; }

    ignoreMutation() { return true; }
}

const quoteClasses = (quote, {first = false, last = false} = {}) => {
    const classes = [quote.callout ? 'cm-ob-callout' : 'cm-ob-quote'];
    if (quote.callout) classes.push(`cm-ob-callout-${CALLOUT_TYPES.has(quote.callout) ? quote.callout : 'note'}`);
    if (first) classes.push('cm-ob-block-first');
    if (last) classes.push('cm-ob-block-last');
    return classes;
};

// ---- Line structure: which lines are quotes/callouts, tables, list items ----

export const analyzeLines = (lines) => {
    const info = lines.map(() => ({}));
    let fence = null;
    let listStack = [];
    for (let i = 0; i < lines.length; i += 1) {
        const text = lines[i];
        const fenceMatch = text.match(FENCE);
        if (fence) {
            info[i].code = true;
            if (fenceMatch && fenceMatch[1][0] === fence[0] && fenceMatch[1].length >= fence.length) fence = null;
            continue;
        }
        if (fenceMatch) { fence = fenceMatch[1]; info[i].code = true; continue; }

        if (!text.trim()) { info[i].blank = true; continue; }
        const lead = indentWidth(text.match(/^\s*/)[0]);
        const levelFor = (width) => listStack.filter((w) => w < width).length;
        const listMatch = text.match(LIST_ITEM);
        const quoteMatch = text.match(QUOTE);
        if (listMatch) {
            const width = indentWidth(listMatch[1]);
            listStack = listStack.filter((w) => w < width);
            info[i].list = {level: listStack.length, ws: listMatch[1].length, marker: listMatch[2], gap: listMatch[3].length};
            listStack.push(width);
        } else if (quoteMatch) {
            const width = indentWidth(quoteMatch[1]);
            if (width === 0) listStack = [];
            info[i].quote = {level: width ? levelFor(width) : 0, ws: quoteMatch[1].length, prefix: quoteMatch[0].length, indent: width};
        } else if (lead === 0) {
            listStack = [];
        } else {
            info[i].continuation = {level: levelFor(lead), ws: text.match(/^\s*/)[0].length};
        }
    }

    // Group consecutive quote lines with the same indent into one block and
    // read a callout header off its first line.
    for (let i = 0; i < lines.length; i += 1) {
        if (!info[i].quote || info[i].quote.block) continue;
        const start = i;
        let end = i;
        while (end + 1 < lines.length && info[end + 1].quote && info[end + 1].quote.indent === info[i].quote.indent) end += 1;
        const head = lines[start].slice(info[start].quote.prefix).match(CALLOUT_HEAD);
        const block = {start, end, callout: head ? head[1].toLowerCase() : null};
        for (let k = start; k <= end; k += 1) info[k].quote.block = block;
        if (head) info[start].quote.calloutHead = head[0].length;
        i = end;
    }

    // Tables, inside or outside a quote: a header row, a separator, body rows.
    for (let i = 0; i + 1 < lines.length; i += 1) {
        if (info[i].code || info[i].table) continue;
        const bodyOf = (k) => (info[k].quote ? lines[k].slice(info[k].quote.prefix) : lines[k]).trim();
        const sameQuote = (k) => (info[k].quote?.block || null) === (info[i].quote?.block || null);
        if (!bodyOf(i).startsWith('|') || !sameQuote(i + 1) || !TABLE_SEPARATOR.test(bodyOf(i + 1))) continue;
        let end = i + 1;
        while (end + 1 < lines.length && sameQuote(end + 1) && !info[end + 1].code && bodyOf(end + 1).startsWith('|')) end += 1;
        const rows = [i, ...Array.from({length: end - i - 1}, (_, n) => i + 2 + n)].map((k) => splitRow(bodyOf(k)));
        const table = {start: i, end, rows};
        for (let k = i; k <= end; k += 1) info[k].table = table;
        i = end;
    }
    return info;
};

// ---- Inline decorations on one line's content ----

const INLINE = /!\[([^\]]*)\]\(([^)\s]+)\)|\[\[([^\]|]+)(?:\|([^\]]+))?\]\]|\[([^\]]+)\]\(([^)\s]+)\)|`([^`]+)`|\*\*(.+?)\*\*|(?<![*\w])\*([^*\s][^*]*?)\*(?!\*)/g;

const addInline = (out, text, offset, active, context) => {
    INLINE.lastIndex = 0;
    let match;
    while ((match = INLINE.exec(text))) {
        const from = offset + match.index;
        const to = from + match[0].length;
        if (match[2] !== undefined) {
            const {src, crop} = parseCrop(match[2]);
            const widget = new ImageWidget(resolveImageSrc(src, context), match[1], crop);
            if (active) {
                out.push(Decoration.mark({class: 'cm-ob-source'}).range(from, to));
                out.push(Decoration.widget({widget, side: 1}).range(to));
            } else {
                out.push(Decoration.replace({widget}).range(from, to));
            }
        } else if (match[3] !== undefined) {
            if (active) out.push(Decoration.mark({class: 'cm-ob-wikilink-src'}).range(from, to));
            else out.push(Decoration.replace({widget: new LinkWidget(match[4] || match[3].replace(/^#/, ''), match[3], 'wiki')}).range(from, to));
        } else if (match[5] !== undefined) {
            if (active) out.push(Decoration.mark({class: 'cm-ob-link-src'}).range(from, to));
            else out.push(Decoration.replace({widget: new LinkWidget(match[5], match[6], 'url')}).range(from, to));
        } else if (match[7] !== undefined) {
            out.push(Decoration.mark({class: 'cm-ob-code'}).range(from, to));
            if (!active) {
                out.push(Decoration.replace({}).range(from, from + 1));
                out.push(Decoration.replace({}).range(to - 1, to));
            }
        } else {
            const delimiter = match[8] !== undefined ? 2 : 1;
            out.push(Decoration.mark({class: delimiter === 2 ? 'cm-ob-strong' : 'cm-ob-em'}).range(from, to));
            const inner = match[8] !== undefined ? match[8] : match[9];
            const pending = INLINE.lastIndex;
            addInline(out, inner, from + delimiter, active, context);
            INLINE.lastIndex = pending;
            if (active) {
                out.push(Decoration.mark({class: 'cm-ob-delim'}).range(from, from + delimiter));
                out.push(Decoration.mark({class: 'cm-ob-delim'}).range(to - delimiter, to));
            } else {
                out.push(Decoration.replace({}).range(from, from + delimiter));
                out.push(Decoration.replace({}).range(to - delimiter, to));
            }
        }
    }
};

const lineStyle = (level, hanging) => (hanging
    ? `padding-left: ${(level + 1) * 1.6}em; text-indent: -1.6em;`
    : `padding-left: ${level * 1.6}em;`);

const activeLinesOf = (state, focused) => {
    const active = new Set();
    if (!focused) return active;
    for (const range of state.selection.ranges) {
        const first = state.doc.lineAt(range.from).number;
        const last = state.doc.lineAt(range.to).number;
        for (let n = first; n <= last; n += 1) active.add(n);
    }
    return active;
};

const buildDecorations = (state, focused, context) => {
    const doc = state.doc;
    const lines = [];
    for (let n = 1; n <= doc.lines; n += 1) lines.push(doc.line(n).text);
    const info = analyzeLines(lines);
    const active = activeLinesOf(state, focused);
    const flash = state.field(flashField, false);
    const out = [];

    for (let i = 0; i < lines.length; i += 1) {
        const line = doc.line(i + 1);
        const text = line.text;
        const meta = info[i];
        const isActive = active.has(i + 1);
        if (flash !== null && flash === line.from) out.push(Decoration.line({class: 'cm-ob-flash'}).range(line.from));

        if (meta.code) {
            out.push(Decoration.line({class: 'cm-ob-codeblock'}).range(line.from));
            continue;
        }

        if (meta.table) {
            const table = meta.table;
            const editingHere = state.field(editingTableField, false) === doc.line(table.start + 1).from;
            const blockActive = !editingHere && Array.from({length: table.end - table.start + 1}, (_, n) => table.start + n + 1).some((n) => active.has(n));
            if (!blockActive && i === table.start) {
                const quote = meta.quote ? {
                    callout: meta.quote.block.callout,
                    level: meta.quote.level,
                    endsHere: meta.quote.block.end === table.end,
                } : null;
                out.push(Decoration.replace({
                    widget: new TableWidget(table.rows, line.from, quote),
                    block: true,
                }).range(line.from, doc.line(table.end + 1).to));
            }
            if (!blockActive) { i = table.end; continue; }
            // Editing the table: show its rows as source, still inside its quote.
            const classes = ['cm-ob-table-src'];
            let style = '';
            if (meta.quote) {
                classes.push(...quoteClasses(meta.quote.block, {first: i === meta.quote.block.start, last: i === meta.quote.block.end}));
                style = `margin-left: ${meta.quote.level * 1.6}em;`;
                if (meta.quote.ws) out.push(Decoration.replace({}).range(line.from, line.from + meta.quote.ws));
            }
            out.push(Decoration.line({class: classes.join(' '), attributes: style ? {style} : {}}).range(line.from));
            continue;
        }

        if (meta.blank) continue;

        if (meta.quote) {
            const {block, level, ws, prefix, calloutHead} = meta.quote;
            const classes = quoteClasses(block, {first: i === block.start, last: i === block.end});
            out.push(Decoration.line({class: classes.join(' '), attributes: {style: `margin-left: ${level * 1.6}em;`}}).range(line.from));
            if (ws) out.push(Decoration.replace({}).range(line.from, line.from + ws));
            if (isActive) out.push(Decoration.mark({class: 'cm-ob-delim'}).range(line.from + ws, line.from + prefix));
            else out.push(Decoration.replace({}).range(line.from + ws, line.from + prefix));
            let contentStart = line.from + prefix;
            if (calloutHead) {
                const headEnd = contentStart + calloutHead;
                const title = text.slice(prefix + calloutHead).trim();
                if (isActive) out.push(Decoration.mark({class: 'cm-ob-delim'}).range(contentStart, headEnd));
                else out.push(Decoration.replace({widget: new CalloutLabelWidget(block.callout, !title)}).range(contentStart, headEnd));
                if (title) out.push(Decoration.mark({class: 'cm-ob-callout-title'}).range(headEnd, line.to));
                contentStart = headEnd;
            }
            addInline(out, text.slice(contentStart - line.from), contentStart, isActive, context);
            continue;
        }

        if (meta.list) {
            const {level, ws, marker, gap} = meta.list;
            out.push(Decoration.line({class: 'cm-ob-li', attributes: {style: lineStyle(level, true)}}).range(line.from));
            if (ws) out.push(Decoration.replace({}).range(line.from, line.from + ws));
            const markerFrom = line.from + ws;
            const markerTo = markerFrom + marker.length + gap;
            if (isActive) out.push(Decoration.mark({class: 'cm-ob-marker cm-ob-marker-src'}).range(markerFrom, markerTo));
            else out.push(Decoration.replace({widget: new BulletWidget(marker, /\d/.test(marker))}).range(markerFrom, markerTo));
            addInline(out, text.slice(ws + marker.length + gap), markerTo, isActive, context);
            continue;
        }

        if (meta.continuation) {
            const {level, ws} = meta.continuation;
            out.push(Decoration.line({attributes: {style: lineStyle(level, false)}}).range(line.from));
            if (ws) out.push(Decoration.replace({}).range(line.from, line.from + ws));
            addInline(out, text.slice(ws), line.from + ws, isActive, context);
            continue;
        }

        const heading = text.match(HEADING);
        if (heading) {
            out.push(Decoration.line({class: `cm-ob-h${heading[1].length}`}).range(line.from));
            const markEnd = line.from + heading[0].length;
            if (isActive) out.push(Decoration.mark({class: 'cm-ob-delim'}).range(line.from, markEnd));
            else out.push(Decoration.replace({}).range(line.from, markEnd));
            addInline(out, text.slice(heading[0].length), markEnd, isActive, context);
            continue;
        }

        if (RULE.test(text)) {
            if (!isActive) out.push(Decoration.replace({widget: new RuleWidget()}).range(line.from, line.to));
            continue;
        }

        if (/^\s*<!--.*-->\s*$/.test(text)) {
            out.push(Decoration.line({class: 'cm-ob-comment'}).range(line.from));
            continue;
        }

        addInline(out, text, line.from, isActive, context);
    }
    return Decoration.set(out, true);
};

// ---- Wiring ----

const setFocused = StateEffect.define();
const focusField = StateField.define({
    create: () => false,
    update(value, tr) {
        for (const effect of tr.effects) if (effect.is(setFocused)) return effect.value;
        return value;
    },
});

export const noteLivePreview = (context = {}) => {
    const decorations = StateField.define({
        create: (state) => buildDecorations(state, state.field(focusField), context),
        update(value, tr) {
            const focusChanged = tr.effects.some((effect) => effect.is(setFocused) || effect.is(flashHeading) || effect.is(setEditingTable));
            if (!tr.docChanged && !tr.selection && !focusChanged) return value;
            return buildDecorations(tr.state, tr.state.field(focusField), context);
        },
        provide: (field) => EditorView.decorations.from(field),
    });
    return [
        focusField,
        flashField,
        editingTableField,
        decorations,
        EditorView.focusChangeEffect.of((_, focusing) => setFocused.of(focusing)),
        EditorState.allowMultipleSelections.of(false),
    ];
};
