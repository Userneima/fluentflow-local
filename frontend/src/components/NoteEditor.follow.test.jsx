// @vitest-environment jsdom

// The note following playback (decision 6.37). Written from the requirements:
//   - Each heading the service matched shows its time; clicking it asks the
//     page to play from where the section is talked about most.
//   - The section playing is marked, heading and body, up to the next heading
//     of its level or higher.
//   - Neither the times nor the marking changes the markdown or reports an
//     edit, so they can never be saved (decisions 6.27, 6.30); a newer note
//     handed down by the page keeps them.
//   - Scrolling or typing in the note tells the page to stop following.
//   - The section playing is the deepest heading whose main span, or one of
//     its other spans, holds the time; the keys match the service's.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render} from '@testing-library/react';
import NoteEditor from './NoteEditor.jsx';
import {noteSections, sectionAt, sectionKey, timesByKey} from '../lib/noteFollow.js';

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
].join('\n');

const KEY_ONE = sectionKey(2, null, '一、做得好的');
const KEY_ONE_ONE = sectionKey(3, '一、做得好的', '（一）发版节奏');
const KEY_TWO = sectionKey(2, null, '二、计划方式');

const ROWS = [
    {key: KEY_ONE, level: 2, primary: [161, 571], others: [[15, 31]]},
    {key: KEY_ONE_ONE, level: 3, primary: [161, 196], others: []},
    {key: KEY_TWO, level: 2, primary: [826, 950], others: [[571, 652]]},
];

afterEach(cleanup);

const renderNote = (props = {}) => {
    const onChange = vi.fn();
    const follow = {times: timesByKey(ROWS), activeKey: null, following: true, revealToken: 0, onSeek: vi.fn(), onUserScroll: vi.fn(), ...props};
    const view = render(<NoteEditor markdown={NOTE} onChange={onChange} follow={follow}/>);
    const rerender = (next) => view.rerender(<NoteEditor markdown={next.markdown || NOTE} onChange={onChange} follow={{...follow, ...next}}/>);
    return {...view, onChange, follow, rerender};
};

const lineOf = (container, text) => [...container.querySelectorAll('.cm-line')].find((line) => line.textContent.includes(text));

describe('note sections and the section playing', () => {
    it('reads the level-2 and level-3 headings with the keys the service uses', () => {
        expect(noteSections(NOTE).map((s) => [s.key, s.line])).toEqual([[KEY_ONE, 5], [KEY_ONE_ONE, 7], [KEY_TWO, 15]]);
        // The service names a heading [level, parent or "", text, occurrence].
        // The same text tests/test_note_section_times.py pins for the service.
        expect(KEY_ONE_ONE).toBe('[3,"一、做得好的","（一）发版节奏",1]');
    });

    it('picks the deepest heading, counting the other places a section draws on', () => {
        const times = timesByKey(ROWS);
        expect(sectionAt(170, times)).toBe(KEY_ONE_ONE);
        expect(sectionAt(300, times)).toBe(KEY_ONE);
        expect(sectionAt(20, times)).toBe(KEY_ONE);
        expect(sectionAt(600, times)).toBe(KEY_TWO);
        expect(sectionAt(900, times)).toBe(KEY_TWO);
        expect(sectionAt(700, times)).toBe(null);
    });
});

describe('the note editor following playback', () => {
    it('shows a time beside each matched heading and plays from it on click, without editing the note', () => {
        const {container, onChange, follow} = renderNote();
        const times = [...container.querySelectorAll('.cm-follow-time')].map((node) => node.textContent);
        expect(times).toEqual(['02:41', '02:41', '13:46']);
        fireEvent.mouseDown(container.querySelectorAll('.cm-follow-time')[2]);
        fireEvent.click(container.querySelectorAll('.cm-follow-time')[2]);
        expect(follow.onSeek).toHaveBeenCalledWith(826);
        expect(onChange).not.toHaveBeenCalled();
    });

    it('marks the section playing from its heading to the next heading of its level', () => {
        const {container, rerender, onChange} = renderNote();
        rerender({activeKey: KEY_ONE});
        expect(lineOf(container, '一、做得好的').className).toContain('cm-follow-active-heading');
        expect(lineOf(container, '稳定。').className).toContain('cm-follow-active');
        expect(lineOf(container, '二、计划方式').className).not.toContain('cm-follow-active');

        rerender({activeKey: KEY_TWO});
        expect(lineOf(container, '稳定。').className).not.toContain('cm-follow-active');
        expect(lineOf(container, '两周一个').className).toContain('cm-follow-active');
        expect(onChange).not.toHaveBeenCalled();
    });

    it('keeps the times and the marking when the page hands down a newer note', () => {
        const {container, rerender, onChange} = renderNote({activeKey: KEY_TWO});
        rerender({activeKey: KEY_TWO, markdown: `${NOTE}\n\n补一句。`});
        expect(container.querySelector('.cm-content').textContent).toContain('补一句。');
        expect(container.querySelectorAll('.cm-follow-time')).toHaveLength(3);
        expect(lineOf(container, '二、计划方式').className).toContain('cm-follow-active-heading');
        expect(onChange).not.toHaveBeenCalled();
    });

    it('tells the page the reader scrolled or typed in the note', () => {
        const {container, follow} = renderNote();
        act(() => { fireEvent.wheel(container.querySelector('.cm-scroller')); });
        expect(follow.onUserScroll).toHaveBeenCalledTimes(1);
        act(() => { fireEvent.keyDown(container.querySelector('.cm-content'), {key: 'a'}); });
        expect(follow.onUserScroll).toHaveBeenCalledTimes(2);
    });

    it('adds nothing when the page does not follow playback', () => {
        const {container} = render(<NoteEditor markdown={NOTE} onChange={vi.fn()}/>);
        expect(container.querySelectorAll('.cm-follow-time')).toHaveLength(0);
    });
});
