// @vitest-environment jsdom

// Records page redesign, 2026-10-23: below a 1000px window the sidebar folds
// to icons on its own, on every page, without overwriting the folded/unfolded
// choice the owner made; widening the window brings that choice back.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

vi.mock('../components/SideNav.jsx', () => ({
    default: ({collapsed, onToggle}) => (
        <aside data-testid="sidenav" data-collapsed={collapsed ? 'yes' : 'no'} data-toggle={onToggle ? 'yes' : 'no'}/>
    ),
}));
vi.mock('../components/InterruptedTasksDialog.jsx', () => ({default: () => null}));
vi.mock('../components/BackendStatusBanner.jsx', () => ({default: () => null}));

const {default: ShellLayout, NARROW_SIDEBAR_WIDTH} = await import('./ShellLayout.jsx');

let width = 1440;
const listeners = new Set();
const installMatchMedia = () => {
    window.matchMedia = (query) => {
        const max = Number(/max-width:\s*(\d+)px/.exec(query)?.[1]);
        return {
            get matches() { return width <= max; },
            addEventListener: (_type, fn) => listeners.add(fn),
            removeEventListener: (_type, fn) => listeners.delete(fn),
        };
    };
};
const resize = (next) => act(() => {
    width = next;
    listeners.forEach((fn) => fn());
});

const mount = () => render(<MemoryRouter><ShellLayout><p>页面</p></ShellLayout></MemoryRouter>);

afterEach(() => {
    cleanup();
    listeners.clear();
    localStorage.clear();
});

describe('the sidebar in a narrow window', () => {
    it('folds to icons below 1000px and unfolds again when widened', () => {
        width = 1440;
        installMatchMedia();
        mount();
        const nav = () => screen.getByTestId('sidenav');
        expect(nav().dataset.collapsed).toBe('no');
        expect(nav().dataset.toggle).toBe('yes');
        resize(NARROW_SIDEBAR_WIDTH - 232);
        expect(nav().dataset.collapsed).toBe('yes');
        // Folding it there is not the owner's choice: no toggle, nothing stored.
        expect(nav().dataset.toggle).toBe('no');
        expect(localStorage.getItem('fluentflow_sidebar_collapsed')).toBe('0');
        resize(1024);
        expect(nav().dataset.collapsed).toBe('no');
    });

    it('keeps a sidebar the owner folded folded in a wide window', () => {
        width = 1440;
        installMatchMedia();
        localStorage.setItem('fluentflow_sidebar_collapsed', '1');
        mount();
        expect(screen.getByTestId('sidenav').dataset.collapsed).toBe('yes');
    });
});
