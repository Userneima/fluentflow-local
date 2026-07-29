// @vitest-environment jsdom

import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterAll, afterEach, beforeAll, describe, expect, it, vi} from 'vitest';
import RichNoteEditor from './RichNoteEditor.jsx';

afterEach(cleanup);

const originalRangeRects = window.Range.prototype.getClientRects;
const originalRangeRect = window.Range.prototype.getBoundingClientRect;

beforeAll(() => {
    window.Range.prototype.getClientRects = () => [];
    window.Range.prototype.getBoundingClientRect = () => ({
        bottom: 0, height: 0, left: 0, right: 0, top: 0, width: 0, x: 0, y: 0,
        toJSON: () => ({}),
    });
});

afterAll(() => {
    window.Range.prototype.getClientRects = originalRangeRects;
    window.Range.prototype.getBoundingClientRect = originalRangeRect;
});

describe('RichNoteEditor', () => {
    it('opens an existing markdown note as formatted editable content', () => {
        render(
            <RichNoteEditor
                lang="zh"
                markdown={'# 课程笔记\n\n这是正文。'}
                onChange={vi.fn()}
            />
        );

        const editor = screen.getByRole('textbox', {name: '编辑笔记正文'});
        expect(editor.querySelector('h2')?.textContent).toBe('课程笔记');
        expect(editor.textContent).toContain('这是正文。');
        expect(editor.textContent).not.toContain('# 课程笔记');
        expect(Array.from(editor.children).map((node) => node.tagName)).toEqual(['H2', 'P']);
    });

    it('changes the current block style and emits markdown through the public callback', async () => {
        const onChange = vi.fn();
        render(<RichNoteEditor lang="zh" markdown="正文内容" onChange={onChange}/>);

        fireEvent.change(screen.getByLabelText('文本样式'), {target: {value: 'h3'}});

        await waitFor(() => {
            expect(onChange).toHaveBeenLastCalledWith('## 正文内容');
        });
    });

    it('shows the active inline format at the current cursor position', () => {
        render(<RichNoteEditor lang="zh" markdown="**重点内容**" onChange={vi.fn()}/>);

        expect(screen.getByRole('button', {name: '加粗'}).getAttribute('aria-pressed')).toBe('true');
    });

    it('reserves space for the native dropdown icon beside the longest block label', () => {
        render(<RichNoteEditor lang="zh" markdown="### 三级标题" onChange={vi.fn()}/>);

        const styleSelect = screen.getByLabelText('文本样式');
        expect(styleSelect.className).toContain('w-[7.25rem]');
        expect(styleSelect.className).toContain('pr-7');
    });

    it('keeps supported formatting while dropping pasted presentation styles', async () => {
        const onChange = vi.fn();
        render(<RichNoteEditor lang="zh" markdown="" onChange={onChange}/>);
        const editor = screen.getByRole('textbox', {name: '编辑笔记正文'});
        fireEvent.focus(editor);

        fireEvent.paste(editor, {
            clipboardData: {
                files: [],
                getData: (type) => {
                    if (type === 'text/html') return '<p style="color:red;font-size:40px"><strong>粘贴重点</strong></p>';
                    if (type === 'text/plain') return '粘贴重点';
                    return '';
                },
                types: ['text/html', 'text/plain'],
            },
        });

        await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('**粘贴重点**'));
        expect(editor.querySelector('[style]')).toBeNull();
    });

    it('uses the latest save callback after the surrounding task state changes', async () => {
        const firstOnChange = vi.fn();
        const latestOnChange = vi.fn();
        const view = render(<RichNoteEditor lang="zh" markdown="正文内容" onChange={firstOnChange}/>);
        view.rerender(<RichNoteEditor lang="zh" markdown="正文内容" onChange={latestOnChange}/>);

        fireEvent.change(screen.getByLabelText('文本样式'), {target: {value: 'h3'}});

        await waitFor(() => expect(latestOnChange).toHaveBeenLastCalledWith('## 正文内容'));
        expect(firstOnChange).not.toHaveBeenCalled();
    });
});
