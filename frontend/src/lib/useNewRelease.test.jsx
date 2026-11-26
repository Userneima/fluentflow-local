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
        answers.push({current: '0.11.0', latest: '0.12.0', update_available: true, checkout: 'release',
            update_command: 'FLUENTFLOW_INSTALL_DIR="/a/app" bash -c "…"'});
        const {result} = renderHook(() => useNewRelease());
        await act(async () => { await Promise.resolve(); await Promise.resolve(); });
        expect(result.current).toBe(null);
        await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
        expect(result.current).toEqual({version: '0.12.0', checkout: 'release',
            command: 'FLUENTFLOW_INSTALL_DIR="/a/app" bash -c "…"'});
    });

    it('drops the notice once the service says the installed version is the latest', async () => {
        vi.useFakeTimers();
        answers.push({current: '0.11.0', latest: '0.12.0', update_available: true, checkout: 'release'});
        answers.push({current: '0.12.0', latest: '0.12.0', update_available: false});
        const {result} = renderHook(() => useNewRelease());
        await act(async () => { await Promise.resolve(); await Promise.resolve(); });
        expect(result.current).toEqual({version: '0.12.0', checkout: 'release', command: ''});
        await act(async () => { await vi.advanceTimersByTimeAsync(6 * 3600 * 1000); });
        expect(result.current).toBe(null);
    });

    it('says a git checkout is updated with git, not the install command', async () => {
        vi.useFakeTimers();
        answers.push({current: '0.11.0', latest: '0.12.0', update_available: true, checkout: 'git',
            update_command: 'cd "/a/app" && git pull && bash launchers/macos/setup-local.sh'});
        const {result} = renderHook(() => useNewRelease());
        await act(async () => { await Promise.resolve(); await Promise.resolve(); });
        expect(result.current).toEqual({version: '0.12.0', checkout: 'git',
            command: 'cd "/a/app" && git pull && bash launchers/macos/setup-local.sh'});
    });

    it('keeps the command the backend wrote for this copy', async () => {
        vi.useFakeTimers();
        answers.push({current: '0.11.0', latest: '0.12.0', update_available: true, checkout: 'release',
            update_command: '$env:FLUENTFLOW_INSTALL_DIR = "D:\\FluentFlow"; powershell …'});
        const {result} = renderHook(() => useNewRelease());
        await act(async () => { await Promise.resolve(); await Promise.resolve(); });
        // The page shows and copies this line: without it, a user who installed
        // into a folder of their own runs the bare command and gets a second copy.
        expect(result.current.command).toContain('D:\\FluentFlow');
    });
});
