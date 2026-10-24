// The note following playback (decision 6.37, mock-up behind `?follow=on`).
// The service keeps, for the level-2 and level-3 headings, the span of the
// recording each came from (`note_section_times`, one row per section with a
// stable `id`). How a row finds its section in the note as it is now is the
// service's rule (backend/core/note_section_times.py `match_rows`): level and
// heading text; among headings with the same text, the hash of the section's
// own body (several with one hash pair in order only when the counts agree);
// what is left over pairs in order only when the counts agree.

const PART_MARKER = /<!--\s*第\s*\d+\/\d+\s*部分.*?-->/g;
const HEADING = /^(#{2,3})\s+(.*?)\s*#*\s*$/;
const FENCE = /^\s*(```|~~~)/;
const encoder = new TextEncoder();

// FNV-1a (32 bit) of the text's UTF-8 bytes, as 8 hex digits; the service
// computes the same.
export const bodyHash = (text) => {
    let value = 0x811c9dc5;
    for (const byte of encoder.encode(text)) {
        value ^= byte;
        value = Math.imul(value, 0x01000193) >>> 0;
    }
    return value.toString(16).padStart(8, '0');
};

// Each heading of the note, in order, with the line it is on (1-based, as
// CodeMirror counts) and the hash of its own body.
export const noteSections = (markdown) => {
    const lines = String(markdown || '').replace(PART_MARKER, '').split(/\r?\n/);
    const heads = [];
    let inFence = false;
    lines.forEach((raw, number) => {
        if (FENCE.test(raw)) { inFence = !inFence; return; }
        if (inFence) return;
        const match = HEADING.exec(raw);
        if (match && match[2]) heads.push({number, level: match[1].length, heading: match[2].trim()});
    });
    return heads.map((head, index) => {
        const end = index + 1 < heads.length ? heads[index + 1].number : lines.length;
        const body = lines.slice(head.number + 1, end).join('\n').trim();
        return {index, heading: head.heading, level: head.level, line: head.number + 1, bodyHash: bodyHash(body)};
    });
};

// The rows the service sent, keeping only well-formed spans.
export const normalizeRows = (rows) => (Array.isArray(rows) ? rows : []).flatMap((row) => {
    const primary = Array.isArray(row?.primary) ? row.primary.map(Number) : null;
    if (!row?.id || !primary || primary.length !== 2 || !primary.every(Number.isFinite)) return [];
    const others = (Array.isArray(row.others) ? row.others : [])
        .map((span) => (Array.isArray(span) ? span.map(Number) : []))
        .filter((span) => span.length === 2 && span.every(Number.isFinite) && span[1] > span[0]);
    return [{id: String(row.id), heading: String(row.heading || ''), level: Number(row.level) || 2, bodyHash: row.body_hash || '', primary, others}];
});

// Which row each section has: Map(section index -> row).
export const matchRows = (rows, sections) => {
    const groups = new Map();
    const group = (level, heading) => {
        const key = `${level}\u0000${heading}`;
        if (!groups.has(key)) groups.set(key, {rows: [], sections: []});
        return groups.get(key);
    };
    rows.forEach((row) => group(row.level, row.heading).rows.push(row));
    sections.forEach((section) => {
        const key = `${section.level}\u0000${section.heading}`;
        if (groups.has(key)) groups.get(key).sections.push(section);
    });
    const matched = new Map();
    groups.forEach(({rows: groupRows, sections: groupSections}) => {
        let rowsLeft = [...groupRows];
        let sectionsLeft = [...groupSections];
        // Same heading and same body: in order only when as many rows as
        // sections share it; otherwise none of them keeps a time.
        new Set(groupSections.map((section) => section.bodyHash)).forEach((digest) => {
            const sameRows = rowsLeft.filter((row) => row.bodyHash === digest);
            const sameSections = sectionsLeft.filter((section) => section.bodyHash === digest);
            if (!sameRows.length) return;
            if (sameRows.length === sameSections.length) sameSections.forEach((section, i) => matched.set(section.index, sameRows[i]));
            rowsLeft = rowsLeft.filter((row) => row.bodyHash !== digest);
            sectionsLeft = sectionsLeft.filter((section) => section.bodyHash !== digest);
        });
        if (rowsLeft.length === sectionsLeft.length) sectionsLeft.forEach((section, i) => matched.set(section.index, rowsLeft[i]));
    });
    return matched;
};

const inside = (span, time) => time >= span[0] && time < span[1];

export const rowHolds = (row, time) => !!row && (inside(row.primary, time) || row.others.some((span) => inside(span, time)));

// The row to highlight at `time` among `rows` (those found in the note): the
// deepest heading whose main span or one of its other spans holds it; among
// several of one level, a main span wins over another's other span, then the
// latest start. `null` when none holds it (the caller keeps the last one:
// people quietly writing in a shared document still belong to the section
// before).
export const sectionAt = (time, rows) => {
    if (!Number.isFinite(time) || !rows?.length) return null;
    let best = null;
    let bestRank = null;
    rows.forEach((row) => {
        const main = inside(row.primary, time);
        const other = main ? null : row.others.find((span) => inside(span, time));
        if (!main && !other) return;
        const rank = [row.level, main ? 1 : 0, main ? row.primary[0] : other[0]];
        const better = !bestRank || rank[0] > bestRank[0]
            || (rank[0] === bestRank[0] && (rank[1] > bestRank[1] || (rank[1] === bestRank[1] && rank[2] > bestRank[2])));
        if (better) { best = row.id; bestRank = rank; }
    });
    return best;
};
