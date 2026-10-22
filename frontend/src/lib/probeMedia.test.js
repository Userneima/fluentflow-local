// Requirement: a present file must not be reported missing because one request
// failed; a file the service says is gone is reported at once, without waiting.

import {describe, expect, it, vi} from 'vitest';
import {probeMedia} from './probeMedia.js';

const noSleep = {sleep: async () => {}};

describe('probeMedia', () => {
    it('tries again when a request gets no answer, and returns the file once it answers', async () => {
        const fetchFn = vi.fn()
            .mockRejectedValueOnce(new TypeError('Failed to fetch'))
            .mockResolvedValueOnce(new Response('x', {status: 206}));

        const response = await probeMedia(fetchFn, '/jobs/t1/artifacts/debreath_media', noSleep);

        expect(response.status).toBe(206);
        expect(fetchFn).toHaveBeenCalledTimes(2);
    });

    it('tries again after a server error', async () => {
        const fetchFn = vi.fn()
            .mockResolvedValueOnce(new Response('busy', {status: 503}))
            .mockResolvedValueOnce(new Response('x', {status: 206}));

        const response = await probeMedia(fetchFn, '/media', noSleep);

        expect(response.ok).toBe(true);
    });

    it('takes a missing file as final at once', async () => {
        const fetchFn = vi.fn().mockResolvedValue(new Response('gone', {status: 404}));

        const response = await probeMedia(fetchFn, '/media', noSleep);

        expect(response.status).toBe(404);
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });

    it('gives up after a few tries that never get an answer', async () => {
        const fetchFn = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));

        await expect(probeMedia(fetchFn, '/media', noSleep)).rejects.toThrow('Failed to fetch');
        expect(fetchFn).toHaveBeenCalledTimes(3);
    });
});
