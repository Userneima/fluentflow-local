import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';

// The Vite dev server injects the react-refresh preamble (which defines
// $RefreshSig$/$RefreshReg$) and the HMR client right after the FIRST `<head`
// it finds in the entry document. If that first match sits inside an HTML
// comment, both script tags are injected into the comment, so they never run:
// every JSX module then throws "$RefreshSig$ is not defined" and the dev app
// renders a blank page. The production build does not use the preamble, so the
// only way to catch this is here.
const ENTRY_FILES = ['../../local.html'];

const firstHeadIndex = (html) => html.indexOf('<head');

const isInsideComment = (html, index) => {
    const before = html.slice(0, index);
    const opened = before.lastIndexOf('<!--');
    if (opened === -1) return false;
    return before.indexOf('-->', opened) === -1;
};

describe.each(ENTRY_FILES)('entry document %s', (relative) => {
    const path = fileURLToPath(new URL(relative, import.meta.url));
    const html = readFileSync(path, 'utf8');

    it('has a head element', () => {
        expect(firstHeadIndex(html)).toBeGreaterThan(-1);
    });

    it('mentions no head tag before the real one, so dev injection lands in the document', () => {
        const index = firstHeadIndex(html);
        expect(
            isInsideComment(html, index),
            'the first "<head" is inside a comment: Vite would inject the react-refresh preamble there and the dev page would render blank',
        ).toBe(false);
    });
});
