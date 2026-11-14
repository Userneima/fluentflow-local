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

// Keep the part of the section with row `rowId` that is being talked about in
// view. `progress` (0 to 1) is how far into the section's span the playback
// is; the place it points to is that share down the section's own text (to
// its next heading of any level). A section that fits in the view is shown
// from its heading, as before. A longer one is scrolled along: once the place
// talked about leaves the upper two thirds of the view, it is brought back to
// a quarter from the top, never above the section's heading (decision 6.66).
// Moving to another section jumps; moving inside one scrolls smoothly.
const lastFollowed = new WeakMap();

// Where the scroller should be (its scrollTop), or null to stay: the section
// spans `sectionTop` to `sectionBottom` in the scroller's own measure.
// `entering`: the section has just become the one playing, so a long one is
// shown from near its heading rather than wherever its start happens to sit.
// Lines of a section that were not said in the recording: the fact checks
// added after the lecture (a quote starting "**核对（日期）**", with the quote
// lines after it) and empty lines. The reading point is placed by playback
// progress over the other lines only, so two dozen checks in lesson three no
// longer push it later than the talk (owner, 2026-11-14, decision 6.66).
const CHECK_START = /^\s*>\s*\*\*核对（/;
const QUOTE = /^\s*>/;
export const unspokenLines = (lines) => {
    let inCheck = false;
    return lines.map((line) => {
        if (CHECK_START.test(line)) inCheck = true;
        else if (!QUOTE.test(line)) inCheck = false;
        return inCheck || !line.trim();
    });
};

// Where `share` of the way through the spoken lines falls, in pixels:
// `blocks` are the section's lines, in order, as {top, bottom, spoken}.
// null when none was spoken.
export const spokenPoint = (blocks, share) => {
    const spoken = blocks.filter((block) => block.spoken && block.bottom > block.top);
    const total = spoken.reduce((sum, block) => sum + (block.bottom - block.top), 0);
    if (!total) return null;
    let left = Math.min(1, Math.max(0, Number(share) || 0)) * total;
    for (const block of spoken) {
        const tall = block.bottom - block.top;
        if (left <= tall) return block.top + left;
        left -= tall;
    }
    return spoken[spoken.length - 1].bottom;
};

export const followScrollTop = ({sectionTop, sectionBottom, top, height, progress = 0, entering = false, talkedAt = null}) => {
    const share = Math.min(1, Math.max(0, Number(progress) || 0));
    let want;
    if (sectionBottom - sectionTop <= height * 0.75) {
        if (sectionTop >= top && sectionBottom <= top + height) return null;
        want = sectionTop - 32;
    } else {
        const talked = Number.isFinite(talkedAt) ? talkedAt : sectionTop + share * (sectionBottom - sectionTop);
        const headingShown = sectionTop >= top && sectionTop <= top + height * (entering ? 0.25 : 0.75);
        if (talked >= top && talked <= top + height * 0.65 && (headingShown || sectionTop < top)) return null;
        want = Math.max(sectionTop - 32, talked - height * 0.25);
    }
    want = Math.max(0, Math.round(want));
    return Math.abs(want - top) < 4 ? null : want;
};

export const followSection = (view, rowId, progress = 0) => {
    if (!view || !rowId) return false;
    const {rows} = view.state.field(followField);
    const sections = noteSections(view.state.doc.toString());
    const matched = matchRows(rows || [], sections);
    const index = sections.findIndex((item) => matched.get(item.index)?.id === rowId);
    if (index < 0) return false;
    const doc = view.state.doc;
    const scroller = view.scrollDOM;
    const height = scroller.clientHeight;
    if (!(height > 0)) return false;
    const ownLast = index + 1 < sections.length ? sections[index + 1].line - 1 : doc.lines;
    const first = view.lineBlockAt(doc.line(sections[index].line).from);
    const last = view.lineBlockAt(doc.line(Math.max(ownLast, sections[index].line)).to);
    // Line blocks are measured from the top of the document; the scroller's
    // padding sits above it.
    const origin = view.documentTop - scroller.getBoundingClientRect().top + scroller.scrollTop;
    const sectionTop = origin + first.top;
    const sectionBottom = origin + last.bottom;
    const top = scroller.scrollTop;
    const same = lastFollowed.get(view) === rowId;
    const bodyLines = [];
    for (let number = sections[index].line + 1; number <= ownLast; number += 1) bodyLines.push(number);
    const unspoken = unspokenLines(bodyLines.map((number) => doc.line(number).text));
    const talkedAt = spokenPoint(bodyLines.map((number, i) => {
        const block = view.lineBlockAt(doc.line(number).from);
        return {top: origin + block.top, bottom: origin + block.bottom, spoken: !unspoken[i]};
    }), progress);
    const want = followScrollTop({sectionTop, sectionBottom, top, height, progress, entering: !same, talkedAt});
    lastFollowed.set(view, rowId);
    if (want === null) return false;
    if (typeof scroller.scrollTo === 'function') scroller.scrollTo({top: want, behavior: same ? 'smooth' : 'auto'});
    else scroller.scrollTop = want;
    return true;
};
