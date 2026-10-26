// FluentFlow's interface rules that a machine can check (docs/06-界面规矩.md).
// Each block below is one rule from that document, read off the source files,
// so a new page or button is held to it without anyone remembering to look.
import {describe, expect, it} from 'vitest';
import {readFileSync, readdirSync, statSync} from 'node:fs';
import {join, relative} from 'node:path';
import {msgs} from './app/shared.jsx';
import * as ui from './lib/ui.js';

const SRC = new URL('.', import.meta.url).pathname;

// Comments mention <button> and units in prose; blank them out, keeping the
// line numbers.
const blankComments = (text) => text
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (c) => c.replace(/[^\n]/g, ' '))
    .replace(/^\s*\/\*[\s\S]*?\*\//gm, (c) => c.replace(/[^\n]/g, ' '))
    .replace(/^\s*\/\/.*$/gm, '');

const sourceFiles = (() => {
    const out = [];
    const walk = (dir) => {
        for (const name of readdirSync(dir)) {
            const path = join(dir, name);
            if (statSync(path).isDirectory()) walk(path);
            else if (/\.(js|jsx)$/.test(name) && !/\.test\./.test(name)) out.push(path);
        }
    };
    walk(SRC);
    return out.map((path) => ({path: relative(SRC, path), text: blankComments(readFileSync(path, 'utf8'))}));
})();

const lineOf = (text, index) => text.slice(0, index).split('\n').length;

// ---- Buttons take their style from lib/ui.js ---------------------------------

// The helpers in lib/ui.js that a button's className may use.
const UI_HELPERS = ['dropZone', 'tileButton', 'timestampButton', 'factToggle', 'optionCard', 'bareButton', 'button', 'iconButton', 'linkButton', 'segmentItem', 'chip', 'navItem', 'menuItem', 'toolbarButton', 'rowButton', 'textButton', 'statusButton'];

// Classes a button may add next to a helper: where it sits, how wide it is and
// the spacing inside a whole-row button, never how it looks (colour, height,
// text size, corner, border, weight).
const LAYOUT_CLASS = new RegExp([
    '^(?:[a-z0-9@-]+:)*-?(?:',
    [
        'm[trblxy]?-\\S+', 'p[trblxy]?-\\S+', 'w-\\S+', 'min-w-\\S+', 'min-h-\\S+', 'max-w-\\S+', 'flex', 'flex-\\S+', 'grow\\S*', 'shrink\\S*', 'basis-\\S+',
        'self-\\S+', 'justify-\\S+', 'items-\\S+', 'order-\\S+', 'hidden', 'block', 'inline', 'inline-flex', 'absolute', 'relative',
        'top-\\S+', 'left-\\S+', 'right-\\S+', 'bottom-\\S+', 'inset-\\S+', 'z-\\S+', 'gap-\\S+', 'text-left', 'text-center',
        'text-right', 'truncate', 'line-clamp-\\S+', 'break-\\S+', 'whitespace-\\S+', 'col-\\S+', 'row-\\S+', 'overflow-\\S+',
        'sr-only', 'grid', 'grid-cols-\\S+', 'grid-rows-\\S+', 'place-\\S+', 'group', 'peer', 'not-italic', 'tabular-nums', 'cursor-\\S+', 'pointer-events-\\S+', 'select-\\S+', 'contents',
    ].join('|'),
    ')$',
].join(''));

// The opening tag starting at `start` (at "<"), braces balanced, so a ">" inside
// an attribute expression does not end it.
const openingTag = (text, start) => {
    let depth = 0;
    let quote = '';
    for (let i = start + 1; i < text.length; i += 1) {
        const c = text[i];
        if (quote) {
            if (c === quote && text[i - 1] !== '\\') quote = '';
            continue;
        }
        if (depth > 0 && (c === '\'' || c === '"' || c === '`')) { quote = c; continue; }
        if (c === '{') depth += 1;
        else if (c === '}') depth -= 1;
        else if (c === '>' && depth === 0) return text.slice(start, i + 1);
    }
    return text.slice(start);
};

// The value of className in an opening tag: {kind:'literal'|'expr', value}.
const classNameOf = (tag) => {
    const at = tag.search(/\sclassName=/);
    if (at < 0) return null;
    const rest = tag.slice(at + ' className='.length);
    if (rest[0] === '"' || rest[0] === '\'') return {kind: 'literal', value: rest.slice(1, rest.indexOf(rest[0], 1))};
    if (rest[0] !== '{') return null;
    let depth = 0;
    for (let i = 0; i < rest.length; i += 1) {
        if (rest[i] === '{') depth += 1;
        else if (rest[i] === '}') { depth -= 1; if (depth === 0) return {kind: 'expr', value: rest.slice(1, i)}; }
    }
    return {kind: 'expr', value: rest.slice(1)};
};

// A helper call with its arguments, which are tone names and conditions, not
// classes.
const dropHelperCalls = (expr) => {
    let out = expr;
    for (const name of UI_HELPERS) {
        let at = out.search(new RegExp(`\\b${name}\\(`));
        while (at >= 0) {
            let depth = 0;
            let end = at + name.length;
            for (; end < out.length; end += 1) {
                if (out[end] === '(') depth += 1;
                else if (out[end] === ')') { depth -= 1; if (depth === 0) break; }
            }
            out = `${out.slice(0, at)}${name}${out.slice(end + 1)}`;
            at = out.search(new RegExp(`\\b${name}\\(`));
        }
    }
    return out;
};

// The fixed class text inside an expression: string literals and the static
// parts of template literals.
const staticClassText = (rawExpr) => {
    const expr = dropHelperCalls(rawExpr);
    const parts = [];
    expr.replace(/'([^']*)'|"([^"]*)"/g, (_, a, b) => { parts.push(a ?? b); return ''; });
    expr.replace(/`((?:[^`\\]|\\.)*)`/g, (_, body) => { parts.push(body.replace(/\$\{[^}]*\}/g, ' ')); return ''; });
    return parts.join(' ');
};

// Props through which a component receives only its placement from the
// caller (NoteEditor's toolbar buttons, the transcript's timestamps).
const LAYOUT_PROPS = new Set(['className', 'layout']);

// String arguments of the helpers themselves (tone and size names) are not
// classes.
const HELPER_ARGS = new Set(['primary', 'secondary', 'quiet', 'danger', 'sm', 'md', 'lg', 'blue', 'inherit', 'default', 'error', 'warning', 'info', 'success', 'busy', 'muted']);

export const buttonStyleProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        if (path === 'lib/ui.js') continue;
        for (const match of text.matchAll(/<(button|Link|NavLink|a|div|span|label|li)\b/g)) {
            const tag = openingTag(text, match.index);
            const where = `${path}:${lineOf(text, match.index)}`;
            const cls = classNameOf(tag);
            const actsAsButton = match[1] === 'button' || /\srole="button"/.test(tag);
            if (['div', 'span', 'label', 'li'].includes(match[1]) && !actsAsButton) continue;
            if (actsAsButton && /\sstyle=\{/.test(tag)) { problems.push(`${where} styles a button inline (style=)`); continue; }
            if (!actsAsButton) {
                // A link only counts when it is drawn as a button: a fixed
                // height and a corner radius.
                const look = cls ? (cls.kind === 'literal' ? cls.value : staticClassText(cls.value)) : '';
                if (!/\brounded-/.test(look) || !/(?:^|\s)(?:h-\d|size-\d)/.test(look)) continue;
            }
            if (!cls) { problems.push(`${where} has no className from lib/ui.js`); continue; }
            if (cls.kind === 'literal') { problems.push(`${where} writes its own classes: "${cls.value.slice(0, 60)}"`); continue; }
            const usesHelper = UI_HELPERS.some((name) => new RegExp(`\\b${name}\\b`).test(cls.value));
            if (!usesHelper) { problems.push(`${where} does not use a lib/ui.js helper: {${cls.value.slice(0, 60)}}`); continue; }
            // A variable next to the helper could carry any look; only the
            // layout a component was handed may pass through.
            const hidden = [...dropHelperCalls(cls.value).matchAll(/\$\{\s*([A-Za-z_$][\w$.]*)\s*\}/g)]
                .map((m) => m[1]).filter((name) => !UI_HELPERS.includes(name) && !LAYOUT_PROPS.has(name));
            if (hidden.length) { problems.push(`${where} adds classes from a variable next to the helper: ${hidden.join(', ')}`); continue; }
            const extras = staticClassText(cls.value).split(/\s+/).filter(Boolean)
                .filter((c) => !HELPER_ARGS.has(c) && !LAYOUT_CLASS.test(c));
            if (extras.length) problems.push(`${where} adds look classes next to the helper: ${extras.join(' ')}`);
        }
    }
    return problems;
};

describe('buttons use the shared styles', () => {
    it('every button and button-shaped link takes its look from lib/ui.js', () => {
        expect(buttonStyleProblems(sourceFiles)).toEqual([]);
    });

    it('catches a button that writes its own colours', () => {
        const broken = [{path: 'x.jsx', text: '<button type="button" className="rounded-[12px] bg-[#111111] px-3 text-white">写笔记</button>'}];
        expect(buttonStyleProblems(broken)).toHaveLength(1);
        const padded = [{path: 'x.jsx', text: '<button className={`${button(\'primary\')} bg-red-500`}>x</button>'}];
        expect(buttonStyleProblems(padded)).toHaveLength(1);
        const fine = [{path: 'x.jsx', text: '<button className={`${button(\'primary\', \'sm\')} ml-auto w-full`}>x</button>'}];
        expect(buttonStyleProblems(fine)).toEqual([]);
        const hiddenLook = [{path: 'x.jsx', text: 'const red = \'bg-red-500\';\n<button className={`${button()} ${red}`}>x</button>'}];
        expect(buttonStyleProblems(hiddenLook)).toHaveLength(1);
        const roleButton = [{path: 'x.jsx', text: '<div role="button" className="rounded-[10px] bg-[#111] px-3">x</div>'}];
        expect(buttonStyleProblems(roleButton)).toHaveLength(1);
        const inlineStyle = [{path: 'x.jsx', text: '<button className={button()} style={{background: \'red\'}}>x</button>'}];
        expect(buttonStyleProblems(inlineStyle)).toHaveLength(1);
    });
});

// ---- Dark mode reaches the shared styles ---------------------------------------

// local.html darkens old pages with `.dark .bg-white { … !important }` and the
// like. A class the shared styles use must not be one of those, or its own
// dark colours, hover included, never apply (2026-10-24 review: secondary
// buttons, chips and icon buttons had no hover in dark mode).
const darkOverrides = (() => {
    const html = readFileSync(join(SRC, '../local.html'), 'utf8');
    const names = new Set();
    for (const m of html.matchAll(/\.dark\s+\.((?:[\w-]|\\.)+)(?::hover|::placeholder|:focus)?\s*\{[^}]*!important/g)) {
        names.add(m[1].replace(/\\/g, ''));
    }
    return names;
})();

const everyUiClass = () => {
    const out = [];
    const tones = ['primary', 'secondary', 'quiet', 'danger', 'blue', 'muted', 'inherit', 'warning', 'error', 'busy', 'default'];
    for (const value of Object.values(ui)) {
        if (typeof value === 'string') out.push(value);
        else if (typeof value === 'function') {
            for (const a of [...tones, true, false]) for (const b of ['sm', 'md', 'lg', true, false]) out.push(value(a, b));
        }
    }
    return new Set(out.join(' ').split(/\s+/).filter(Boolean));
};

describe('dark mode reaches the shared styles', () => {
    it('no class in lib/ui.js is overridden by the dark-mode patches in local.html', () => {
        expect([...everyUiClass()].filter((c) => darkOverrides.has(c))).toEqual([]);
    });
    it('a secondary button looks different when hovered, in both modes', () => {
        const classes = ui.button('secondary').split(' ');
        const pick = (prefix) => classes.filter((c) => c.startsWith(prefix) && /bg-/.test(c));
        expect(pick('dark:hover:bg-')).toHaveLength(1);
        expect(pick('dark:bg-')).toHaveLength(1);
        expect(pick('dark:hover:bg-')[0].slice('dark:hover:'.length)).not.toBe(pick('dark:bg-')[0].slice('dark:'.length));
        expect(pick('hover:bg-')[0]).not.toBe(classes.find((c) => /^bg-/.test(c)));
    });
    it('catches a shared class that a dark patch overrides', () => {
        expect(darkOverrides.has('bg-white')).toBe(true);
    });
});

// ---- Opacity steps exist ------------------------------------------------------

// Tailwind only generates opacity modifiers on its scale (or in brackets).
// `text-white/62` produces no CSS at all, so dark mode silently fell back to
// the light-mode grey (2026-10-23: the records page's fact blocks).
const OPACITY_STEPS = new Set([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]);

export const opacityProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        const re = /\b(?:text|bg|border|ring|fill|stroke|outline|decoration|divide|placeholder|from|via|to|shadow)-[a-z]+(?:-\d+)?\/(\d+)\b/g;
        for (const m of text.matchAll(re)) {
            if (!OPACITY_STEPS.has(Number(m[1]))) problems.push(`${path}:${lineOf(text, m.index)} ${m[0]}`);
        }
    }
    return problems;
};

describe('colour opacity uses steps that exist', () => {
    it('no class uses an opacity Tailwind does not generate', () => {
        expect(opacityProblems(sourceFiles)).toEqual([]);
    });
    it('catches an opacity off the scale', () => {
        expect(opacityProblems([{path: 'x.jsx', text: 'className="dark:text-white/62"'}])).toHaveLength(1);
        expect(opacityProblems([{path: 'x.jsx', text: 'className="dark:text-white/60 bg-white/[0.62]"'}])).toEqual([]);
    });
});

// ---- Corners ------------------------------------------------------------------

// Cards 16px, blocks inside them and switch tracks 12px, buttons and inputs
// 10px, small blocks and menu items 8px, the checkbox 6px, a sentence being
// edited 4px (docs/06-界面规矩.md). Pills use rounded-full. The theme's named
// sizes (rounded-lg is 4px here) are not used on pages; lib/markdown.js styles
// note content and is left out.
const CORNERS = new Set([4, 6, 8, 10, 12, 16]);
export const cornerProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        if (path === 'lib/markdown.js') continue;
        for (const m of text.matchAll(/\brounded-(?:[trbl]{1,2}-)?\[(\d+)px\]/g)) {
            if (!CORNERS.has(Number(m[1]))) problems.push(`${path}:${lineOf(text, m.index)} ${m[0]}`);
        }
        for (const m of text.matchAll(/\brounded(?:-(?:[trbl]{1,2}))?(?:-(?:sm|md|lg|xl|2xl|3xl))?(?=[\s"'`])/g)) {
            if (m[0] !== 'rounded-full') problems.push(`${path}:${lineOf(text, m.index)} ${m[0]}`);
        }
    }
    return problems;
};

describe('corners come in the agreed sizes', () => {
    it('nothing is rounder than a card', () => {
        expect(cornerProblems(sourceFiles)).toEqual([]);
    });
    it('catches a 24px card', () => {
        expect(cornerProblems([{path: 'x.jsx', text: 'className="rounded-[24px] border"'}])).toHaveLength(1);
        expect(cornerProblems([{path: 'x.jsx', text: 'className="rounded-[14px] border"'}])).toHaveLength(1);
        expect(cornerProblems([{path: 'x.jsx', text: 'className="rounded-lg border"'}])).toHaveLength(1);
        expect(cornerProblems([{path: 'x.jsx', text: 'className="rounded-[16px] rounded-full"'}])).toEqual([]);
    });
});

// ---- Note lengths go through fmtCharCount -------------------------------------

export const charCountProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        if (path === 'lib/format.js') continue;
        text.split('\n').forEach((line, i) => {
            if (/\$\{[^}]+\}\s*(?:字(?![幕体号段])|chars\b)|\}\s*字(?![幕体号段])/.test(line)) problems.push(`${path}:${i + 1} ${line.trim().slice(0, 90)}`);
        });
    }
    return problems;
};

describe('note lengths have one wording', () => {
    it('no page writes a character count by hand', () => {
        expect(charCountProblems(sourceFiles)).toEqual([]);
    });
    it('catches a hand-written count', () => {
        expect(charCountProblems([{path: 'x.jsx', text: 'const a = `${n.toLocaleString()} 字`;'}])).toHaveLength(1);
        expect(charCountProblems([{path: 'x.jsx', text: 'const a = `${n} 字幕`;'}])).toEqual([]);
    });
});

// ---- Model tokens and money go through fmtTokens and fmtUsd -------------------

export const usageWordingProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        if (path === 'lib/format.js') continue;
        text.split('\n').forEach((line, i) => {
            if (/\$\{[^}]+\}\s*tokens?\b|\$\$\{|\\\$\$\{|¥\$\{/.test(line)) problems.push(`${path}:${i + 1} ${line.trim().slice(0, 90)}`);
        });
    }
    return problems;
};

describe('model tokens and money have one wording', () => {
    it('no page writes a token count or a dollar amount by hand', () => {
        expect(usageWordingProblems(sourceFiles)).toEqual([]);
    });
    it('catches a hand-written one', () => {
        expect(usageWordingProblems([{path: 'x.jsx', text: 'const a = `${n} token`;'}])).toHaveLength(1);
        expect(usageWordingProblems([{path: 'x.jsx', text: 'const a = `$${cost.toFixed(2)}`;'}])).toHaveLength(1);
        expect(usageWordingProblems([{path: 'x.jsx', text: 'const a = `¥${cost}`;'}])).toHaveLength(1);
        expect(usageWordingProblems([{path: 'x.jsx', text: 'const a = `约 ${fmtCosts(cost)}`;'}])).toEqual([]);
    });
});

// ---- Type is at least 11px ----------------------------------------------------

export const tinyTextProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        for (const m of text.matchAll(/\btext-\[(\d+(?:\.\d+)?)px\]/g)) {
            if (Number(m[1]) < 11) problems.push(`${path}:${lineOf(text, m.index)} ${m[0]}`);
        }
    }
    return problems;
};

describe('no text is smaller than 11px', () => {
    it('every fixed text size is 11px or more', () => {
        expect(tinyTextProblems(sourceFiles)).toEqual([]);
    });
    it('catches 10px', () => {
        expect(tinyTextProblems([{path: 'x.jsx', text: 'className="text-[10px]"'}])).toHaveLength(1);
    });
});

// ---- Only floating layers cast a shadow ---------------------------------------

export const shadowProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        if (path === 'lib/ui.js') continue;
        text.split('\n').forEach((line, i) => {
            const m = line.match(/\bshadow-(?!none\b)[\w[]/);
            if (m && !/\b(?:fixed|absolute)\b/.test(line)) problems.push(`${path}:${i + 1} ${line.trim().slice(0, 90)}`);
        });
    }
    return problems;
};

describe('cards have no shadow', () => {
    it('a shadow is only on something positioned over the page', () => {
        expect(shadowProblems(sourceFiles)).toEqual([]);
    });
    it('catches a card with a shadow', () => {
        expect(shadowProblems([{path: 'x.jsx', text: '<section className="rounded-[16px] border shadow-[0_18px_44px_-38px_rgba(0,0,0,.4)]">'}])).toHaveLength(1);
        expect(shadowProblems([{path: 'x.jsx', text: '<div className="fixed right-8 top-6 shadow-lg">'}])).toEqual([]);
    });
});

// ---- Headline and body fonts end in a generic family --------------------------

// Headlines once named only Manrope, which the app never ships, and every
// headline fell back to the browser's serif.
export const fontStackProblems = (config) => Object.entries(config.theme.extend.fontFamily)
    .filter(([, stack]) => !['sans-serif', 'serif', 'monospace'].includes(stack[stack.length - 1]))
    .map(([name]) => name);

describe('fonts fall back to a sans family', () => {
    it('every font stack in the Tailwind config ends in a generic family', async () => {
        const {default: config} = await import('../../tailwind.config.cjs');
        expect(fontStackProblems(config)).toEqual([]);
    });
    it('catches a stack of one unshipped font', () => {
        expect(fontStackProblems({theme: {extend: {fontFamily: {headline: ['Manrope']}}}})).toEqual(['headline']);
    });
});

// ---- Lengths of time go through fmtDuration -----------------------------------

// A number glued to a unit of time by hand, outside lib/format.js. "N 分钟前"
// is a moment, not a length, and stays allowed.
const HAND_DURATION = [
    /\$\{[^}]+\}\s*(?:秒|分钟(?!前)|分(?![钟享])|小时(?!前))/,
    /\}\s*(?:秒|分钟(?!前)|分(?![钟享])|小时(?!前))/,
    /\$\{[^}]+\}(?:s|m|h)(?=[\s`'"])/,
    /\$\{[^}]+\}\s*(?:min|mins|sec|secs)(?![\w-])/,
    /\+\s*['"`]\s*(?:秒|分钟|小时|min|sec)/,
];

export const durationProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        if (path === 'lib/format.js') continue;
        text.split('\n').forEach((line, i) => {
            if (/^\s*(?:\/\/|\*)/.test(line)) return;
            if (HAND_DURATION.some((re) => re.test(line))) problems.push(`${path}:${i + 1} ${line.trim().slice(0, 90)}`);
        });
    }
    return problems;
};

describe('lengths of time have one wording', () => {
    it('no page writes a length of time by hand', () => {
        expect(durationProblems(sourceFiles)).toEqual([]);
    });
    it('inside lib/format.js only fmtDuration writes hours, minutes and seconds', () => {
        const text = readFileSync(join(SRC, 'lib/format.js'), 'utf8');
        const start = text.indexOf('export const fmtDuration');
        const end = text.indexOf('\n};', start);
        const outside = text.slice(0, start) + text.slice(end);
        expect(durationProblems([{path: 'format-outside.js', text: outside}])).toEqual([]);
    });
    it('catches a hand-written length', () => {
        expect(durationProblems([{path: 'x.jsx', text: 'const a = `${m} 分 ${s} 秒`;'}])).toHaveLength(1);
        expect(durationProblems([{path: 'x.jsx', text: 'const a = `时长：${n} min`;'}])).toHaveLength(1);
        expect(durationProblems([{path: 'x.jsx', text: 'const a = `${n} 分钟前`;'}])).toEqual([]);
        expect(durationProblems([{path: 'x.jsx', text: '<span>{m} 分 {s} 秒</span>'}])).toHaveLength(1);
        expect(durationProblems([{path: 'x.jsx', text: '<span>{mins} 分钟</span>'}])).toHaveLength(1);
        expect(durationProblems([{path: 'x.jsx', text: 'const a = `${n}s`;'}])).toHaveLength(1);
    });
});

// ---- No English placeholders in the Chinese interface -------------------------

const PLACEHOLDER = /\b(?:Loading|Saving|Please wait|Untitled|TODO|Lorem|Coming soon|N\/A)\b/;

// A string literal with an English placeholder is fine only as the English
// side of a language choice: `zh ? '正在读取…' : 'Loading…'`.
export const placeholderProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        // JSX text between tags, wherever the line breaks fall.
        for (const m of text.matchAll(/[>}]([^<>{}'"`=;]+)(?=[<{])/g)) {
            if (PLACEHOLDER.test(m[1])) problems.push(`${path}:${lineOf(text, m.index)} ${m[1].trim()}`);
        }
        text.split('\n').forEach((line, i) => {
            if (/^\s*(?:\/\/|\*)/.test(line)) return;
            if (!PLACEHOLDER.test(line)) return;
            for (const m of line.matchAll(/(['"`])((?:(?!\1)[^\\]|\\.)*)\1/g)) {
                if (!PLACEHOLDER.test(m[2])) continue;
                const before = line.slice(0, m.index);
                const englishSide = /(['"`])[^'"`]*[一-鿿][^'"`]*\1\s*\)?\s*:\s*$/.test(before);
                const enDictionary = /^\s*'[\w.]+'\s*:/.test(before) || /,\s*'[\w.]+'\s*:\s*$/.test(before);
                if (!englishSide && !(enDictionary && path === 'app/shared.jsx')) problems.push(`${path}:${i + 1} '${m[2]}'`);
            }
        });
    }
    return problems;
};

describe('the Chinese interface shows no English placeholders', () => {
    it('every interface string has a Chinese version', () => {
        const missing = Object.keys(msgs.en).filter((key) => !(key in msgs.zh));
        expect(missing).toEqual([]);
        const english = Object.entries(msgs.zh).filter(([, value]) => PLACEHOLDER.test(value));
        expect(english).toEqual([]);
    });
    it('no page writes an English placeholder outside a language choice', () => {
        expect(placeholderProblems(sourceFiles)).toEqual([]);
    });
    it('catches a hard-coded Loading', () => {
        expect(placeholderProblems([{path: 'x.jsx', text: '    <p>Loading...</p>'}])).toHaveLength(1);
        expect(placeholderProblems([{path: 'x.jsx', text: '    <p className="x">\n        Loading...\n    </p>'}])).toHaveLength(1);
        expect(placeholderProblems([{path: 'x.jsx', text: "    const title = name || 'Untitled';"}])).toHaveLength(1);
        expect(placeholderProblems([{path: 'x.jsx', text: "    {zh ? '正在读取…' : 'Loading…'}"}])).toEqual([]);
    });
});

// ---- FluentFlow's words ------------------------------------------------------

// A file chosen on this computer is read where it is, so it is a 本机 file, not
// an upload or a 本地 one (decision 1.7). 上传 stays for the one path that does
// copy the file (a dropped file whose place could not be told).
const WRONG_WORDS = [
    [/本地/, '本机'],
    [/转写/, '转录'],
];

export const wordingProblems = (files) => {
    const problems = [];
    for (const {path, text} of files) {
        text.split('\n').forEach((line, i) => {
            // Matching what the service says is not saying it.
            if (/\.includes\(|\.replace\(\/|\.test\(/.test(line)) return;
            for (const [re, right] of WRONG_WORDS) {
                const m = line.match(re);
                if (m) problems.push(`${path}:${i + 1} 「${m[0]}」 should be 「${right}」`);
            }
        });
    }
    return problems;
};

describe('the interface uses FluentFlow\'s words', () => {
    it('files on this computer are 本机 files, and speech becomes text by 转录', () => {
        expect(wordingProblems(sourceFiles)).toEqual([]);
        expect(Object.entries(msgs.zh).filter(([, v]) => WRONG_WORDS.some(([re]) => re.test(v)))).toEqual([]);
    });
    it('catches 本地文件', () => {
        expect(wordingProblems([{path: 'x.jsx', text: "label: '本地文件'"}])).toHaveLength(1);
        expect(wordingProblems([{path: 'x.jsx', text: "detail: '先剪掉气口再转写'"}])).toHaveLength(1);
        expect(wordingProblems([{path: 'x.jsx', text: "if (raw.includes('转写超时'))"}])).toEqual([]);
    });
});
