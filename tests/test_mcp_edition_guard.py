"""The MCP client asks which edition is answering before it needs a specific one.

Both editions serve the same ``/agent/v1`` routes and this client can address
either, so the tools both editions provide stay ungated. The two that need the
Local edition check first, because the hosted rejection they would otherwise get
names the inputs that backend accepts and never says which backend replied.
"""

from __future__ import annotations

import pytest

from scripts import fluentflow_mcp_server as mcp
from scripts.local_agent_client import FluentFlowApiError

LOCAL_HEALTH = {"status": "ok", "edition": "local", "runtime": {"execution": "local"}}
HOSTED_HEALTH = {"status": "ok", "edition": "hosted", "runtime": {"runtime_os": "Darwin"}}
# A backend from before /health declared an edition.
LEGACY_LOCAL_HEALTH = {"status": "ok", "runtime": {"execution": "local"}}
LEGACY_HOSTED_HEALTH = {"status": "ok", "runtime": {"runtime_os": "Darwin"}}


def _install(monkeypatch, health, *, result=None):
    """Answer /health with ``health`` (or raise it) and record every call."""
    calls: list[tuple[str, str]] = []

    def fake_api_request(method, api_base, path, **kwargs):
        calls.append((method, path))
        if path == "/health":
            if isinstance(health, Exception):
                raise health
            return health
        return result if result is not None else {"ok": True}

    monkeypatch.setattr(mcp, "api_request", fake_api_request)
    return calls


def test_unreachable_backend_says_how_to_start_it(monkeypatch):
    _install(monkeypatch, FluentFlowApiError("Connection refused"))
    result = mcp.submit_local_media("/Users/me/meeting.mov")
    assert result["ok"] is False
    assert "FluentFlow Local" in result["error"] and "启动" in result["error"]


def test_hosted_backend_is_refused_before_the_submission(monkeypatch):
    calls = _install(monkeypatch, HOSTED_HEALTH)
    result = mcp.submit_local_media("/Users/me/meeting.mov")
    assert result["ok"] is False
    assert "FluentFlow Hosted" in result["error"]
    # The submission never goes out, so the caller never has to interpret the
    # hosted backend's intake rejection.
    assert [path for _, path in calls] == ["/health"]


def test_local_backend_passes_through(monkeypatch):
    calls = _install(monkeypatch, LOCAL_HEALTH, result={"ok": True, "task_id": "t1"})
    assert mcp.submit_local_media("/Users/me/meeting.mov") == {"ok": True, "task_id": "t1"}
    assert calls == [("GET", "/health"), ("POST", "/agent/v1/tasks")]


def test_cut_media_note_is_gated_too(monkeypatch):
    calls = _install(monkeypatch, HOSTED_HEALTH)
    result = mcp.write_note_from_cut_media("t1")
    assert result["ok"] is False
    assert [path for _, path in calls] == ["/health"]


def test_shared_tools_are_not_gated(monkeypatch):
    # Ten of the twelve tools work on either edition; gating them would break the
    # documented design where FLUENTFLOW_API_BASE picks the backend.
    calls = _install(monkeypatch, HOSTED_HEALTH, result={"ok": True, "task": {}})
    assert mcp.get_task("t1") == {"ok": True, "task": {}}
    assert [path for _, path in calls] == ["/agent/v1/tasks/t1"]


@pytest.mark.parametrize(
    "health,expected_ok",
    [(LEGACY_LOCAL_HEALTH, True), (LEGACY_HOSTED_HEALTH, False)],
)
def test_backend_without_declared_edition_falls_back_to_runtime(monkeypatch, health, expected_ok):
    _install(monkeypatch, health, result={"ok": True, "task_id": "t1"})
    assert mcp.submit_local_media("/Users/me/one.mov")["ok"] is expected_ok


def test_identity_is_reread_every_time(monkeypatch):
    # The case this guard exists for is a backend swapped on a port, so a cached
    # identity would be wrong exactly when it matters.
    health = {"value": HOSTED_HEALTH}

    def fake_api_request(method, api_base, path, **kwargs):
        if path == "/health":
            return health["value"]
        return {"ok": True, "task_id": "t1"}

    monkeypatch.setattr(mcp, "api_request", fake_api_request)
    assert mcp.submit_local_media("/Users/me/one.mov")["ok"] is False
    health["value"] = LOCAL_HEALTH
    assert mcp.submit_local_media("/Users/me/one.mov") == {"ok": True, "task_id": "t1"}
