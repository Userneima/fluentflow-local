// @vitest-environment jsdom

// A menu that opens from the keyboard, says it is a menu, closes on Escape and
// on a click elsewhere, and gives focus back to its button.

import {afterEach, describe, expect, it} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {DropdownMenu} from './DropdownMenu.jsx';

const mount = () => render(
    <div>
        <button type="button">elsewhere</button>
        <DropdownMenu
            trigger={<button type="button">Export</button>}
            items={[{label: 'TXT', onClick: () => {}}, {divider: true}, {label: 'MD', onClick: () => {}}]}
        />
    </div>
);

describe('DropdownMenu', () => {
    afterEach(cleanup);

    it('announces itself on a real button and opens on click', () => {
        mount();
        const trigger = screen.getByRole('button', {name: 'Export'});
        expect(trigger.getAttribute('aria-haspopup')).toBe('menu');
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
        fireEvent.click(trigger);
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        expect(screen.getByRole('menu')).toBeTruthy();
        expect(screen.getAllByRole('menuitem').map((node) => node.textContent)).toEqual(['TXT', 'MD']);
    });

    it('moves focus into the menu, closes on Escape, and returns focus to the button', () => {
        mount();
        const trigger = screen.getByRole('button', {name: 'Export'});
        trigger.focus();
        fireEvent.click(trigger);
        expect(document.activeElement.textContent).toBe('TXT');
        fireEvent.keyDown(document.activeElement, {key: 'Escape'});
        expect(screen.queryByRole('menu')).toBeNull();
        expect(document.activeElement).toBe(trigger);
    });

    it('closes on a click outside', () => {
        mount();
        fireEvent.click(screen.getByRole('button', {name: 'Export'}));
        expect(screen.getByRole('menu')).toBeTruthy();
        fireEvent.mouseDown(screen.getByRole('button', {name: 'elsewhere'}));
        expect(screen.queryByRole('menu')).toBeNull();
    });
});
