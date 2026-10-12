import {describe, expect, it} from 'vitest';
import {readFileSync} from 'node:fs';

// Clicking a settings switch focused its checkbox, and the browser scrolled the
// shell's overflow-hidden box to bring it into view: the page slid out of sight
// and the window looked blank (reported from Windows, reproduced on macOS).
// The shell must not be scrollable at all; only each page's <main> scrolls.
describe('the app shell', () => {
    it('clips instead of hiding overflow, so nothing can scroll it', () => {
        const source = readFileSync(new URL('./ShellLayout.jsx', import.meta.url), 'utf8');
        expect(source).not.toMatch(/overflow-hidden/);
        expect(source.match(/overflow-clip/g)?.length).toBe(2);
    });
});
