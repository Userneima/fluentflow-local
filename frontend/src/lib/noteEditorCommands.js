// The note editor's toolbar, as edits of the markdown source (decision 5.34).
// Each command changes only the characters it is about, so a note written in
// Obsidian keeps its exact text everywhere the user did not touch.

const HEADING_PREFIX = /^(#{1,6})\s+/;
const LIST_PREFIX = /^(\s*)([-*+]|\d+[.)])\s+/;
// The quote marks of a quote or callout line, any depth: markers go after them.
const QUOTE_PREFIX = /^(?:\s*>\s?)*/;
const TABLE_ROW = /^\s*\|/;

// Where a line's own text starts, past its quote marks.
const bodyOf = (line) => {
    const quote = line.text.match(QUOTE_PREFIX)[0];
    return {from: line.from + quote.length, text: line.text.slice(quote.length)};
};

const selectedLines = (state) => {
    const lines = [];
    const seen = new Set();
    for (const range of state.selection.ranges) {
        const first = state.doc.lineAt(range.from).number;
        const last = state.doc.lineAt(range.to).number;
        for (let n = first; n <= last; n += 1) {
            if (seen.has(n)) continue;
            seen.add(n);
            lines.push(state.doc.line(n));
        }
    }
    return lines;
};

// 'p', or 'h2' / 'h3' / 'h4' for the line the cursor is on.
export const lineStyleOf = (state) => {
    const match = bodyOf(state.doc.lineAt(state.selection.main.head)).text.match(HEADING_PREFIX);
    return match ? `h${match[1].length}` : 'p';
};

// Turn the selected lines into body text or a heading of the given level.
export const setLineStyle = (view, style) => {
    const level = style === 'p' ? 0 : Number(String(style).slice(1)) || 0;
    const prefix = level ? `${'#'.repeat(level)} ` : '';
    const changes = selectedLines(view.state).flatMap((line) => {
        const body = bodyOf(line);
        // A callout's title line and a table row are not headings.
        if (TABLE_ROW.test(body.text) || /^\[!/.test(body.text)) return [];
        const current = body.text.match(HEADING_PREFIX);
        const from = body.from;
        const to = body.from + (current ? current[0].length : 0);
        if (body.text.slice(0, to - from) === prefix) return [];
        if (!body.text.trim() && !prefix) return [];
        return [{from, to, insert: prefix}];
    });
    if (changes.length) view.dispatch({changes, userEvent: 'input.format'});
    view.focus();
};

// Wrap the selection in a mark (** for bold, * for italic), or unwrap it when
// it already is. With nothing selected, the marks go in around the cursor.
export const toggleInline = (view, mark) => {
    const {state} = view;
    const range = state.selection.main;
    const before = state.sliceDoc(Math.max(0, range.from - mark.length), range.from);
    const after = state.sliceDoc(range.to, range.to + mark.length);
    const inner = state.sliceDoc(range.from, range.to);
    if (!range.empty && before === mark && after === mark) {
        view.dispatch({
            changes: [{from: range.from - mark.length, to: range.from, insert: ''}, {from: range.to, to: range.to + mark.length, insert: ''}],
            selection: {anchor: range.from - mark.length, head: range.to - mark.length},
            userEvent: 'input.format',
        });
    } else if (!range.empty && inner.startsWith(mark) && inner.endsWith(mark) && inner.length >= mark.length * 2) {
        view.dispatch({
            changes: {from: range.from, to: range.to, insert: inner.slice(mark.length, inner.length - mark.length)},
            selection: {anchor: range.from, head: range.to - mark.length * 2},
            userEvent: 'input.format',
        });
    } else {
        view.dispatch({
            changes: {from: range.from, to: range.to, insert: `${mark}${inner}${mark}`},
            selection: {anchor: range.from + mark.length, head: range.to + mark.length},
            userEvent: 'input.format',
        });
    }
    view.focus();
};

// Make the selected lines a bullet or numbered list, or take the marker off
// when every one of them already is that kind of list.
export const toggleList = (view, kind) => {
    // A table row or a callout's title line cannot become a list item.
    const lines = selectedLines(view.state)
        .map((line) => ({line, body: bodyOf(line)}))
        .filter(({body}) => body.text.trim() && !TABLE_ROW.test(body.text) && !/^\[!/.test(body.text));
    if (!lines.length) { view.focus(); return; }
    const isKind = (text) => {
        const match = text.match(LIST_PREFIX);
        if (!match) return false;
        return kind === 'bullet' ? /^[-*+]$/.test(match[2]) : /^\d+[.)]$/.test(match[2]);
    };
    const remove = lines.every(({body}) => isKind(body.text));
    const changes = lines.map(({body}, n) => {
        const match = body.text.match(LIST_PREFIX);
        const indent = match ? match[1] : (body.text.match(/^\s*/) || [''])[0];
        const from = body.from;
        const to = body.from + (match ? match[0].length : indent.length);
        if (remove) return {from, to, insert: indent};
        return {from, to, insert: `${indent}${kind === 'bullet' ? '- ' : `${n + 1}. `}`};
    });
    view.dispatch({changes, userEvent: 'input.format'});
    view.focus();
};
