// @vitest-environment jsdom

// The note following playback (decision 6.37). Written from the requirements:
//   - Each heading with a row shows its time; clicking it asks the page to
//     play from where the section is talked about most, naming the section.
//   - The section playing is marked, heading and body, up to the next heading
//     of its level or higher; while following, its heading is brought into view,
//     and a section longer than the view is scrolled through as the playback
//     moves through its span, so the part being talked about stays in view
//     (decision 6.66).
//   - Only the main span (where a section is talked about most) is followed.
//     A section that draws on the moment through another span, like the recap
//     at the end of a meeting, is marked lightly and never scrolled to; its
//     parent heading is not marked (owner, 2026-10-25).
//   - (owner, 2026-11-14) A level-3 section talked about in two stretches is
//     followed in both, when the other stretch falls inside a section of the
//     same chapter; a section of another chapter still is not.
//   - (owner, 2026-11-14) The fact checks added after the lecture ("> **核对（…）**")
//     were not said: the reading point is placed by progress over the
//     spoken lines only, never inside a check; the checks stay shown.
//   - Neither the times nor the marking changes the markdown or reports an
//     edit, so they can never be saved (decisions 6.27, 6.30); a newer note
//     handed down by the page keeps them.
//   - Scrolling, typing or pressing in the note (scrollbar, selection) tells
//     the page to stop following.
//   - A row finds its section by heading text, then by the hash of the
//     section's own body, the service's rule: a renamed chapter loses only
//     its own row, and a new heading with an old heading's text does not take
//     its time.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render} from '@testing-library/react';
import {EditorView} from '@codemirror/view';
import NoteEditor from './NoteEditor.jsx';
import {alsoAt, bodyHash, matchRows, normalizeRows, noteSections, sectionAt, spanProgress} from '../lib/noteFollow.js';
import {followScrollTop, spokenPoint, unspokenLines} from '../lib/noteFollowEditor.js';

const noRects = () => ({length: 0, item: () => null, [Symbol.iterator]: function* () {}});
const emptyRect = () => ({x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({})});
Range.prototype.getClientRects = noRects;
Range.prototype.getBoundingClientRect = emptyRect;
Element.prototype.getClientRects = noRects;

const NOTE = [
    '# 复盘会',
    '',
    '<!-- 第 1/2 部分（00:00–10:00） -->',
    '',
    '## 一、做得好的',
    '',
    '### （一）发版节奏',
    '',
    '稳定。',
    '',
    '```',
    '## 代码块里的不是标题',
    '```',
    '',
    '## 二、计划方式',
    '',
    '两周一个 Sprint。',
    '',
    '## 小结',
    '',
    '第一处小结。',
].join('\n');

const bodyOf = (heading) => noteSections(NOTE).find((s) => s.heading === heading).bodyHash;

const ROWS = normalizeRows([
    {id: 'r1', heading: '一、做得好的', level: 2, body_hash: bodyOf('一、做得好的'), primary: [161, 571], others: [[15, 31]]},
    {id: 'r11', heading: '（一）发版节奏', level: 3, body_hash: bodyOf('（一）发版节奏'), primary: [161, 196], others: []},
    {id: 'r2', heading: '二、计划方式', level: 2, body_hash: bodyOf('二、计划方式'), primary: [826, 950], others: [[571, 652]]},
    {id: 'r9', heading: '小结', level: 2, body_hash: bodyOf('小结'), primary: [1939, 1972], others: [[619, 652]]},
]);

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

// jsdom lays nothing out; give the note's scroller a height so following
// has a view to keep the section in, and record where it is sent.
const followScroller = () => {
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function () { return this.classList?.contains('cm-scroller') ? 400 : 0; });
    const sent = [];
    // jsdom keeps no scroll position and lays nothing out: the note is put
    // far below the view, so any section playing is out of it.
    vi.spyOn(EditorView.prototype, 'documentTop', 'get').mockReturnValue(5000);
    const place = new WeakMap();
    vi.spyOn(HTMLElement.prototype, 'scrollTop', 'get').mockImplementation(function () { return place.get(this) || 0; });
    vi.spyOn(HTMLElement.prototype, 'scrollTop', 'set').mockImplementation(function (value) { place.set(this, value); });
    HTMLElement.prototype.scrollTo = function (options) { sent.push(options); place.set(this, options.top); };
    return sent;
};

const renderNote = (props = {}) => {
    const onChange = vi.fn();
    const follow = {rows: ROWS, activeKey: null, following: true, revealToken: 0, onSeek: vi.fn(), onUserScroll: vi.fn(), ...props};
    const view = render(<NoteEditor markdown={NOTE} onChange={onChange} follow={follow}/>);
    const rerender = (next) => view.rerender(<NoteEditor markdown={next.markdown || NOTE} onChange={onChange} follow={{...follow, ...next}}/>);
    return {...view, onChange, follow, rerender};
};

const lineOf = (container, text) => [...container.querySelectorAll('.cm-line')].find((line) => line.textContent.includes(text));
const headingIds = (markdown) => {
    const sections = noteSections(markdown);
    const matched = matchRows(ROWS, sections);
    return sections.map((s) => [s.heading, matched.get(s.index)?.id || null]);
};

describe('finding each row its section', () => {
    it('hashes a section body the way the service does', () => {
        // Pinned in tests/test_note_section_times.py as well.
        expect(bodyHash('')).toBe('811c9dc5');
        expect(bodyHash('稳定。')).toBe('84537921');
        expect(noteSections(NOTE).map((s) => [s.level, s.heading, s.line])).toEqual([
            [2, '一、做得好的', 5], [3, '（一）发版节奏', 7], [2, '二、计划方式', 15], [2, '小结', 19],
        ]);
    });

    it('loses only the renamed chapter, never its sub-sections', () => {
        expect(headingIds(NOTE.replace('## 一、做得好的', '## 一、上季度做得好的'))).toEqual([
            ['一、上季度做得好的', null], ['（一）发版节奏', 'r11'], ['二、计划方式', 'r2'], ['小结', 'r9'],
        ]);
    });

    it('does not give an old heading\'s time to a new heading with the same text', () => {
        const inserted = NOTE.replace('## 二、计划方式', '## 小结\n\n新插的小结。\n\n## 二、计划方式');
        expect(headingIds(inserted).filter(([heading]) => heading === '小结')).toEqual([['小结', null], ['小结', 'r9']]);
    });

    it('gives no time to any of several same-name empty sections once one is inserted', () => {
        const note = ['## 讨论', '', '## 讨论', '', '## 结论', '', '定了。'].join('\n');
        const rows = normalizeRows(noteSections(note).map((section, i) => ({
            id: `d${i}`, heading: section.heading, level: 2, body_hash: section.bodyHash, primary: [i * 100, i * 100 + 50], others: [],
        })));
        const inserted = `## 讨论\n\n${note}`;
        const sections = noteSections(inserted);
        const matched = matchRows(rows, sections);
        expect(sections.map((s) => matched.get(s.index)?.id || null)).toEqual([null, null, null, 'd2']);
    });

    it('keeps a section matched when only its body changed', () => {
        expect(headingIds(NOTE.replace('两周一个 Sprint。', '两周一个 Sprint，改过。'))[2]).toEqual(['二、计划方式', 'r2']);
    });

    it('follows the deepest heading whose main span holds the moment', () => {
        expect(sectionAt(170, ROWS)).toBe('r11');
        expect(sectionAt(300, ROWS)).toBe('r1');
        expect(sectionAt(900, ROWS)).toBe('r2');
        expect(sectionAt(700, ROWS)).toBe(null);
    });

    it('never follows a section through one of its other spans, and lists those to mark lightly', () => {
        // 10:30 of the meeting: chapter two and the recap both draw on it
        // through another span; the note used to jump to the recap.
        expect(sectionAt(630, ROWS)).toBe(null);
        expect(sectionAt(20, ROWS)).toBe(null);
        expect(alsoAt(630, ROWS, 'r1').sort()).toEqual(['r2', 'r9']);
        expect(alsoAt(170, ROWS, 'r11')).toEqual(['r1']);
    });
});

describe('scrolling through a long section', () => {
    const long = {sectionTop: 1000, sectionBottom: 3000, height: 800};

    it('tells how far into the span holding the moment the playback is', () => {
        expect(spanProgress(ROWS[0], 161)).toBe(0);
        expect(spanProgress(ROWS[0], 366)).toBeCloseTo(0.5);
        expect(spanProgress(ROWS[0], 23)).toBeCloseTo(0.5);
        expect(spanProgress(ROWS[0], 700)).toBe(null);
    });

    it('shows a long section from its heading when it starts', () => {
        expect(followScrollTop({...long, top: 0, progress: 0})).toBe(968);
        expect(followScrollTop({...long, top: 968, progress: 0.1})).toBe(null);
        // Its heading half way down the view: left there while playing on,
        // brought up when the section has just started playing.
        expect(followScrollTop({...long, top: 600, progress: 0.02})).toBe(null);
        expect(followScrollTop({...long, top: 600, progress: 0.02, entering: true})).toBe(968);
    });

    it('scrolls on once the part talked about leaves the upper two thirds of the view', () => {
        // Half way through: 2000 is the place talked about, out of view at 968.
        expect(followScrollTop({...long, top: 968, progress: 0.5})).toBe(1800);
        // Then it stays while the place moves down the view.
        expect(followScrollTop({...long, top: 1800, progress: 0.6})).toBe(null);
        expect(followScrollTop({...long, top: 1800, progress: 0.9})).toBe(2600);
    });

    it('leaves a short section alone while it is in view, and brings it in otherwise', () => {
        const short = {sectionTop: 1000, sectionBottom: 1300, height: 800};
        expect(followScrollTop({...short, top: 900, progress: 0.9})).toBe(null);
        expect(followScrollTop({...short, top: 0, progress: 0.9})).toBe(968);
    });
});

describe('the note editor following playback', () => {
    it('shows a time beside each heading with a row and plays from it on click, without editing the note', () => {
        const {container, onChange, follow} = renderNote();
        const times = [...container.querySelectorAll('.cm-follow-time')].map((node) => node.textContent);
        expect(times).toEqual(['02:41', '02:41', '13:46', '32:19']);
        fireEvent.mouseDown(container.querySelectorAll('.cm-follow-time')[2]);
        fireEvent.click(container.querySelectorAll('.cm-follow-time')[2]);
        expect(follow.onSeek).toHaveBeenCalledWith(826, 'r2');
        expect(onChange).not.toHaveBeenCalled();
    });

    it('marks the section playing from its heading to the next heading of its level', () => {
        const {container, rerender, onChange} = renderNote();
        rerender({activeKey: 'r1'});
        expect(lineOf(container, '一、做得好的').className).toContain('cm-follow-active-heading');
        expect(lineOf(container, '稳定。').className).toContain('cm-follow-active');
        expect(lineOf(container, '二、计划方式').className).not.toContain('cm-follow-active');

        rerender({activeKey: 'r2'});
        expect(lineOf(container, '稳定。').className).not.toContain('cm-follow-active');
        expect(lineOf(container, '两周一个').className).toContain('cm-follow-active');
        expect(onChange).not.toHaveBeenCalled();
    });

    it('brings the section playing into view while following, and not while paused', () => {
        const sent = followScroller();
        const {rerender} = renderNote({following: false});
        rerender({following: false, activeKey: 'r2'});
        expect(sent).toHaveLength(0);
        rerender({following: true, activeKey: 'r2'});
        expect(sent).toHaveLength(1);
        rerender({following: true, activeKey: 'r9'});
        expect(sent).toHaveLength(2);
    });

    it('marks the recap lightly without scrolling to it, and leaves the parent of the section playing unmarked', () => {
        const sent = followScroller();
        const {container, rerender, onChange} = renderNote({following: false});
        rerender({following: true, activeKey: 'r1', alsoKeys: ['r2', 'r9']});
        expect(sent).toHaveLength(1);
        expect(lineOf(container, '第一处小结').className).toContain('cm-follow-also');
        expect(lineOf(container, '第一处小结').className).not.toContain('cm-follow-active');
        expect(lineOf(container, '两周一个').className).toContain('cm-follow-also');
        expect(lineOf(container, '稳定。').className).toContain('cm-follow-active');

        rerender({following: true, activeKey: 'r1', alsoKeys: ['r9']});
        expect(sent).toHaveLength(1);

        rerender({following: true, activeKey: 'r11', alsoKeys: ['r1']});
        expect(lineOf(container, '一、做得好的').className).not.toContain('cm-follow-also');
        expect(lineOf(container, '稳定。').className).toContain('cm-follow-active');
        expect(onChange).not.toHaveBeenCalled();
    });

    it('keeps the times and the marking when the page hands down a newer note', () => {
        const {container, rerender, onChange} = renderNote({activeKey: 'r2'});
        rerender({activeKey: 'r2', markdown: NOTE.replace('第一处小结。', '第一处小结。补一句。')});
        expect(container.querySelector('.cm-content').textContent).toContain('补一句。');
        expect(container.querySelectorAll('.cm-follow-time')).toHaveLength(4);
        expect(lineOf(container, '二、计划方式').className).toContain('cm-follow-active-heading');
        expect(onChange).not.toHaveBeenCalled();
    });

    it('tells the page the reader scrolled, typed or pressed in the note', () => {
        const {container, follow} = renderNote();
        act(() => { fireEvent.wheel(container.querySelector('.cm-scroller')); });
        act(() => { fireEvent.keyDown(container.querySelector('.cm-content'), {key: 'a'}); });
        // Dragging the scrollbar or selecting with the mouse starts with a press.
        act(() => { fireEvent.pointerDown(container.querySelector('.cm-scroller')); });
        act(() => { fireEvent.pointerDown(lineOf(container, '稳定。')); });
        expect(follow.onUserScroll).toHaveBeenCalledTimes(4);
    });

    it('adds nothing when the page does not follow playback', () => {
        const {container} = render(<NoteEditor markdown={NOTE} onChange={vi.fn()}/>);
        expect(container.querySelectorAll('.cm-follow-time')).toHaveLength(0);
    });

describe('a subject talked about in two stretches', () => {
    // Lesson three: 二、Jessie > （二）提示缓存怎么运作 1692-2124, （三）张和讲缓存命中率
    // 2124-2337 and again 1865-1961; 四、小结 draws on 1865-1961 too.
    const rows = normalizeRows([
        {id: 'c2', heading: '二、Jessie', level: 2, body_hash: 'a', primary: [1535, 2446], others: []},
        {id: 'c2a', heading: '（一）做法', level: 3, body_hash: 'b', primary: [1535, 1692], others: [[1818, 1865]]},
        {id: 'c2b', heading: '（二）提示缓存怎么运作', level: 3, body_hash: 'c', primary: [1692, 2124], others: [[2352, 2431]]},
        {id: 'c2c', heading: '（三）张和讲缓存命中率', level: 3, body_hash: 'd', primary: [2124, 2337], others: [[1865, 1961]]},
        {id: 'c4', heading: '四、小结', level: 2, body_hash: 'e', primary: [4000, 4100], others: []},
        {id: 'c4a', heading: '（一）决定', level: 3, body_hash: 'f', primary: [4000, 4050], others: [[1900, 1950]]},
    ]);

    it('follows the second stretch inside its own chapter, and comes back after it', () => {
        expect(sectionAt(1800, rows)).toBe('c2b');
        expect(sectionAt(1830, rows)).toBe('c2a');
        expect(sectionAt(1866, rows)).toBe('c2c');
        expect(sectionAt(1920, rows)).toBe('c2c');
        expect(sectionAt(1962, rows)).toBe('c2b');
        expect(sectionAt(2200, rows)).toBe('c2c');
    });

    it('never follows a section of another chapter there', () => {
        expect(sectionAt(1920, rows)).not.toBe('c4a');
        expect(alsoAt(1920, rows, sectionAt(1920, rows))).toContain('c4a');
    });

    it('keeps chapters apart when a chapter heading is not found in the note', () => {
        const withoutC4 = rows.filter((row) => row.id !== 'c4');
        expect(sectionAt(1920, withoutC4, rows)).toBe('c2c');
        expect(sectionAt(1920, withoutC4)).toBe('c4a'); // what joining it to the chapter before did
    });

    it('scrolls through the second stretch by its own progress', () => {
        expect(spanProgress(rows.find((row) => row.id === 'c2c'), 1913)).toBeCloseTo(0.5, 2);
    });
});

describe('fact checks are not on the timeline', () => {
    const lines = [
        '- 第一段讲的话。',
        '',
        '  > **核对（2026-11-05）**：这是课后补的核对。',
        '  > 核对的第二行。',
        '',
        '- 第二段讲的话。',
    ];

    it('tells the check and the empty lines from the spoken ones', () => {
        expect(unspokenLines(lines)).toEqual([false, true, true, true, true, false]);
        expect(unspokenLines(['> 普通引用，是课上讲的。'])).toEqual([false]);
    });

    it('places the reading point over the spoken lines only, never inside a check', () => {
        // Each line 20px tall; the check is 300px tall in the middle.
        const tall = [20, 20, 150, 150, 20, 20];
        let y = 0;
        const unspoken = unspokenLines(lines);
        const blocks = tall.map((h, i) => { const block = {top: y, bottom: y + h, spoken: !unspoken[i]}; y += h; return block; });
        expect(spokenPoint(blocks, 0.25)).toBe(10);
        expect(spokenPoint(blocks, 0.75)).toBe(360 + 10);
        expect(spokenPoint(blocks, 0.5)).not.toBeGreaterThan(20);
        for (const share of [0, 0.1, 0.3, 0.5, 0.6, 0.9, 1]) {
            const at = spokenPoint(blocks, share);
            expect(at <= 20 || at >= 360).toBe(true);
        }
    });

    it('scrolls to the spoken point instead of the share of the whole section', () => {
        const want = followScrollTop({sectionTop: 0, sectionBottom: 4000, top: 0, height: 800, progress: 0.5, talkedAt: 1000});
        expect(want).toBe(800);
    });
});
});
