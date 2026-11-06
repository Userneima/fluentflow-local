// @vitest-environment jsdom

// The Feishu documents made on this machine were listed on the settings page
// until 2026-10-22 and then on no page at all (owner, 2026-11-06: put them on
// the records page). Folded under one chip with the count; opened, each
// document is a link with its title, newest task first.

import {afterEach, describe, expect, it} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {LarkExportList} from './agent-tasks.jsx';

afterEach(cleanup);

const exports = [
    {url: 'https://x.feishu.cn/docx/a', title: '周会笔记', timestamp: Date.parse('2026-11-05T10:00:00')},
    {url: 'https://x.feishu.cn/docx/b', title: '张和第四课', timestamp: Date.parse('2026-11-04T09:30:00')},
];

describe('LarkExportList', () => {
    it('shows nothing when nothing was exported', () => {
        const {container} = render(<LarkExportList exports={[]} lang="zh"/>);
        expect(container.textContent).toBe('');
    });

    it('is folded with the count, and opened lists each document as a link, in the order given', () => {
        render(<LarkExportList exports={exports} lang="zh"/>);
        const toggle = screen.getByRole('button', {name: /飞书导出记录/});
        expect(toggle.textContent).toContain('2');
        expect(screen.queryByText('周会笔记')).toBeNull();
        fireEvent.click(toggle);
        const link = screen.getByRole('link', {name: '周会笔记'});
        expect(link.getAttribute('href')).toBe('https://x.feishu.cn/docx/a');
        expect(link.getAttribute('target')).toBe('_blank');
        expect(screen.getAllByRole('link').map((item) => item.textContent)).toEqual(['周会笔记', '张和第四课']);
    });
});
