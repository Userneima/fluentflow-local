// @vitest-environment jsdom

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import OutputFolderLine, {outputFolderPath} from './OutputFolderLine.jsx';

// Owner, 2026-10-18: a link video's outputs were filed under the Videos folder
// and nothing on the page said where; the only button read "download".
describe('OutputFolderLine', () => {
    afterEach(() => {
        cleanup();
        vi.restoreAllMocks();
    });

    it('names the folder the task recorded', () => {
        render(<OutputFolderLine taskId="t1" result={{output_folder: {path: 'D:\\视频\\FluentFlow\\面试'}}} lang="zh"/>);
        expect(screen.getByText('D:\\视频\\FluentFlow\\面试')).toBeTruthy();
        expect(screen.getByRole('button', {name: '打开文件夹'})).toBeTruthy();
    });

    it('says nothing for a task with no folder of the user\'s', () => {
        const {container} = render(<OutputFolderLine taskId="t1" result={{}} lang="zh"/>);
        expect(container.textContent).toBe('');
        expect(outputFolderPath({output_folder: {error: 'x'}})).toBe('');
    });

    it('asks the service to open it, by task, and shows why when it cannot', async () => {
        const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            new Response(JSON.stringify({detail: '产出文件夹不在原来的位置了'}), {status: 409, headers: {'Content-Type': 'application/json'}}),
        );
        render(<OutputFolderLine taskId="t 1" result={{output_folder: {path: '/Users/me/Movies/FluentFlow/a'}}} lang="zh"/>);
        fireEvent.click(screen.getByRole('button', {name: '打开文件夹'}));
        await waitFor(() => expect(screen.getByText(/不在原来的位置/)).toBeTruthy());
        const [url, init] = fetchMock.mock.calls[0];
        expect(String(url)).toMatch(/\/jobs\/t%201\/output-folder\/open$/);
        expect(init.method).toBe('POST');
        expect(init.body).toBeUndefined();
    });
});
