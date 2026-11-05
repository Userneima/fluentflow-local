"""One wait_task call holds long enough that waiting costs few turns.

Requirement (2026-11-05, lesson four through Codex): each call the agent makes
resends its whole conversation, and Codex passed timeout_seconds=55 on every
wait of a 3-hour lesson. A wait returns the moment there is something to do,
so holding longer never delays the agent. So:

- a shorter wait asked for is held for the default (240 seconds), still
  inside the 300 seconds Codex gives one call;
- a longer one is held as asked, up to the most the service allows.
"""

from __future__ import annotations

import pytest

from scripts import fluentflow_mcp_server as server


@pytest.fixture
def sent(monkeypatch):
    payloads: list[dict] = []

    def request(method, path, *, payload=None, api_base=None, client_id=None, timeout=60):
        payloads.append(payload)
        return {"ok": True, "done": False}

    monkeypatch.setattr(server, "_agent_request", request)
    monkeypatch.setattr(server, "_watch", lambda *a, **k: None)
    return payloads


@pytest.mark.parametrize("asked,held", [(55, 240), (0, 240), (None, 240), (300, 300), (5000, 600)])
def test_a_wait_holds_at_least_the_default(sent, asked, held):
    server.wait_task("t1", timeout_seconds=asked)
    assert sent[-1]["timeout_seconds"] == held


def test_the_default_is_inside_codexs_wait():
    assert server.WAIT_DEFAULT_SECONDS <= 270
