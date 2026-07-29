import {simpleMd} from './markdown.js';

const blockTags = new Set(['P', 'DIV', 'SECTION', 'ARTICLE']);

const inlineMarkdown = (node) => {
    if (node.nodeType === 3) return node.nodeValue || '';
    if (node.nodeType !== 1) return '';

    const tag = node.tagName;
    const content = Array.from(node.childNodes).map(inlineMarkdown).join('');
    if (tag === 'BR') return '\n';
    if (tag === 'STRONG' || tag === 'B') return `**${content}**`;
    if (tag === 'EM' || tag === 'I') return `*${content}*`;
    if (tag === 'CODE' && node.parentElement?.tagName !== 'PRE') return `\`${content}\``;
    if (tag === 'A') {
        const href = node.getAttribute('href') || '';
        return href ? `[${content}](${href})` : content;
    }
    if (tag === 'IMG') {
        const src = node.getAttribute('src') || '';
        return src ? `![${node.getAttribute('alt') || ''}](${src})` : '';
    }
    return content;
};

const listMarkdown = (node, depth=0) => {
    const ordered = node.tagName === 'OL';
    return Array.from(node.children)
        .filter((child) => child.tagName === 'LI')
        .map((item, index) => {
            const nested = Array.from(item.children).filter((child) => child.tagName === 'UL' || child.tagName === 'OL');
            const content = Array.from(item.childNodes)
                .filter((child) => !nested.includes(child))
                .map(inlineMarkdown)
                .join('')
                .trim();
            const marker = ordered ? `${index + 1}. ` : '- ';
            const prefix = `${'  '.repeat(depth)}${marker}`;
            const nestedText = nested.map((child) => listMarkdown(child, depth + 1)).filter(Boolean).join('\n');
            return `${prefix}${content}${nestedText ? `\n${nestedText}` : ''}`.trimEnd();
        })
        .join('\n');
};

const tableMarkdown = (table) => {
    const rows = Array.from(table.querySelectorAll('tr'));
    if (!rows.length) return '';
    const values = rows.map((row) => Array.from(row.children)
        .filter((cell) => cell.tagName === 'TH' || cell.tagName === 'TD')
        .map((cell) => inlineMarkdown(cell).replace(/\|/g, '\\|').trim()));
    const width = Math.max(...values.map((row) => row.length), 1);
    const pad = (row) => Array.from({length: width}, (_, index) => row[index] || '');
    const hasHeader = rows[0].querySelector('th') !== null;
    const body = values.map((row) => `| ${pad(row).join(' | ')} |`);
    if (hasHeader) body.splice(1, 0, `| ${Array.from({length: width}, () => '---').join(' | ')} |`);
    return body.join('\n');
};

const blockMarkdown = (node) => {
    if (node.nodeType === 3) return (node.nodeValue || '').trim();
    if (node.nodeType !== 1) return '';

    const tag = node.tagName;
    if (tag === 'H1' || tag === 'H2') return `# ${inlineMarkdown(node).trim()}`;
    if (tag === 'H3') return `## ${inlineMarkdown(node).trim()}`;
    if (tag === 'H4') return `### ${inlineMarkdown(node).trim()}`;
    if (tag === 'H5' || tag === 'H6') return `#### ${inlineMarkdown(node).trim()}`;
    if (tag === 'UL' || tag === 'OL') return listMarkdown(node);
    if (tag === 'BLOCKQUOTE') return inlineMarkdown(node).trim().split('\n').map((line) => `> ${line}`).join('\n');
    if (tag === 'PRE') return `\`\`\`\n${node.textContent || ''}\n\`\`\``;
    if (tag === 'TABLE') return tableMarkdown(node);
    if (tag === 'FIGURE') return Array.from(node.querySelectorAll('img')).map(inlineMarkdown).join('\n');
    if (tag === 'HR') return '---';
    if (tag === 'BR') return '';
    if (blockTags.has(tag)) {
        const table = Array.from(node.children).find((child) => child.tagName === 'TABLE');
        return table ? tableMarkdown(table) : inlineMarkdown(node).trim();
    }
    return inlineMarkdown(node).trim();
};

export const markdownToEditableHtml = (markdown) => simpleMd(markdown || '', {
    renderImages: true,
    renderManualListMarkers: false,
});

export const markdownToStructuredEditorHtml = (markdown) => {
    const container = document.createElement('div');
    container.innerHTML = markdownToEditableHtml(markdown);
    Array.from(container.children)
        .filter((node) => node.tagName === 'BR')
        .forEach((node) => node.remove());
    return container.innerHTML;
};

export const editableHtmlToMarkdown = (html) => {
    const container = document.createElement('div');
    container.innerHTML = html || '';
    const blocks = [];
    for (const node of container.childNodes) {
        const markdown = blockMarkdown(node).trim();
        if (markdown) blocks.push(markdown);
    }
    return blocks.join('\n\n').replace(/\n{3,}/g, '\n\n').trim();
};
