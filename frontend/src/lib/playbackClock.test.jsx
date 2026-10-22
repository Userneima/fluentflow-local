// @vitest-environment jsdom

// The player's time reaches only what shows it: a subscriber hears each new
// time, and an unchanged time wakes nobody.
import {describe, expect, it} from 'vitest';
import {act, render, screen} from '@testing-library/react';
import {createPlaybackClock, usePlaybackTime} from './playbackClock.js';

describe('createPlaybackClock', () => {
    it('tells subscribers each new time, and not a repeated one', () => {
        const clock = createPlaybackClock();
        const heard = [];
        const stop = clock.subscribe((t) => heard.push(t));
        clock.set(1);
        clock.set(1);
        clock.set(2.5);
        stop();
        clock.set(3);
        expect(heard).toEqual([1, 2.5]);
        expect(clock.get()).toBe(3);
    });

    it('re-renders a component that shows the time, and only that one', () => {
        const clock = createPlaybackClock();
        let shown = 0;
        let parent = 0;
        const Time = () => { shown += 1; return <span data-testid="t">{usePlaybackTime(clock)}</span>; };
        const Page = () => { parent += 1; return <Time/>; };
        render(<Page/>);
        act(() => { clock.set(4); clock.set(5); });
        expect(screen.getByTestId('t').textContent).toBe('5');
        expect(parent).toBe(1);
        expect(shown).toBeGreaterThan(1);
    });
});
