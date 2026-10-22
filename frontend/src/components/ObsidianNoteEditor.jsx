import {useEffect, useRef} from 'react';
import {EditorState} from '@codemirror/state';
import {EditorView, keymap} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap, indentWithTab} from '@codemirror/commands';
import {markdown, markdownKeymap} from '@codemirror/lang-markdown';
import {obsidianLivePreview} from '../lib/obsidianLivePreview.js';

// Prototype behind ?editor=obsidian. It edits the note's markdown source in place
// and never reports changes upward: nothing typed here is saved anywhere.

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

const ObsidianNoteEditor = ({lang = 'zh', markdown: source = '', taskId = '', apiBase = ''}) => {
    const hostRef = useRef(null);
    const viewRef = useRef(null);
    const editedRef = useRef(false);

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
                        'aria-label': lang === 'zh' ? '编辑笔记正文（样机）' : 'Edit note body (prototype)',
                        spellcheck: 'false',
                    }),
                    EditorView.updateListener.of((update) => {
                        if (update.docChanged) editedRef.current = true;
                    }),
                    editorTheme,
                    obsidianLivePreview({taskId, apiBase}),
                ],
            }),
        });
        viewRef.current = view;
        return () => view.destroy();
        // Built once per note; the effect below follows the note until it is edited here.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const view = viewRef.current;
        if (!view || editedRef.current || view.state.doc.toString() === source) return;
        view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: source}});
        editedRef.current = false;
    }, [source]);

    return (
        <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex shrink-0 items-center gap-2 border-b border-amber-300/60 bg-amber-50 px-4 py-2 text-xs font-bold text-amber-900 dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-200">
                <span className="rounded-[6px] bg-amber-200/80 px-1.5 py-0.5 dark:bg-amber-400/20">{lang === 'zh' ? '样机' : 'Prototype'}</span>
                <span>{lang === 'zh'
                    ? 'Obsidian 式编辑器：改动只留在这个页面，不保存，刷新就还原。'
                    : 'Obsidian-style editor: edits stay on this page and are not saved.'}</span>
            </div>
            <div ref={hostRef} className="min-h-0 flex-1"/>
        </div>
    );
};

export default ObsidianNoteEditor;
