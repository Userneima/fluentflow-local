// The note following playback (decision 6.37, mock-up behind `?follow=on`).
// The service keeps, for each level-2 and level-3 heading, the span of the
// recording it came from (`note_section_times`); a heading is named by its
// level, its parent heading, its text and which occurrence of that text it
// is, the same key the service builds (backend/core/note_section_times.py).

const PART_MARKER = /<!--\s*第\s*\d+\/\d+\s*部分.*?-->/g;
const HEADING = /^(#{2,3})\s+(.*?)\s*#*\s*$/;
const FENCE = /^\s*(```|~~~)/;

export const sectionKey = (level, parent, heading, occurrence = 1) => JSON.stringify([level, parent || '', heading, occurrence]);

// Each heading of the note with the line it is on (1-based, as CodeMirror
// counts) and its key.
export const noteSections = (markdown) => {
    const sections = [];
    const seen = new Map();
    let parent = null;
    let inFence = false;
    String(markdown || '').split(/\r?\n/).forEach((raw, index) => {
        if (FENCE.test(raw)) { inFence = !inFence; return; }
        if (inFence) return;
        const match = HEADING.exec(raw.replace(PART_MARKER, ''));
        if (!match || !match[2]) return;
        const level = match[1].length;
        const heading = match[2].trim();
        const owner = level === 2 ? null : parent;
        if (level === 2) parent = heading;
        const base = JSON.stringify([level, owner, heading]);
        seen.set(base, (seen.get(base) || 0) + 1);
        sections.push({key: sectionKey(level, owner, heading, seen.get(base)), heading, level, parent: owner, line: index + 1});
    });
    return sections;
};

// The table's rows by key, keeping only well-formed spans.
export const timesByKey = (rows) => {
    const map = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
        const primary = Array.isArray(row?.primary) ? row.primary.map(Number) : null;
        if (!row?.key || !primary || primary.length !== 2 || !primary.every(Number.isFinite)) return;
        const others = (Array.isArray(row.others) ? row.others : [])
            .map((span) => (Array.isArray(span) ? span.map(Number) : []))
            .filter((span) => span.length === 2 && span.every(Number.isFinite) && span[1] > span[0]);
        map.set(row.key, {primary, others, level: Number(row.level) || 2});
    });
    return map;
};

const inside = (span, time) => time >= span[0] && time < span[1];

// The section to highlight at `time`: the deepest heading whose main span or
// one of its other spans holds it; a level-3 heading wins over its chapter.
// Among several of one level, the main span wins over another's other span,
// then the latest start. `null` when none holds it (the caller keeps the
// last one: people quietly writing in a shared document for a minute and a
// half still belong to the section before).
export const sectionAt = (time, times) => {
    if (!Number.isFinite(time) || !times?.size) return null;
    let best = null;
    let bestRank = null;
    times.forEach((entry, key) => {
        const main = inside(entry.primary, time);
        const other = !main && entry.others.some((span) => inside(span, time));
        if (!main && !other) return;
        const start = main ? entry.primary[0] : entry.others.find((span) => inside(span, time))[0];
        const rank = [entry.level, main ? 1 : 0, start];
        if (!bestRank || rank[0] > bestRank[0] || (rank[0] === bestRank[0] && (rank[1] > bestRank[1] || (rank[1] === bestRank[1] && rank[2] > bestRank[2])))) {
            best = key;
            bestRank = rank;
        }
    });
    return best;
};
