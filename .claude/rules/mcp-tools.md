---
paths:
  - scripts/fluentflow_mcp_server.py
  - scripts/local_agent_client.py
  - backend/routers/local_agent.py
  - tests/test_mcp_*.py
---

# Adding or changing an MCP tool

The audit behind these rules: `docs/方案/2026-11-04-MCP工具体检.md`.

- Tools that always come in pairs differing by one argument are one tool with that argument (`list_transcript_corrections` takes `source`).
- An answer that grows with the recording gives a brief by default and a way to read the rest (a section, a page, `include_reverted`). Add such a read to `READS` in `tests/test_mcp_size_budget.py`, which reads a recording as long as the largest real one and fails past 50,000 characters, the length Claude Code still shows inline. A tool that must return more declares `anthropic/maxResultSizeChars`; Codex cuts anything past about 10,000 tokens regardless, so put what the agent needs to read the rest first.
- A refusal carries a `code` and says what to do next. Answers name tools, not HTTP paths, and schemas leave out what an agent never sets (`api_base`).
- The same test caps each tool definition at 3,500 characters; the total is not capped, since both hosts load a tool's definition only when the agent uses it (decision 8.21). Over budget, shorten what grew; raise the number only with the reason beside it and a decision log entry.
