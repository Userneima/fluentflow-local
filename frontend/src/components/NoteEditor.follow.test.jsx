// @vitest-environment jsdom

// The note following playback (decision 6.37). Written from the requirements:
//   - Each heading with a row shows its time; clicking it asks the page to
//     play from where the section is talked about most, naming the section.
//   - The section playing is marked, heading and body, up to the next heading
//     of its level or higher; while following, its heading is brought into view.
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
import {bodyHash, matchRows, normalizeRows, noteSections, sectionAt} from '../lib/noteFollow.js';

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
    {id: 'r9', heading: '小结', level: 2, body_hash: bodyOf('小结'), primary: [1939, 1972], others: []},
]);

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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

    it('picks the deepest heading, counting the other places a section draws on', () => {
        expect(sectionAt(170, ROWS)).toBe('r11');
        expect(sectionAt(300, ROWS)).toBe('r1');
        expect(sectionAt(20, ROWS)).toBe('r1');
        expect(sectionAt(600, ROWS)).toBe('r2');
        expect(sectionAt(700, ROWS)).toBe(null);
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
        const scroll = vi.spyOn(EditorView, 'scrollIntoView');
        const {rerender} = renderNote({following: false});
        rerender({following: false, activeKey: 'r2'});
        expect(scroll).not.toHaveBeenCalled();
        rerender({following: true, activeKey: 'r2'});
        expect(scroll).toHaveBeenCalledTimes(1);
        rerender({following: true, activeKey: 'r9'});
        expect(scroll).toHaveBeenCalledTimes(2);
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
});
