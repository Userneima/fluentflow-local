import {StateEffect, StateField} from '@codemirror/state';
import {Decoration, EditorView, WidgetType} from '@codemirror/view';
import {fmtElapsed} from './format.js';
import {matchRows, noteSections} from './noteFollow.js';

// The note editor's side of following playback (decision 6.37): a time beside
// each heading the service matched to the recording, and the section playing
// now marked. Both are decorations: the markdown is never touched, so nothing
// here can make the note look changed or be saved (decisions 6.27, 6.30).

export const setNoteFollow = StateEffect.define();

const followField = StateField.define({
    create: () => ({rows: null, activeKey: null, alsoKeys: []}),
    update(value, tr) {
        let next = value;
        for (const effect of tr.effects) if (effect.is(setNoteFollow)) next = {...next, ...effect.value};
        return next;
    },
});

class TimeWidget extends WidgetType {
    constructor(rowId, seconds, label, onSeek) {
        super();
        this.rowId = rowId;
        this.seconds = seconds;
        this.label = label;
        this.onSeek = onSeek;
    }

    eq(other) { return other.rowId === this.rowId && other.seconds === this.seconds && other.label === this.label; }

    toDOM() {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'cm-follow-time';
        button.textContent = fmtElapsed(this.seconds);
        button.title = this.label;
        button.setAttribute('aria-label', this.label);
        // The click must not move the cursor into the heading (which would
        // show its `##` source) or take the focus from the player.
        button.addEventListener('mousedown', (event) => event.preventDefault());
        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            this.onSeek?.(this.seconds, this.rowId);
        });
        return button;
    }

    ignoreEvent() { return true; }
}

// The lines a section spans: from its heading to the next heading of the
// same or a higher level.
const sectionEnd = (sections, index, lastLine) => {
    const level = sections[index].level;
    for (let i = index + 1; i < sections.length; i += 1) if (sections[i].level <= level) return sections[i].line - 1;
    return lastLine;
};

const buildDecorations = (state, {lang, onSeek}) => {
    const {rows, activeKey, alsoKeys} = state.field(followField);
    if (!rows?.length) return Decoration.none;
    const doc = state.doc;
    const sections = noteSections(doc.toString());
    const matched = matchRows(rows, sections);
    const also = new Set(alsoKeys || []);
    // The section followed, to leave its parent heading unmarked: the parent
    // holds the moment too, as a whole, and marking it lightly says nothing.
    const activeIndex = sections.findIndex((section) => matched.get(section.index)?.id === activeKey);
    const out = [];
    sections.forEach((section, index) => {
        const entry = matched.get(section.index);
        const line = doc.line(section.line);
        const last = Math.min(sectionEnd(sections, index, doc.lines), doc.lines);
        const holdsActive = activeIndex > index && sections[activeIndex].line <= last;
        const mark = entry && entry.id === activeKey ? 'active' : entry && also.has(entry.id) && !holdsActive ? 'also' : '';
        if (mark) {
            out.push(Decoration.line({class: `cm-follow-${mark} cm-follow-${mark}-heading`}).range(line.from));
            for (let n = section.line + 1; n <= last; n += 1) out.push(Decoration.line({class: `cm-follow-${mark}`}).range(doc.line(n).from));
        }
        if (entry) {
            const label = lang === 'zh'
                ? `从 ${fmtElapsed(entry.primary[0])} 播放这一节讲得最多的那一段`
                : `Play from ${fmtElapsed(entry.primary[0])}, where this section is discussed most`;
            out.push(Decoration.widget({widget: new TimeWidget(entry.id, entry.primary[0], label, onSeek), side: 1}).range(line.to));
        }
    });
    return Decoration.set(out, true);
};

export const followTheme = EditorView.theme({
    '.cm-follow-active': {
        boxShadow: 'inset 3px 0 0 rgba(59, 130, 246, 0.85)',
        backgroundColor: 'rgba(59, 130, 246, 0.06)',
    },
    '.cm-follow-active-heading': {backgroundColor: 'rgba(59, 130, 246, 0.12)'},
    // Another place the moment playing is drawn on (a recap at the end):
    // lighter, a thin bar, never scrolled to.
    '.cm-follow-also': {
        boxShadow: 'inset 2px 0 0 rgba(59, 130, 246, 0.35)',
        backgroundColor: 'rgba(59, 130, 246, 0.03)',
    },
    '.cm-follow-time': {
        marginLeft: '10px', padding: '1px 7px', borderRadius: '8px', border: '1px solid var(--c-outline-variant)',
        backgroundColor: 'transparent', color: 'var(--c-on-surface-variant)', cursor: 'pointer',
        fontFamily: 'inherit', fontSize: '12px', fontWeight: '700', lineHeight: '1.6', verticalAlign: 'middle',
        fontVariantNumeric: 'tabular-nums',
    },
    '.cm-follow-time:hover': {color: '#3B82F6', borderColor: 'rgba(59, 130, 246, 0.5)'},
    '.cm-follow-active-heading .cm-follow-time': {color: '#3B82F6', borderColor: 'rgba(59, 130, 246, 0.5)'},
});

// `context.onSeek(seconds, rowId)` is called when a time is clicked. Read through a
// ref by the caller, so the extension is built once per note.
export const noteFollow = (context = {}) => {
    const decorations = StateField.define({
        create: (state) => buildDecorations(state, context),
        update(value, tr) {
            if (!tr.docChanged && !tr.effects.some((effect) => effect.is(setNoteFollow))) return value;
            return buildDecorations(tr.state, context);
        },
        provide: (field) => EditorView.decorations.from(field),
    });
    return [followField, decorations, followTheme];
};

// Bring the heading of the section that has row `rowId` into view unless it
// already is.
export const revealSection = (view, rowId) => {
    if (!view || !rowId) return false;
    const {rows} = view.state.field(followField);
    const sections = noteSections(view.state.doc.toString());
    const matched = matchRows(rows || [], sections);
    const section = sections.find((item) => matched.get(item.index)?.id === rowId);
    if (!section) return false;
    const pos = view.state.doc.line(section.line).from;
    const block = view.lineBlockAt(pos);
    const scroller = view.scrollDOM;
    const top = scroller.scrollTop;
    const height = scroller.clientHeight;
    if (height > 0 && block.top >= top && block.bottom <= top + height * 0.75) return false;
    view.dispatch({effects: EditorView.scrollIntoView(pos, {y: 'start', yMargin: 32})});
    return true;
};
