// @vitest-environment jsdom

// The Agent access page. What the person should get: commands that work as
// copied on this machine, with this checkout's path and the interpreter that
// runs it, and a verify command that exists. A placeholder only appears when
// the service does not say where it lives.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';

let runtimeConfig = {};
vi.mock('../app/AppContext.jsx', () => ({useApp: () => ({runtimeConfig})}));

const {default: LocalAgentAccessPanel, agentAccessCommands} = await import('./LocalAgentAccessPanel.jsx');
const {normalizeRuntimeConfig} = await import('../lib/sttPolicy.js');

const HERE = {
    apiBase: 'http://127.0.0.1:8000',
    repoRoot: '/Users/me/fluentflow-local',
    pythonExecutable: '/Users/me/fluentflow-local/.venv/bin/python',
};

describe('agentAccessCommands', () => {
    it('fills in the real path and interpreter, with no placeholder and no $(pwd)', () => {
        const out = agentAccessCommands({...HERE, token: 'tok'});
        for (const text of [out.json, out.claude, out.codex, out.verify]) {
            expect(text).not.toMatch(/<path-to-fluentflow>|\$\(pwd\)|<your-local-access-token>/);
        }
        expect(JSON.parse(out.json).mcpServers.fluentflow).toMatchObject({
            command: HERE.pythonExecutable,
            args: ['/Users/me/fluentflow-local/scripts/fluentflow_mcp_server.py'],
        });
        expect(out.claude).toMatch(/^claude mcp add fluentflow /);
        expect(out.claude).toContain('-- /Users/me/fluentflow-local/.venv/bin/python /Users/me/fluentflow-local/scripts/fluentflow_mcp_server.py');
        expect(out.codex).toMatch(/^codex mcp add fluentflow .* -- \/Users\/me\/fluentflow-local\/\.venv\/bin\/python /);
        expect(out.verify).toBe('/Users/me/fluentflow-local/.venv/bin/python /Users/me/fluentflow-local/scripts/check_mcp_server.py --backend-e2e --api-base http://127.0.0.1:8000 --access-token tok');
        expect(out.complete).toBe(true);
    });

    it('quotes a path with spaces so the command still runs', () => {
        const out = agentAccessCommands({...HERE, repoRoot: '/Users/me/My Apps/ff', pythonExecutable: '/Users/me/My Apps/ff/.venv/bin/python'});
        expect(out.verify).toContain("'/Users/me/My Apps/ff/scripts/check_mcp_server.py'");
    });

    it('falls back to the placeholder only when the service does not say', () => {
        const out = agentAccessCommands({apiBase: 'http://127.0.0.1:8000'});
        expect(out.json).toContain('<path-to-fluentflow>');
        expect(out.complete).toBe(false);
    });
});

describe('the page', () => {
    afterEach(cleanup);

    it('reads the path from /runtime-config and never prints the old npm script', () => {
        runtimeConfig = normalizeRuntimeConfig({repo_root: HERE.repoRoot, python_executable: HERE.pythonExecutable});
        render(<LocalAgentAccessPanel/>);
        expect(screen.getByText(/check_mcp_server\.py --backend-e2e/)).toBeTruthy();
        expect(screen.queryByText(/npm run mcp:check:e2e/)).toBeNull();
        expect(document.body.textContent).not.toContain('<path-to-fluentflow>');
    });
});
