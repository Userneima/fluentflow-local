// @vitest-environment jsdom

// The Agent access page. What the person should get: commands that work as
// copied on this machine, with this checkout's path and the interpreter that
// runs it, and a verify command that exists. A placeholder only appears when
// the service does not say where it lives.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';

let runtimeConfig = {};
vi.mock('../app/AppContext.jsx', () => ({useApp: () => ({runtimeConfig})}));

const {default: LocalAgentAccessPanel, agentAccessCommands, agentConnectionSentence} = await import('./LocalAgentAccessPanel.jsx');
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

    // Checklist 2026-10-22 (lane 3, item 9): on the machine that runs
    // FluentFlow nothing asks for the access token (the MCP server and the
    // check read it from the repository .env), and the page says whether an
    // agent has connected.
    it('asks for no token on this machine and prints commands without one', () => {
        runtimeConfig = normalizeRuntimeConfig({repo_root: HERE.repoRoot, python_executable: HERE.pythonExecutable});
        render(<LocalAgentAccessPanel/>);
        expect(document.getElementById('local-agent-access-token')).toBeNull();
        expect(document.body.textContent).not.toMatch(/FLUENTFLOW_ACCESS_TOKEN/);
    });

    it('says whether an agent can connect and whether one has', () => {
        const now = Date.parse('2026-10-22T12:00:00Z');
        expect(agentConnectionSentence({token_configured: false, last_agent_request_at: null}, now)).toMatch(/还连不上/);
        expect(agentConnectionSentence({token_configured: true, last_agent_request_at: null}, now)).toMatch(/这次启动 FluentFlow 后还没有 agent 调用过；已经接入过的不用重新接/);
        expect(agentConnectionSentence({token_configured: true, last_agent_request_at: now / 1000 - 600}, now)).toBe('这次启动后有 agent 调用过，最近一次在 10 分钟前。');
        expect(agentConnectionSentence(null, now)).toBe('');
    });
});
