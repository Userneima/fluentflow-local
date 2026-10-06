import {describe, expect, it} from 'vitest';
import {escapeHtml, safeUrl, simpleMd} from './markdown.js';

// The note is model output and is rendered with dangerouslySetInnerHTML, in the
// editor and in the PDF export frame. Requirements, written before the fix:
//   1. Nothing in the note text can become an HTML attribute or tag.
//   2. An image only renders when its address is http(s), relative, or an
//      inline image; every other scheme is dropped, not escaped into place.
//   3. Ordinary notes still render the way they did.

describe('note text cannot break out of markup', () => {
    it('keeps an image alt text with a quote as text, never as an attribute', () => {
        const html = simpleMd('![a" onerror="alert(1)](x.png)');
        // A real attribute needs an unescaped quote before it.
        expect(html).not.toContain('" onerror="');
        expect(html).toContain('alt="a&quot; onerror=&quot;alert(1)"');
    });

    it('escapes every character that can close an attribute or a tag', () => {
        expect(escapeHtml(`&<>"'`)).toBe('&amp;&lt;&gt;&quot;&#39;');
    });

    it('renders a paragraph with angle brackets as literal text', () => {
        expect(simpleMd('1 < 2 and <b>bold</b>')).toContain('1 &lt; 2 and &lt;b&gt;bold&lt;/b&gt;');
    });

    it('keeps table cells inert too', () => {
        const html = simpleMd('| a | b |\n|---|---|\n| <img src=x onerror=alert(1)> | 2 |');
        expect(html).not.toMatch(/<img/);
        expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
    });
});

describe('image addresses', () => {
    it('allows http(s), relative paths, and inline images', () => {
        expect(safeUrl('https://example.com/a.png')).toBe('https://example.com/a.png');
        expect(safeUrl('http://127.0.0.1:8000/jobs/1/frames/2.jpg')).toBe('http://127.0.0.1:8000/jobs/1/frames/2.jpg');
        expect(safeUrl('/jobs/1/frames/2.jpg')).toBe('/jobs/1/frames/2.jpg');
        expect(safeUrl('frames/2.jpg')).toBe('frames/2.jpg');
        expect(safeUrl('data:image/png;base64,iVBORw0KGgo=')).toBe('data:image/png;base64,iVBORw0KGgo=');
    });

    it('drops every other scheme', () => {
        expect(safeUrl('javascript:alert(1)')).toBe('');
        expect(safeUrl('JavaScript:alert(1)')).toBe('');
        expect(safeUrl('java\nscript:alert(1)')).toBe('');
        expect(safeUrl('vbscript:msgbox')).toBe('');
        expect(safeUrl('data:text/html;base64,PHNjcmlwdD4=')).toBe('');
        expect(safeUrl('//evil.example/x.png')).toBe('');
        expect(safeUrl('')).toBe('');
    });

    it('does not render an image whose address was dropped', () => {
        const html = simpleMd('![frame](javascript:alert(1))');
        expect(html).not.toContain('<img');
        expect(html).not.toContain('javascript:');
    });

    it('still renders a normal image with its caption', () => {
        const html = simpleMd('![slide 3](/jobs/1/frames/3.jpg)');
        expect(html).toContain('<img src="/jobs/1/frames/3.jpg" alt="slide 3"');
        expect(html).toContain('<figcaption class="text-xs text-on-surface-variant mt-1">slide 3</figcaption>');
    });
});

describe('ordinary notes', () => {
    it('renders headings, lists, emphasis, and code as before', () => {
        const html = simpleMd('# Title\n\n- one **two** `three`\n\n1. first\n\n> quote');
        expect(html).toContain('<h2 class="text-xl font-headline font-bold mt-6 mb-2">Title</h2>');
        expect(html).toContain('<strong>two</strong>');
        expect(html).toContain('<code class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 text-[0.92em]">three</code>');
        expect(html).toContain('<ol class="list-decimal space-y-1 my-2 pl-5">');
        expect(html).toContain('<blockquote');
    });
});
