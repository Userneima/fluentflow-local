import {fmtCharCount, fmtDuration} from './format.js';

// What the page says while FluentFlow writes a note, from what the writer
// reports (backend/core/note_progress.py): the stage it is on, the parts or
// chapters and which are done, the stretch of the recording a part covers.
// Every sentence is a fact the writer gave; none is said for its own sake
// (user feedback, 2026-11-09: minutes of 「正在写笔记」 and nothing else).
//
// `t` is the page's translator (app/shared.jsx, keys noteProgress.*).

const fill = (template, values) => String(template).replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ''));

const count = (value) => Math.max(0, Number(value) || 0);

const chaptersOf = (progress) => (Array.isArray(progress?.chapters) ? progress.chapters : []);

// The main line: what the writer is doing now.
export const noteProgressStatus = (progress, t, lang = 'zh') => {
    if (!progress) return '';
    const stage = String(progress.stage || '');
    const chapters = chaptersOf(progress);
    const partsTotal = count(progress.parts_total);
    if (stage === 'frames') return t('noteProgress.frames');
    if (stage === 'reading') {
        return fill(t('noteProgress.reading'), {done: count(progress.read_done), total: count(progress.read_total)});
    }
    if (stage === 'planning') return t('noteProgress.planning');
    if (stage === 'unifying') return t('noteProgress.unifying');
    if (stage === 'revising') return t('noteProgress.revising');
    if (stage === 'finishing') return t('noteProgress.finishing');
    if (stage === 'checking') {
        return partsTotal > 1
            ? fill(t('noteProgress.checkingPart'), {index: count(progress.part_index), total: partsTotal})
            : t('noteProgress.checking');
    }
    // Writing.
    if (chapters.length) {
        return fill(t('noteProgress.chapters'), {done: chapters.filter((c) => c.done).length, total: chapters.length});
    }
    if (partsTotal > 1) {
        const span = progress.part_starts_at && progress.part_ends_at
            ? `（${progress.part_starts_at}–${progress.part_ends_at}）`
            : '';
        const head = fill(t('noteProgress.part'), {index: count(progress.part_index), total: partsTotal});
        return lang === 'zh' ? `${head}${span}` : `${head}${span.replace('（', ' (').replace('）', ')')}`;
    }
    if (progress.writer === 'claude') return t('noteProgress.claudeOne');
    if (!count(progress.written_chars)) return t('noteProgress.draftWaiting');
    return fill(t('noteProgress.draft'), {chars: fmtCharCount(progress.written_chars, lang)});
};

// The second line: time so far, the parts written, and, once a part is
// done, about how long the rest takes at this run's own pace. Elapsed seconds
// come from the server; `sinceFetch` adds what passed since it answered.
export const noteProgressDetail = (progress, t, lang = 'zh', sinceFetch = 0) => {
    if (!progress) return '';
    const pieces = [fill(t('noteProgress.elapsed'), {time: fmtDuration(count(progress.elapsed_seconds) + count(sinceFetch), lang)})];
    const partsTotal = count(progress.parts_total);
    if (partsTotal > 1 && progress.stage !== 'finishing') {
        pieces.push(fill(t('noteProgress.partsDone'), {done: count(progress.parts_done), total: partsTotal}));
        const perPart = count(progress.seconds_per_part);
        const left = partsTotal - count(progress.parts_done);
        if (perPart > 0 && left > 0) {
            const intoCurrent = Math.min(perPart, count(progress.part_elapsed_seconds) + count(sinceFetch));
            pieces.push(fill(t('noteProgress.remaining'), {time: fmtDuration(Math.max(60, perPart * left - intoCurrent), lang)}));
        }
    }
    return pieces.join(lang === 'zh' ? '，' : ', ');
};

// A few words for a task row and the editor's status pill: 「写好 2/5 章」.
export const noteProgressShort = (progress, t, lang = 'zh') => {
    if (!progress) return '';
    const stage = String(progress.stage || '');
    const chapters = chaptersOf(progress);
    const partsTotal = count(progress.parts_total);
    const keyed = {
        frames: 'noteProgress.shortFrames', planning: 'noteProgress.shortPlanning', unifying: 'noteProgress.shortUnifying',
        checking: 'noteProgress.shortChecking', revising: 'noteProgress.shortRevising', finishing: 'noteProgress.shortFinishing',
    };
    if (stage === 'reading') {
        return fill(t('noteProgress.shortReading'), {done: count(progress.read_done), total: count(progress.read_total)});
    }
    if (stage === 'checking' && partsTotal > 1) {
        return `${fill(t('noteProgress.shortPart'), {index: count(progress.part_index), total: partsTotal})} ${t('noteProgress.shortChecking')}`;
    }
    if (keyed[stage]) return t(keyed[stage]);
    if (chapters.length) {
        return fill(t('noteProgress.shortChapters'), {done: chapters.filter((c) => c.done).length, total: chapters.length});
    }
    if (partsTotal > 1) return fill(t('noteProgress.shortPart'), {index: count(progress.part_index), total: partsTotal});
    if (count(progress.written_chars) > 0) return fill(t('noteProgress.shortDraft'), {chars: fmtCharCount(progress.written_chars, lang)});
    return '';
};

// The draft as blocks a read-only preview can show without a Markdown
// library: headings, list items and paragraphs. Pictures and HTML comments
// (the joined parts' stretch markers) are left out; bold marks are dropped.
export const draftBlocks = (markdown) => {
    const blocks = [];
    let paragraph = [];
    const flush = () => {
        if (paragraph.length) blocks.push({kind: 'p', text: paragraph.join(' ')});
        paragraph = [];
    };
    const clean = (text) => text.replace(/\*\*(.+?)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1').trim();
    String(markdown || '').split('\n').forEach((raw) => {
        const line = raw.trim().replace(/^>\s?/, '');
        if (!line || /^<!--.*-->$/.test(line) || /^!\[[^\]]*\]\([^)]*\)$/.test(line)) {
            flush();
            return;
        }
        const heading = line.match(/^(#{1,6})\s+(.*)$/);
        if (heading) {
            flush();
            blocks.push({kind: `h${Math.min(3, heading[1].length)}`, text: clean(heading[2])});
            return;
        }
        const item = line.match(/^(?:[-*+]|\d+[.)])\s+(.*)$/);
        if (item) {
            flush();
            blocks.push({kind: 'li', text: clean(item[1])});
            return;
        }
        paragraph.push(clean(line));
    });
    flush();
    return blocks.filter((block) => block.text);
};
