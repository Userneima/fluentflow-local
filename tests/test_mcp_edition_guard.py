"""The MCP server refuses to talk to a backend that is not the Local edition.

Both editions of FluentFlow ship these tool names and accept the same JSON, so
the tool call itself cannot tell them apart. Without this guard the hosted
backend answers a local-path submission with a generic 400 that reads like a
version mismatch, and the caller walks away from the product.
"""

from __future__ import annotations

import pytest

from scripts import fluentflow_mcp_server as mcp
from scripts.local_agent_client import FluentFlowApiError

BASE = "http://127.0.0.1:8000"
LOCAL_HEALTH = {"status": "ok", "app_version": "0.3.2", "runtime": {"execution": "local"}}
HOSTED_HEALTH = {"status": "ok", "app_version": "0.3.2", "runtime": {"runtime_os": "Darwin"}}


@pytest.fixture(autouse=True)
def _forget_confirmed_bases():
    mcp._LOCAL_EDITION_CONFIRMED.clear()
    yield
    mcp._LOCAL_EDITION_CONFIRMED.clear()


def _install(monkeypatch, health, *, submit_result=None):
    """Answer /health with ``health`` (or raise it) and record every other call."""
    calls: list[tuple[str, str]] = []

    def fake_api_request(method, api_base, path, **kwargs):
        calls.append((method, path))
        if path == "/health":
            if isinstance(health, Exception):
                raise health
            return health
        return submit_result if submit_result is not None else {"ok": True}

    monkeypatch.setattr(mcp, "api_request", fake_api_request)
    return calls


def test_unreachable_backend_says_how_to_start_it(monkeypatch):
    calls = _install(monkeypatch, FluentFlowApiError("Connection refused"))
    result = mcp.submit_local_media("/Users/me/meeting.mov")
    assert result["ok"] is False
    assert "FluentFlow Local" in result["error"]
    assert [path for _, path in calls] == ["/health"]


def test_hosted_backend_is_refused_before_the_submission(monkeypatch):
    calls = _install(monkeypatch, HOSTED_HEALTH)
    result = mcp.submit_local_media("/Users/me/meeting.mov")
    assert result["ok"] is False
    assert "不是 FluentFlow Local" in result["error"]
    # The point of the guard: the task submission never goes out, so the caller
    # never sees the hosted backend's generic rejection.
    assert [path for _, path in calls] == ["/health"]


def test_local_backend_passes_through(monkeypatch):
    calls = _install(monkeypatch, LOCAL_HEALTH, submit_result={"ok": True, "task_id": "t1"})
    result = mcp.submit_local_media("/Users/me/meeting.mov")
    assert result == {"ok": True, "task_id": "t1"}
    assert calls == [("GET", "/health"), ("POST", "/agent/v1/tasks")]


def test_confirmation_is_probed_once_per_base(monkeypatch):
    calls = _install(monkeypatch, LOCAL_HEALTH)
    mcp.submit_local_media("/Users/me/one.mov")
    mcp.get_task("t1")
    assert [path for _, path in calls].count("/health") == 1


def test_refusal_is_not_cached_so_starting_the_app_unblocks_it(monkeypatch):
    health = {"value": HOSTED_HEALTH}

    def fake_api_request(method, api_base, path, **kwargs):
        if path == "/health":
            return health["value"]
        return {"ok": True, "task_id": "t1"}

    monkeypatch.setattr(mcp, "api_request", fake_api_request)

    assert mcp.submit_local_media("/Users/me/one.mov")["ok"] is False
    health["value"] = LOCAL_HEALTH
    assert mcp.submit_local_media("/Users/me/one.mov") == {"ok": True, "task_id": "t1"}
