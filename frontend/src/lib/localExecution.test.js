// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { localExecutionHeaders, shouldUseLocalExecution, LOCAL_EXECUTION_HEADER } from './localExecution.js';

// Guards the local-execution auth exemption. Regression origin: switching to
// cloud transcription dropped the X-FluentFlow-Execution-Target header, so
// localhost single-user uploads got 401 and the record vanished (commit
// a86cecf / 50067ff). The rule: on localhost EVERY request is local execution
// regardless of STT provider; on a real host it depends on the request.

const setHostname = (hostname) => {
    Object.defineProperty(window, 'location', {
        value: { hostname },
        configurable: true,
    });
};

afterEach(() => {
    setHostname('localhost');
});

describe('localExecutionHeaders on localhost single-user', () => {
    it('marks LOCAL transcription as local execution', () => {
        setHostname('localhost');
        expect(localExecutionHeaders({ sttProvider: 'local' })[LOCAL_EXECUTION_HEADER]).toBe('local');
    });

    it('ALSO marks CLOUD transcription as local execution (the 401 fix)', () => {
        setHostname('127.0.0.1');
        expect(localExecutionHeaders({ sttProvider: 'cloud' })[LOCAL_EXECUTION_HEADER]).toBe('local');
        expect(shouldUseLocalExecution({ sttProvider: 'cloud' })).toBe(true);
    });
});

describe('localExecutionHeaders on a real (non-localhost) host', () => {
    it('does NOT exempt cloud transcription (public deploy still needs account auth)', () => {
        setHostname('app.example.com');
        expect(localExecutionHeaders({ sttProvider: 'cloud' })).toEqual({});
        expect(shouldUseLocalExecution({ sttProvider: 'cloud' })).toBe(false);
    });

    it('still marks local transcription and explicit localExecution as local', () => {
        setHostname('app.example.com');
        expect(localExecutionHeaders({ sttProvider: 'local' })[LOCAL_EXECUTION_HEADER]).toBe('local');
        expect(localExecutionHeaders({ localExecution: true })[LOCAL_EXECUTION_HEADER]).toBe('local');
    });
});
