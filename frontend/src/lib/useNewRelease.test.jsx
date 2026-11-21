// @vitest-environment jsdom

import {describe, it, expect, vi, afterEach} from 'vitest';
import {renderHook, act} from '@testing-library/react';

const answers = [];
vi.mock('../app/apiConfig.js', () => ({
    API_BASE: '',
    apiFetch: vi.fn(() => Promise.resolve({ok: true, json: () => Promise.resolve(answers.shift())})),
}));

import {useNewRelease} from './useNewRelease.js';

describe('the new-version notice', () => {
    afterEach(() => vi.useRealTimers());

    it('asks again while the service has not learned the latest version yet', async () => {
        vi.useFakeTimers();
        answers.push({current: '0.11.0', latest: null, update_available: false});
        answers.push({current: '0.11.0', latest: '0.12.0', update_available: true});
        const {result} = renderHook(() => useNewRelease());
        await act(async () => { await Promise.resolve(); await Promise.resolve(); });
        expect(result.current).toBe(null);
        await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
        expect(result.current).toBe('0.12.0');
    });
});
