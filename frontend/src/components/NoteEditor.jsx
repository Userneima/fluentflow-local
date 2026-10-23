import {useEffect, useRef, useState} from 'react';
import {Annotation, EditorState} from '@codemirror/state';
import {EditorView, keymap} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap, indentWithTab, redo, redoDepth, undo, undoDepth} from '@codemirror/commands';
import {markdown, markdownKeymap} from '@codemirror/lang-markdown';
import SvgIcon from './SvgIcon.jsx';
import {noteLivePreview} from '../lib/noteLivePreview.js';
import {lineStyleOf, setLineStyle, toggleInline, toggleList} from '../lib/noteEditorCommands.js';

// The note editor (decision 5.34): the note's markdown source edited in place,
// shown the way Obsidian's live preview shows it. Nothing is converted on the
// way in or out, so callouts, heading links, tables and screenshot crops keep
// their exact text.
//
// What it reports: every change the user makes, as the whole markdown
// (onChange). What it takes: a newer note handed down by the page (an Obsidian
// edit taken in, a Feishu pull, a poll) replaces the text and is not reported
// back, so it is never saved again as if the user had typed it.

// Marks a transaction that brings in the page's copy rather than a user edit.
const fromPage = Annotation.define();

const calloutColors = {
    question: '236, 117, 0',
    tip: '0, 160, 158',
    note: '8, 109, 221',
    todo: '8, 109, 221',
};

const calloutTheme = Object.fromEntries(Object.entries(calloutColors).flatMap(([type, rgb]) => [
    [`.cm-ob-callout-${type}`, {backgroundColor: `rgba(${rgb}, 0.1)`, '--ob-callout': `rgb(${rgb})`}],
]));

const editorTheme = EditorView.theme({
    '&': {height: '100%', color: 'var(--c-on-surface)', backgroundColor: 'transparent', fontSize: '16px'},
    '&.cm-focused': {outline: 'none'},
    '.cm-scroller': {
        fontFamily: 'inherit', lineHeight: '1.85', overflow: 'auto', padding: '20px 28px 160px',
    },
    '.cm-content': {maxWidth: '760px', margin: '0 auto', caretColor: 'var(--c-on-surface)', fontWeight: '500'},
    '.cm-line': {padding: '0 4px'},
    '.cm-cursor': {borderLeftColor: 'var(--c-on-surface)'},
    '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': {backgroundColor: 'rgba(59, 130, 246, 0.22)'},
    '.cm-ob-h1': {fontSize: '1.6em', fontWeight: '800', lineHeight: '1.35', padding: '0.4em 4px 0.3em'},
    '.cm-ob-h2': {fontSize: '1.4em', fontWeight: '800', lineHeight: '1.4', padding: '0.9em 4px 0.25em'},
    '.cm-ob-h3': {fontSize: '1.18em', fontWeight: '800', lineHeight: '1.45', padding: '0.7em 4px 0.2em'},
    '.cm-ob-h4, .cm-ob-h5, .cm-ob-h6': {fontWeight: '800'},
    '.cm-ob-delim': {color: 'var(--c-outline)', fontWeight: '500'},
    '.cm-ob-strong': {fontWeight: '800'},
    '.cm-ob-em': {fontStyle: 'italic'},
    '.cm-ob-code': {
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.88em',
        backgroundColor: 'var(--c-surface-container-high)', borderRadius: '4px', padding: '1px 4px',
    },
    '.cm-ob-codeblock': {
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.88em',
        backgroundColor: 'var(--c-surface-container-high)',
    },
    '.cm-ob-comment': {color: 'var(--c-outline)', fontSize: '0.85em'},
    '.cm-ob-source': {color: 'var(--c-outline)', fontSize: '0.9em'},
    '.cm-ob-marker': {display: 'inline-block', width: '1.6em', textIndent: '0', color: 'var(--c-on-surface-variant)'},
    '.cm-ob-marker-bullet': {textAlign: 'center', fontWeight: '800'},
    '.cm-ob-marker-ordered, .cm-ob-marker-src': {textAlign: 'left'},
    '.cm-ob-marker-src': {color: 'var(--c-outline)'},
    '.cm-ob-li': {},
    '.cm-ob-rule': {display: 'block', borderTop: '1px solid var(--c-outline-variant)', margin: '0.9em 0'},
    '.cm-ob-wikilink, .cm-ob-link': {
        color: '#3B82F6', cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'rgba(59,130,246,0.35)',
        textUnderlineOffset: '3px',
    },
    '.cm-ob-wikilink-src, .cm-ob-link-src': {color: '#3B82F6'},
    '.cm-ob-quote': {
        borderLeft: '3px solid var(--c-outline-variant)', paddingLeft: '14px !important',
        color: 'var(--c-on-surface-variant)',
    },
    '.cm-ob-callout': {
        borderLeft: '3px solid var(--ob-callout)', paddingLeft: '14px !important', paddingRight: '14px !important',
    },
    '.cm-ob-callout.cm-ob-block-first': {paddingTop: '8px !important', borderTopRightRadius: '6px', marginTop: '6px'},
    '.cm-ob-callout.cm-ob-block-last': {paddingBottom: '8px !important', borderBottomRightRadius: '6px', marginBottom: '6px'},
    '.cm-ob-callout-title': {color: 'var(--ob-callout)', fontWeight: '800'},
    '.cm-ob-callout-label': {
        display: 'inline-flex', alignItems: 'center', gap: '6px', marginRight: '6px',
        color: 'var(--ob-callout)', fontWeight: '800', verticalAlign: 'baseline',
    },
    '.cm-ob-callout-icon': {
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '1.25em', height: '1.25em',
        borderRadius: '999px', border: '1.5px solid var(--ob-callout)', fontSize: '0.75em', lineHeight: '1',
    },
    ...calloutTheme,
    '.cm-ob-table-wrap': {padding: '4px 0 8px'},
    '.cm-ob-table-wrap.cm-ob-callout': {padding: '4px 14px 10px'},
    '.cm-ob-table-scroll': {overflowX: 'auto'},
    '.cm-ob-table-wrap table': {borderCollapse: 'collapse', width: '100%', fontSize: '0.92em', lineHeight: '1.6'},
    '.cm-ob-table-wrap th, .cm-ob-table-wrap td': {
        border: '1px solid var(--c-outline-variant)', padding: '6px 10px', textAlign: 'left', verticalAlign: 'top',
    },
    '.cm-ob-table-wrap th': {backgroundColor: 'var(--c-surface-container-low)', fontWeight: '800'},
    '.cm-ob-table-wrap code': {
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.88em',
        backgroundColor: 'var(--c-surface-container-high)', borderRadius: '4px', padding: '1px 4px',
    },
    '.cm-ob-table-src': {fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.82em'},
    '.cm-ob-image': {display: 'block', margin: '8px 0', textIndent: '0'},
    '.cm-ob-image img': {display: 'block', maxWidth: '100%', borderRadius: '8px'},
    '.cm-ob-crop': {
        display: 'block', position: 'relative', overflow: 'hidden', width: '100%', aspectRatio: '16 / 9', borderRadius: '8px',
    },
    '.cm-ob-crop img': {position: 'absolute', maxWidth: 'none', borderRadius: '0'},
    '.cm-ob-caption': {display: 'block', fontSize: '0.78em', color: 'var(--c-on-surface-variant)', marginTop: '4px'},
    '.cm-ob-flash': {backgroundColor: 'rgba(59, 130, 246, 0.14)', transition: 'background-color 0.3s'},
});


const ToolbarButton = ({className = 'inline-flex', disabled = false, icon, label, onClick}) => (
    <button
        type="button"
        title={label}
        aria-label={label}
        disabled={disabled}
        onMouseDown={(event) => event.preventDefault()}
        onClick={onClick}
        className={`${className} size-8 shrink-0 items-center justify-center rounded-[8px] text-on-surface-variant transition hover:bg-surface-container-high hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-30`}
    >
        <SvgIcon name={icon} className="text-[17px]"/>
    </button>
);

const NoteEditor = ({lang = 'zh', markdown: source = '', onChange, taskId = '', apiBase = ''}) => {
    const hostRef = useRef(null);
    const viewRef = useRef(null);
    const onChangeRef = useRef(onChange);
    onChangeRef.current = onChange;
    // The texts this editor reported recently. The page hands them back as
    // `markdown`, possibly a keystroke behind; an echo must not be applied
    // over newer typing.
    const reportedRef = useRef([]);
    const [toolbar, setToolbar] = useState({style: 'p', canUndo: false, canRedo: false});

    useEffect(() => {
        const view = new EditorView({
            parent: hostRef.current,
            state: EditorState.create({
                doc: source,
                extensions: [
                    history(),
                    markdown(),
                    keymap.of([...markdownKeymap, ...defaultKeymap, ...historyKeymap, indentWithTab]),
                    EditorView.lineWrapping,
                    EditorView.contentAttributes.of({
                        'aria-label': lang === 'zh' ? '笔记正文' : 'Note body',
                        spellcheck: 'false',
                    }),
                    EditorView.updateListener.of((update) => {
                        if (update.docChanged && !update.transactions.some((tr) => tr.annotation(fromPage))) {
                            const text = update.state.doc.toString();
                            reportedRef.current = [...reportedRef.current.slice(-19), text];
                            onChangeRef.current?.(text);
                        }
                        if (update.docChanged || update.selectionSet) {
                            setToolbar({
                                style: lineStyleOf(update.state),
                                canUndo: undoDepth(update.state) > 0,
                                canRedo: redoDepth(update.state) > 0,
                            });
                        }
                    }),
                    editorTheme,
                    noteLivePreview({taskId, apiBase}),
                ],
            }),
        });
        viewRef.current = view;
        return () => view.destroy();
        // Built once per note (the page keys this component by task).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const view = viewRef.current;
        if (!view || reportedRef.current.includes(source)) return;
        const current = view.state.doc.toString();
        if (current === source) return;
        // A newer copy from the page: take it whole, keep the cursor where it
        // can still be, and do not report it as an edit.
        const head = Math.min(view.state.selection.main.head, source.length);
        view.dispatch({
            changes: {from: 0, to: current.length, insert: source},
            selection: {anchor: head},
            annotations: [fromPage.of(true)],
        });
        reportedRef.current = [];
    }, [source]);

    const run = (command) => { const view = viewRef.current; if (view) command(view); };
    const labels = lang === 'zh'
        ? {style: '文本样式', paragraph: '正文', heading1: '一级标题', heading2: '二级标题', heading3: '三级标题', bold: '加粗', italic: '斜体', bulletList: '项目列表', orderedList: '编号列表', undo: '撤销', redo: '重做'}
        : {style: 'Text style', paragraph: 'Paragraph', heading1: 'Heading 1', heading2: 'Heading 2', heading3: 'Heading 3', bold: 'Bold', italic: 'Italic', bulletList: 'Bullet list', orderedList: 'Numbered list', undo: 'Undo', redo: 'Redo'};

    return (
        <div className="@container flex min-h-0 flex-1 flex-col">
            <div data-testid="note-toolbar" className="flex shrink-0 flex-nowrap items-center gap-1.5 border-b border-outline-variant bg-surface-container-low px-4 py-2">
                <label className="sr-only" htmlFor="note-line-style">{labels.style}</label>
                <select
                    id="note-line-style"
                    value={['h2', 'h3', 'h4'].includes(toolbar.style) ? toolbar.style : 'p'}
                    aria-label={labels.style}
                    onChange={(event) => run((view) => setLineStyle(view, event.target.value))}
                    className="h-8 w-[7.25rem] rounded-[8px] border border-outline-variant bg-surface-container-lowest py-0 pl-2 pr-7 text-[13px] leading-5 font-bold text-on-surface-variant outline-none transition focus:border-primary/60 focus:ring-2 focus:ring-primary/15"
                >
                    <option value="p">{labels.paragraph}</option>
                    <option value="h2">{labels.heading1}</option>
                    <option value="h3">{labels.heading2}</option>
                    <option value="h4">{labels.heading3}</option>
                </select>
                <span className="h-5 w-px bg-outline-variant" aria-hidden="true"/>
                <ToolbarButton icon="format_bold" label={labels.bold} onClick={() => run((view) => toggleInline(view, '**'))}/>
                <ToolbarButton icon="format_italic" label={labels.italic} onClick={() => run((view) => toggleInline(view, '*'))}/>
                <ToolbarButton icon="format_list_bulleted" label={labels.bulletList} onClick={() => run((view) => toggleList(view, 'bullet'))}/>
                <ToolbarButton icon="format_list_numbered" label={labels.orderedList} onClick={() => run((view) => toggleList(view, 'ordered'))}/>
                {/* A narrow pane keeps the formatting buttons on one row; undo and redo stay on Ctrl+Z and Ctrl+Shift+Z there. */}
                <ToolbarButton className="hidden @[25rem]:inline-flex" disabled={!toolbar.canUndo} icon="undo" label={labels.undo} onClick={() => run((view) => { undo(view); view.focus(); })}/>
                <ToolbarButton className="hidden @[25rem]:inline-flex" disabled={!toolbar.canRedo} icon="redo" label={labels.redo} onClick={() => run((view) => { redo(view); view.focus(); })}/>
            </div>
            <div ref={hostRef} data-testid="note-editor" className="min-h-0 flex-1"/>
        </div>
    );
};

export default NoteEditor;
