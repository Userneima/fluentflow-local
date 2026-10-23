// @vitest-environment jsdom

// The note editor edits the note's markdown source in place (decision 5.34).
// Written from the requirements, not from the code:
//   - The markdown goes in and comes out unchanged when nothing is edited:
//     question and tip callouts (also one indented under a list item), tables,
//     links to a heading ([[#标题|第五章]]), screenshots with #crop=, comments.
//     The old editor turned every line of a callout into its own quote.
//   - Editing one table cell in place changes that cell's text and nothing
//     else in the note.
//   - The toolbar changes only the characters it is about; in a quote or a
//     callout its marks go after the quote marks. It is off while a table
//     cell is being edited.
//   - A newer note handed down by the page (Obsidian, Feishu, a poll) replaces
//     the text and is not reported back as an edit, and undo cannot step back
//     into the text it replaced; the page's echo of what was just typed does
//     not undo newer typing. While a table cell is open, a newer note waits
//     until the cell is left, and the page is told a cell is open.
//   - A note with Windows line ends keeps them when edited.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render} from '@testing-library/react';
import {EditorView} from '@codemirror/view';
import {undo} from '@codemirror/commands';
import NoteEditor from './NoteEditor.jsx';

// jsdom has no layout; CodeMirror asks for rectangles when it scrolls.
const noRects = () => ({length: 0, item: () => null, [Symbol.iterator]: function* () {}});
const emptyRect = () => ({x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({})});
Range.prototype.getClientRects = noRects;
Range.prototype.getBoundingClientRect = emptyRect;
Element.prototype.getClientRects = noRects;

export const NOTE = [
    '---',
    'title: 对罗福莉的访谈',
    'tags: [访谈]',
    '---',
    '# 对罗福莉的访谈',
    '',
    '<!-- 第 1/3 部分（00:00–73:27） -->',
    '',
    '## 一、开场片段',
    '',
    '正文里有 **加粗** 和 *斜体*，跳到 [[#二、算力配比|第二章]]。',
    '',
    '> [!question] 为什么是 3:1:1',
    '> 她没有展开。',
    '>',
    '> 空行之后还在框里。  ',
    '',
    '> [!tip]- 折叠的提示框',
    '> 默认收起。',
    '',
    '1. 第一步',
    '   1. 第一步里的小步',
    '\t- 制表符缩进的一项',
    '2. 第二步',
    '',
    '- 列表第一项',
    '    > [!tip] 列表下缩进的提示框',
    '    > 缩进要保留。',
    '- 列表第二项',
    '',
    '| 阶段 | 占比 |',
    '| --- | :---: |',
    '| 研究 | 3 |',
    '| 预训练 \\| 后训练 | 1 |',
    '',
    '> | 框里的表 | 值 |',
    '> | --- | --- |',
    '> | 甲 | 1 |',
    '',
    '![关键画面](/jobs/t1/artifacts/frame?file=note_0001.jpg#crop=0.1,0.2,0.5,0.4)',
    '',
    '## 二、算力配比',
    '',
    '结尾。',
].join('\n');

const viewOf = (container) => EditorView.findFromDOM(container.querySelector('.cm-editor'));

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

describe('the markdown in and out', () => {
    it('keeps the note exactly as written when nothing is edited', () => {
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        expect(viewOf(container).state.doc.toString()).toBe(NOTE);
        expect(onChange).not.toHaveBeenCalled();
    });

    it('reports exactly what the user typed, and nothing else changes', () => {
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        const view = viewOf(container);
        const at = NOTE.indexOf('结尾。') + '结尾'.length;
        act(() => { view.dispatch({changes: {from: at, insert: '了'}, userEvent: 'input.type'}); });
        expect(onChange).toHaveBeenCalledTimes(1);
        expect(onChange.mock.calls[0][0]).toBe(NOTE.replace('结尾。', '结尾了。'));
    });
});

describe('a table cell edited in place', () => {
    const editCell = (container, match, text) => {
        const cell = [...container.querySelectorAll('.cm-ob-table-wrap td, .cm-ob-table-wrap th')].find((node) => node.textContent === match);
        expect(cell, match).toBeTruthy();
        act(() => { fireEvent.focus(cell); });
        cell.textContent = text;
        act(() => { fireEvent.blur(cell); });
    };

    it('changes that cell and nothing else', () => {
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        editCell(container, '3', '4');
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('| 研究 | 3 |', '| 研究 | 4 |'));
    });

    it('keeps an escaped pipe and escapes a typed one', () => {
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        editCell(container, '研究', '研究|探索');
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('| 研究 | 3 |', '| 研究\\|探索 | 3 |'));
        expect(onChange.mock.calls.at(-1)[0]).toContain('| 预训练 \\| 后训练 | 1 |');
    });

    it('edits a table inside a callout without touching the quote marks', () => {
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        editCell(container, '甲', '乙');
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('> | 甲 | 1 |', '> | 乙 | 1 |'));
    });

    it('changes nothing when the cell is left as it was', () => {
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        editCell(container, '3', '3');
        expect(onChange).not.toHaveBeenCalled();
    });
});

describe('the toolbar', () => {
    const at = (container, text) => {
        const view = viewOf(container);
        const pos = view.state.doc.toString().indexOf(text);
        act(() => { view.dispatch({selection: {anchor: pos, head: pos + text.length}}); });
        return view;
    };

    it('makes a line a heading by adding its marks only', () => {
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        at(container, '结尾。');
        fireEvent.change(container.querySelector('#note-line-style'), {target: {value: 'h3'}});
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('\n结尾。', '\n### 结尾。'));
    });

    it('bolds the selection, and the same button takes it off', () => {
        const onChange = vi.fn();
        const {container, getByRole} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        at(container, '结尾');
        fireEvent.click(getByRole('button', {name: '加粗'}));
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('结尾。', '**结尾**。'));
        fireEvent.click(getByRole('button', {name: '加粗'}));
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE);
    });

    it('puts list and heading marks after the quote marks of a callout', () => {
        const onChange = vi.fn();
        const {container, getByRole} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        at(container, '她没有展开。');
        fireEvent.click(getByRole('button', {name: '项目列表'}));
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('> 她没有展开。', '> - 她没有展开。'));
        fireEvent.click(getByRole('button', {name: '项目列表'}));
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE);
        fireEvent.change(container.querySelector('#note-line-style'), {target: {value: 'h3'}});
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('> 她没有展开。', '> ### 她没有展开。'));
    });

    it('leaves a callout title line and a table row alone', () => {
        const onChange = vi.fn();
        const {container, getByRole} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        at(container, '[!question] 为什么');
        fireEvent.click(getByRole('button', {name: '编号列表'}));
        fireEvent.change(container.querySelector('#note-line-style'), {target: {value: 'h2'}});
        at(container, '| 研究 | 3 |');
        fireEvent.click(getByRole('button', {name: '项目列表'}));
        expect(onChange).not.toHaveBeenCalled();
    });

    it('is off while a table cell is being edited', () => {
        const onChange = vi.fn();
        const {container, getByRole} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        at(container, '结尾');
        const cell = [...container.querySelectorAll('.cm-ob-table-wrap td')].find((node) => node.textContent === '研究');
        act(() => { fireEvent.focus(cell); });
        for (const name of ['加粗', '斜体', '项目列表', '编号列表', '撤销', '重做']) {
            expect(getByRole('button', {name}).disabled, name).toBe(true);
        }
        expect(container.querySelector('#note-line-style').disabled).toBe(true);
        act(() => { fireEvent.blur(cell); });
        expect(getByRole('button', {name: '加粗'}).disabled).toBe(false);
        expect(onChange).not.toHaveBeenCalled();
    });

    it('turns lines into a list and back', () => {
        const onChange = vi.fn();
        const {container, getByRole} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        at(container, '结尾。');
        fireEvent.click(getByRole('button', {name: '编号列表'}));
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('\n结尾。', '\n1. 结尾。'));
        fireEvent.click(getByRole('button', {name: '编号列表'}));
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE);
    });
});

describe('a newer note from the page', () => {
    it('replaces the text and is not reported as an edit', () => {
        const onChange = vi.fn();
        const {container, rerender} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        const newer = `${NOTE}\n\n在 Obsidian 里加的一段。`;
        rerender(<NoteEditor markdown={newer} onChange={onChange}/>);
        expect(viewOf(container).state.doc.toString()).toBe(newer);
        expect(onChange).not.toHaveBeenCalled();
    });

    it('cannot be undone back to the text it replaced', () => {
        const onChange = vi.fn();
        const {container, rerender, getByRole} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        const view = viewOf(container);
        act(() => { view.dispatch({changes: {from: NOTE.length, insert: '甲'}, userEvent: 'input.type'}); });
        onChange.mockClear();
        const newer = `${NOTE}\n\n在 Obsidian 里加的一段。`;
        rerender(<NoteEditor markdown={newer} onChange={onChange}/>);
        expect(getByRole('button', {name: '撤销'}).disabled).toBe(true);
        act(() => { undo(viewOf(container)); });
        expect(viewOf(container).state.doc.toString()).toBe(newer);
        expect(onChange).not.toHaveBeenCalled();
    });

    it('waits while a table cell is open, and the page knows a cell is open', () => {
        const onChange = vi.fn();
        const onEditingChange = vi.fn();
        const {container, rerender} = render(<NoteEditor markdown={NOTE} onChange={onChange} onEditingChange={onEditingChange}/>);
        const cell = [...container.querySelectorAll('.cm-ob-table-wrap td')].find((node) => node.textContent === '研究');
        act(() => { fireEvent.focus(cell); });
        expect(onEditingChange).toHaveBeenLastCalledWith(true);
        const newer = `${NOTE}\n\n在 Obsidian 里加的一段。`;
        rerender(<NoteEditor markdown={newer} onChange={onChange} onEditingChange={onEditingChange}/>);
        expect(viewOf(container).state.doc.toString()).toBe(NOTE);
        act(() => { fireEvent.blur(cell); });
        expect(onEditingChange).toHaveBeenLastCalledWith(false);
        // Nothing typed in the cell: the newer note comes in, not as an edit.
        expect(viewOf(container).state.doc.toString()).toBe(newer);
        expect(onChange).not.toHaveBeenCalled();
    });

    it('keeps what was typed in a cell when the note is updated meanwhile', () => {
        const onChange = vi.fn();
        const {container, rerender} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        const cell = [...container.querySelectorAll('.cm-ob-table-wrap td')].find((node) => node.textContent === '研究');
        act(() => { fireEvent.focus(cell); });
        rerender(<NoteEditor markdown={`${NOTE}\n\n新的一段。`} onChange={onChange}/>);
        cell.textContent = '探索';
        act(() => { fireEvent.blur(cell); });
        // The typed cell is reported; the page's save names the version it was
        // typed on, and a refused save brings the newer note back with a notice.
        expect(onChange.mock.calls.at(-1)[0]).toBe(NOTE.replace('| 研究 | 3 |', '| 探索 | 3 |'));
    });

    it('does not let the echo of what was typed undo newer typing', () => {
        const onChange = vi.fn();
        const {container, rerender} = render(<NoteEditor markdown={NOTE} onChange={onChange}/>);
        const view = viewOf(container);
        act(() => { view.dispatch({changes: {from: NOTE.length, insert: 'a'}, userEvent: 'input.type'}); });
        act(() => { view.dispatch({changes: {from: NOTE.length + 1, insert: 'b'}, userEvent: 'input.type'}); });
        // The page re-renders with the first report only.
        rerender(<NoteEditor markdown={`${NOTE}a`} onChange={onChange}/>);
        expect(view.state.doc.toString()).toBe(`${NOTE}ab`);
    });
});

describe('line ends', () => {
    it('keeps Windows line ends when a note that has them is edited', () => {
        const crlf = NOTE.replace(/\n/g, '\r\n');
        const onChange = vi.fn();
        const {container} = render(<NoteEditor markdown={crlf} onChange={onChange}/>);
        const view = viewOf(container);
        // The editor's text, read with the note's own line ends.
        expect(view.state.sliceDoc()).toBe(crlf);
        const at = crlf.indexOf('结尾。') + '结尾'.length;
        act(() => { view.dispatch({changes: {from: view.state.doc.toString().indexOf('结尾。') + '结尾'.length, insert: '了'}, userEvent: 'input.type'}); });
        expect(onChange.mock.calls.at(-1)[0]).toBe(crlf.slice(0, at) + '了' + crlf.slice(at));
    });
});
