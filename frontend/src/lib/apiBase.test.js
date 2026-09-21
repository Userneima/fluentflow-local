import { describe, expect, it } from 'vitest';
import { resolveApiBase } from './apiBase.js';

describe('resolveApiBase', () => {
    it('uses a relative path when the backend served the page', () => {
        expect(resolveApiBase({hostname: '127.0.0.1', port: '8000'})).toBe('');
    });

    it('stays relative on a port the user chose', () => {
        // FLUENTFLOW_LOCAL_PORT is documented and the launchers honour it. This
        // used to return the 8000 backend, so a user on another port watched
        // their page render another instance's tasks without an error anywhere.
        expect(resolveApiBase({hostname: '127.0.0.1', port: '8080'})).toBe('');
        expect(resolveApiBase({hostname: 'localhost', port: '8001'})).toBe('');
    });

    it('reaches across to the backend from the Vite dev server', () => {
        expect(resolveApiBase({hostname: 'localhost', port: '5186'})).toBe('http://127.0.0.1:8000');
        expect(resolveApiBase({hostname: '127.0.0.1', port: '5173'})).toBe('http://127.0.0.1:8000');
    });

    it('lets an explicit setting win over every guess', () => {
        expect(resolveApiBase({hostname: 'localhost', port: '5186'}, 'https://example.test/'))
            .toBe('https://example.test');
    });

    it('is relative when served from a real host', () => {
        expect(resolveApiBase({hostname: 'notes.example.com', port: ''})).toBe('');
    });
});
