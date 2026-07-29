import {useEffect, useRef} from 'react';
import {EditorContent, useEditor, useEditorState} from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import {TableKit} from '@tiptap/extension-table';
import SvgIcon from './SvgIcon.jsx';
import {editableHtmlToMarkdown, markdownToStructuredEditorHtml} from '../lib/richNoteEditor.js';

const editorExtensions = [
    StarterKit.configure({
        heading: {levels: [2, 3, 4]},
        link: {openOnClick: false},
    }),
    Image.configure({allowBase64: false}),
    TableKit,
];

const editorContentClass = 'min-h-[520px] px-1 py-1 text-base font-medium leading-8 text-on-surface outline-none [&_a]:text-primary [&_blockquote]:border-l-4 [&_blockquote]:border-primary/30 [&_blockquote]:pl-4 [&_blockquote]:text-on-surface-variant [&_code]:rounded-[4px] [&_code]:bg-surface-container-high [&_code]:px-1 [&_h2]:mb-4 [&_h2]:mt-6 [&_h2]:font-headline [&_h2]:text-2xl [&_h2]:font-extrabold [&_h3]:mb-3 [&_h3]:mt-6 [&_h3]:font-headline [&_h3]:text-xl [&_h3]:font-extrabold [&_h4]:mb-2 [&_h4]:mt-5 [&_h4]:font-headline [&_h4]:text-lg [&_h4]:font-extrabold [&_hr]:my-6 [&_li]:my-1.5 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:my-3 [&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-[8px] [&_pre]:bg-surface-container-high [&_pre]:p-3 [&_strong]:font-extrabold [&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-outline-variant [&_td]:p-2 [&_th]:border [&_th]:border-outline-variant [&_th]:bg-surface-container-low [&_th]:p-2 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-6';

const editorAttributes = (lang) => ({
    'aria-label': lang === 'zh' ? '编辑笔记正文' : 'Edit note body',
    'aria-multiline': 'true',
    class: editorContentClass,
    role: 'textbox',
    spellcheck: 'false',
});

const blockStyleForEditor = (editor) => {
    if (!editor) return 'p';
    if (editor.isActive('heading', {level: 2})) return 'h2';
    if (editor.isActive('heading', {level: 3})) return 'h3';
    if (editor.isActive('heading', {level: 4})) return 'h4';
    if (editor.isActive('blockquote')) return 'blockquote';
    if (editor.isActive('codeBlock')) return 'pre';
    return 'p';
};

const ToolbarButton = ({active, disabled = false, icon, label, onClick}) => (
    <button
        type="button"
        title={label}
        aria-label={label}
        aria-pressed={typeof active === 'boolean' ? active : undefined}
        disabled={disabled}
        onMouseDown={(event) => event.preventDefault()}
        onClick={onClick}
        className={`inline-flex size-8 shrink-0 items-center justify-center rounded-[8px] transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-30 ${active
            ? 'bg-primary text-on-primary'
            : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface active:scale-[0.97]'}`}
    >
        <SvgIcon name={icon} className="text-[17px]"/>
    </button>
);

const RichNoteEditor = ({lang = 'zh', markdown = '', onChange}) => {
    const emittedMarkdownRef = useRef(null);
    const editor = useEditor({
        extensions: editorExtensions,
        content: markdownToStructuredEditorHtml(markdown),
        editorProps: {
            attributes: editorAttributes(lang),
        },
        onUpdate: ({editor: activeEditor}) => {
            const nextMarkdown = editableHtmlToMarkdown(activeEditor.getHTML());
            emittedMarkdownRef.current = nextMarkdown;
            onChange?.(nextMarkdown);
        },
    });
    const toolbarState = useEditorState({
        editor,
        selector: ({editor: activeEditor}) => ({
            blockStyle: blockStyleForEditor(activeEditor),
            bold: activeEditor?.isActive('bold') || false,
            italic: activeEditor?.isActive('italic') || false,
            bulletList: activeEditor?.isActive('bulletList') || false,
            orderedList: activeEditor?.isActive('orderedList') || false,
            canUndo: activeEditor?.can().chain().focus().undo().run() || false,
            canRedo: activeEditor?.can().chain().focus().redo().run() || false,
        }),
    });

    useEffect(() => {
        if (!editor || markdown === emittedMarkdownRef.current) return;
        const currentMarkdown = editableHtmlToMarkdown(editor.getHTML());
        if (currentMarkdown === markdown) return;
        editor.commands.setContent(markdownToStructuredEditorHtml(markdown), {emitUpdate: false});
    }, [editor, markdown]);

    useEffect(() => {
        editor?.setOptions({
            editorProps: {
                attributes: editorAttributes(lang),
            },
        });
    }, [editor, lang]);

    const applyBlockStyle = (value) => {
        if (!editor) return;
        const chain = editor.chain().focus();
        if (value === 'h2') chain.setHeading({level: 2}).run();
        else if (value === 'h3') chain.setHeading({level: 3}).run();
        else if (value === 'h4') chain.setHeading({level: 4}).run();
        else if (value === 'blockquote') chain.setBlockquote().run();
        else if (value === 'pre') chain.setCodeBlock().run();
        else chain.setParagraph().run();
    };

    const labels = lang === 'zh'
        ? {
            style: '文本样式', paragraph: '正文', heading1: '一级标题', heading2: '二级标题', heading3: '三级标题',
            quote: '引用', codeBlock: '代码块', bold: '加粗', italic: '斜体', bulletList: '项目列表',
            orderedList: '编号列表', divider: '分隔线', undo: '撤销', redo: '重做',
        }
        : {
            style: 'Text style', paragraph: 'Paragraph', heading1: 'Heading 1', heading2: 'Heading 2', heading3: 'Heading 3',
            quote: 'Quote', codeBlock: 'Code block', bold: 'Bold', italic: 'Italic', bulletList: 'Bullet list',
            orderedList: 'Numbered list', divider: 'Divider', undo: 'Undo', redo: 'Redo',
        };

    return (
        <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-outline-variant bg-surface-container-low px-4 py-2">
                <label className="sr-only" htmlFor="note-block-style">{labels.style}</label>
                <select
                    id="note-block-style"
                    value={toolbarState?.blockStyle || 'p'}
                    aria-label={labels.style}
                    onChange={(event) => applyBlockStyle(event.target.value)}
                    className="h-8 w-[7.25rem] rounded-[8px] border border-outline-variant bg-surface-container-lowest pl-2 pr-7 text-xs font-bold text-on-surface-variant outline-none transition focus:border-primary/60 focus:ring-2 focus:ring-primary/15"
                >
                    <option value="p">{labels.paragraph}</option>
                    <option value="h2">{labels.heading1}</option>
                    <option value="h3">{labels.heading2}</option>
                    <option value="h4">{labels.heading3}</option>
                    <option value="blockquote">{labels.quote}</option>
                    <option value="pre">{labels.codeBlock}</option>
                </select>
                <span className="h-5 w-px bg-outline-variant" aria-hidden="true"/>
                <ToolbarButton active={toolbarState?.bold} icon="format_bold" label={labels.bold} onClick={()=>editor?.chain().focus().toggleBold().run()}/>
                <ToolbarButton active={toolbarState?.italic} icon="format_italic" label={labels.italic} onClick={()=>editor?.chain().focus().toggleItalic().run()}/>
                <ToolbarButton active={toolbarState?.bulletList} icon="format_list_bulleted" label={labels.bulletList} onClick={()=>editor?.chain().focus().toggleBulletList().run()}/>
                <ToolbarButton active={toolbarState?.orderedList} icon="format_list_numbered" label={labels.orderedList} onClick={()=>editor?.chain().focus().toggleOrderedList().run()}/>
                <ToolbarButton icon="horizontal_rule" label={labels.divider} onClick={()=>editor?.chain().focus().setHorizontalRule().run()}/>
                <ToolbarButton disabled={!toolbarState?.canUndo} icon="undo" label={labels.undo} onClick={()=>editor?.chain().focus().undo().run()}/>
                <ToolbarButton disabled={!toolbarState?.canRedo} icon="redo" label={labels.redo} onClick={()=>editor?.chain().focus().redo().run()}/>
            </div>
            <div className="hide-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
                <EditorContent editor={editor}/>
            </div>
        </div>
    );
};

export default RichNoteEditor;
