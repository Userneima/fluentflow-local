// @vitest-environment jsdom

// Requirements for the folded sidebar opening on hover (2026-11-19):
//   1. Folded by choice in a wide window, resting the pointer on it opens the
//      full sidebar after a short wait; a pointer that only sweeps past does not.
//   2. It opens over the page: the width the page makes room for stays folded
//      (ShellLayout keeps --sidebar-offset; here, the collapsed prop is unchanged
//      and the toggle offers to pin it open).
//   3. Leaving folds it again after a short wait; coming back within that wait
//      keeps it open. Entering again later opens it again.
//   4. While its bottom menu is open it stays open (also one opened during the
//      close wait); when the menu closes with
//      the pointer outside, it folds after the usual wait.
//   5. The toggle while peeking pins it open (the owner's stored choice).
//   6. Pinned open, or a narrow window (peekOnHover off): hovering changes nothing.

import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {PEEK_CLOSE_DELAY, PEEK_OPEN_DELAY} from '../lib/useHoverPeek.js';

vi.mock('../app/shared.jsx', () => ({
    useI18n: () => ({t: (k) => k, lang: 'zh', toggleLang: () => {}}),
    useSettings: () => ({loadSettings: () => ({}), saveSettings: () => {}}),
}));

const {default: SideNav} = await import('./SideNav.jsx');

const mount = (props) => render(<MemoryRouter><SideNav {...props}/></MemoryRouter>);
const aside = () => document.querySelector('aside');
const wide = () => aside().className.includes('w-56');
const wait = (ms) => act(() => { vi.advanceTimersByTime(ms); });

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('a folded sidebar opens while the pointer rests on it', () => {
    it('opens after the wait, not on a sweep past', () => {
        mount({collapsed: true, peekOnHover: true, onToggle: () => {}});
        expect(wide()).toBe(false);
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY - 50);
        fireEvent.mouseLeave(aside());
        wait(1000);
        expect(wide()).toBe(false);
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        expect(wide()).toBe(true);
        expect(screen.getByText('FluentFlow Local')).toBeTruthy();
        expect(screen.getByRole('button', {name: '固定展开侧边栏'})).toBeTruthy();
    });

    it('folds after leaving, stays if the pointer comes back in time, and opens again later', () => {
        mount({collapsed: true, peekOnHover: true, onToggle: () => {}});
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        fireEvent.mouseLeave(aside());
        wait(PEEK_CLOSE_DELAY - 100);
        expect(wide()).toBe(true);
        fireEvent.mouseEnter(aside());
        wait(PEEK_CLOSE_DELAY * 3);
        expect(wide()).toBe(true);
        fireEvent.mouseLeave(aside());
        wait(PEEK_CLOSE_DELAY + 10);
        expect(wide()).toBe(false);
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        expect(wide()).toBe(true);
    });

    it('stays open while its menu is open, folds once the menu closes', () => {
        mount({collapsed: true, peekOnHover: true, onToggle: () => {}});
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        const menuButton = screen.getByRole('button', {name: /菜单/});
        fireEvent.click(menuButton);
        fireEvent.mouseLeave(aside());
        wait(PEEK_CLOSE_DELAY * 3);
        expect(wide()).toBe(true);
        fireEvent.click(menuButton);
        wait(PEEK_CLOSE_DELAY + 10);
        expect(wide()).toBe(false);
    });

    it('a menu opened while it is about to fold keeps it open', () => {
        mount({collapsed: true, peekOnHover: true, onToggle: () => {}});
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        fireEvent.mouseLeave(aside());
        wait(PEEK_CLOSE_DELAY - 100);
        fireEvent.click(screen.getByRole('button', {name: /菜单/}));
        wait(PEEK_CLOSE_DELAY * 3);
        expect(wide()).toBe(true);
    });

    it('the toggle while peeking pins it open', () => {
        const onToggle = vi.fn();
        mount({collapsed: true, peekOnHover: true, onToggle});
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        fireEvent.click(screen.getByRole('button', {name: '固定展开侧边栏'}));
        expect(onToggle).toHaveBeenCalledTimes(1);
    });

    it('does nothing when pinned open or in a narrow window', () => {
        const {unmount} = mount({collapsed: false, peekOnHover: true, onToggle: () => {}});
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        fireEvent.mouseLeave(aside());
        wait(PEEK_CLOSE_DELAY + 10);
        expect(wide()).toBe(true);
        unmount();
        mount({collapsed: true, peekOnHover: false, onToggle: null});
        fireEvent.mouseEnter(aside());
        wait(PEEK_OPEN_DELAY + 10);
        expect(wide()).toBe(false);
    });
});
