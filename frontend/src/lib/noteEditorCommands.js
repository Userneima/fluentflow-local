// The note editor's toolbar, as edits of the markdown source (decision 5.34).
// Each command changes only the characters it is about, so a note written in
// Obsidian keeps its exact text everywhere the user did not touch.

const HEADING_PREFIX = /^(#{1,6})\s+/;
const LIST_PREFIX = /^(\s*)([-*+]|\d+[.)])\s+/;

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
    const match = state.doc.lineAt(state.selection.main.head).text.match(HEADING_PREFIX);
    return match ? `h${match[1].length}` : 'p';
};

// Turn the selected lines into body text or a heading of the given level.
export const setLineStyle = (view, style) => {
    const level = style === 'p' ? 0 : Number(String(style).slice(1)) || 0;
    const prefix = level ? `${'#'.repeat(level)} ` : '';
    const changes = selectedLines(view.state).flatMap((line) => {
        const current = line.text.match(HEADING_PREFIX);
        const from = line.from;
        const to = line.from + (current ? current[0].length : 0);
        if (line.text.slice(0, to - from) === prefix) return [];
        if (!line.text.trim() && !prefix) return [];
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
    const lines = selectedLines(view.state).filter((line) => line.text.trim());
    if (!lines.length) { view.focus(); return; }
    const isKind = (text) => {
        const match = text.match(LIST_PREFIX);
        if (!match) return false;
        return kind === 'bullet' ? /^[-*+]$/.test(match[2]) : /^\d+[.)]$/.test(match[2]);
    };
    const remove = lines.every((line) => isKind(line.text));
    const changes = lines.map((line, n) => {
        const match = line.text.match(LIST_PREFIX);
        const indent = match ? match[1] : (line.text.match(/^\s*/) || [''])[0];
        const from = line.from;
        const to = line.from + (match ? match[0].length : indent.length);
        if (remove) return {from, to, insert: indent};
        return {from, to, insert: `${indent}${kind === 'bullet' ? '- ' : `${n + 1}. `}`};
    });
    view.dispatch({changes, userEvent: 'input.format'});
    view.focus();
};
