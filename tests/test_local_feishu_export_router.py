from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_feishu_export as local_feishu_export
from backend.routers.local_feishu_export import _local_lark_export_target, router

from fastapi import HTTPException
import pytest


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _silence_telemetry(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr(local_feishu_export, "log_event", lambda **values: events.append(values))
    return events


# ---- target resolution -------------------------------------------------------

def test_default_target_is_openapi():
    assert _local_lark_export_target(None, None) == "lark_openapi"
    assert _local_lark_export_target("", "") == "lark_openapi"


def test_explicit_routes_resolve():
    assert _local_lark_export_target("lark_cli", None) == "lark_cli"
    assert _local_lark_export_target("local_cli", None) == "lark_cli"
    assert _local_lark_export_target("openapi", None) == "lark_openapi"
    assert _local_lark_export_target(None, "true") == "lark_cli"


def test_hosted_oauth_route_is_rejected():
    for route in ("user_oauth", "feishu_user", "feishu_user_oauth", "lark_user_oauth"):
        with pytest.raises(HTTPException) as excinfo:
            _local_lark_export_target(route, None)
        assert excinfo.value.status_code == 400


# ---- openapi export ----------------------------------------------------------

def test_openapi_export_uses_local_credentials(monkeypatch, tmp_path):
    events = _silence_telemetry(monkeypatch)
    stored = {"lark_app_id": "cli_stored_id", "lark_app_secret": "stored_secret"}
    monkeypatch.setattr(
        local_feishu_export, "resolve_secret",
        lambda form_value, name: (form_value or "").strip() or stored.get(name),
    )
    monkeypatch.setattr(local_feishu_export, "_artifact_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(
        local_feishu_export,
        "get_job",
        lambda task_id, client_id=None: {
            "task_id": task_id,
            "client_id": client_id,
            "status": "completed",
        } if task_id == "t-9" else None,
    )
    calls: list[dict] = []

    def fake_export(title, markdown, **kwargs):
        calls.append({"title": title, "markdown": markdown, **kwargs})
        return {"ok": True, "url": "https://example.feishu.cn/docx/abc"}

    monkeypatch.setattr(local_feishu_export, "export_markdown_to_lark", fake_export)

    r = _client().post(
        "/export-lark",
        data={"markdown": "# 我的笔记\n\n内容", "folder_token": "fld123", "task_id": "t-9"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "https://example.feishu.cn/docx/abc"
    assert body["doc_title"] == "我的笔记"
    assert body["task_id"] == "t-9"
    assert calls[0]["app_id"] == "cli_stored_id"
    assert calls[0]["app_secret"] == "stored_secret"
    assert calls[0]["folder_token"] == "fld123"
    assert calls[0]["artifact_root"] == tmp_path
    started, completed = events[0], events[1]
    assert started["event_name"] == "lark_export_started"
    assert completed["event_name"] == "lark_export_completed"
    assert completed["success"] is True
    assert completed["export_target"] == "lark_openapi"
    assert completed["feishu_doc_url"] == "https://example.feishu.cn/docx/abc"


def test_export_rejects_malformed_task_id_before_reading_artifacts(monkeypatch):
    _silence_telemetry(monkeypatch)
    called = []
    monkeypatch.setattr(
        local_feishu_export,
        "export_markdown_to_lark",
        lambda *args, **kwargs: called.append(kwargs) or {"ok": True},
    )

    response = _client().post(
        "/export-lark",
        data={
            "markdown": "![private](/jobs/../private/artifacts/frame?file=private.png)",
            "task_id": "../private",
        },
    )

    assert response.status_code == 400
    assert called == []


def test_export_of_deleted_task_continues_under_fresh_id(monkeypatch, tmp_path):
    """Hosted parity: a stale task_id (job deleted/pruned) must not block a
    manual export — the markdown is in the request; the export runs under a
    fresh id so no artifact images resolve."""
    _silence_telemetry(monkeypatch)
    monkeypatch.setattr(local_feishu_export, "get_job", lambda task_id, client_id=None: None)
    monkeypatch.setattr(local_feishu_export, "_artifact_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(local_feishu_export, "resolve_secret", lambda form_value, name: "x")
    seen: dict = {}
    monkeypatch.setattr(
        local_feishu_export, "export_markdown_to_lark",
        lambda title, markdown, **kwargs: seen.update(kwargs) or {"ok": True, "url": "u"},
    )

    r = _client().post("/export-lark", data={"markdown": "# 笔记", "task_id": "gone-1"})

    assert r.status_code == 200
    assert r.json()["task_id"] != "gone-1"  # fresh id, export not blocked
    assert seen["task_id"] != "gone-1"


def test_export_with_foreign_task_id_is_rejected(monkeypatch):
    """Another client's task id must not resolve their artifacts into this
    caller's document."""
    _silence_telemetry(monkeypatch)

    def foreign_get(task_id, client_id=None):
        if client_id is None:
            return {"task_id": task_id, "client_id": "desktop-b"}
        return None  # not owned by the caller

    monkeypatch.setattr(local_feishu_export, "get_job", foreign_get)
    called = []
    monkeypatch.setattr(
        local_feishu_export, "export_markdown_to_lark",
        lambda *args, **kwargs: called.append(kwargs) or {"ok": True},
    )

    r = _client().post(
        "/export-lark",
        headers={"x-fluentflow-client-id": "desktop-a"},
        data={"markdown": "x", "task_id": "their-task"},
    )

    assert r.status_code == 404
    assert called == []


def test_form_credentials_override_stored(monkeypatch, tmp_path):
    _silence_telemetry(monkeypatch)
    monkeypatch.setattr(
        local_feishu_export, "resolve_secret",
        lambda form_value, name: (form_value or "").strip() or "stored",
    )
    monkeypatch.setattr(local_feishu_export, "_artifact_storage_dir", lambda: tmp_path)
    seen: dict = {}
    monkeypatch.setattr(
        local_feishu_export, "export_markdown_to_lark",
        lambda title, markdown, **kwargs: seen.update(kwargs) or {"ok": True, "url": "u"},
    )

    r = _client().post(
        "/export-lark",
        data={"markdown": "x", "lark_app_id": "form_id", "lark_app_secret": "form_secret"},
    )

    assert r.status_code == 200
    assert seen["app_id"] == "form_id"
    assert seen["app_secret"] == "form_secret"


# ---- lark-cli export ---------------------------------------------------------

def test_cli_export_route(monkeypatch):
    events = _silence_telemetry(monkeypatch)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        local_feishu_export, "export_markdown_via_lark_cli",
        lambda title, markdown: calls.append((title, markdown))
        or {"ok": True, "url": "https://example.feishu.cn/wiki/w1", "via": "lark_cli"},
    )

    r = _client().post(
        "/export-lark",
        data={"markdown": "内容", "title": "标题", "lark_via_cli": "1"},
    )

    assert r.status_code == 200
    assert r.json()["via"] == "lark_cli"
    assert calls == [("标题", "内容")]
    assert events[-1]["export_target"] == "lark_cli"


def test_hosted_oauth_request_returns_400(monkeypatch):
    events = _silence_telemetry(monkeypatch)
    r = _client().post(
        "/export-lark",
        data={"markdown": "x", "lark_export_route": "feishu_user_oauth"},
    )
    assert r.status_code == 400
    assert "OAuth" in r.json()["detail"]
    assert events == []  # rejected before any export started


# ---- failure path ------------------------------------------------------------

def test_export_failure_returns_friendly_500(monkeypatch):
    events = _silence_telemetry(monkeypatch)

    def boom(title, markdown):
        raise RuntimeError("lark-cli not found. Install @larksuite/cli globally.")

    monkeypatch.setattr(local_feishu_export, "export_markdown_via_lark_cli", boom)

    r = _client().post("/export-lark", data={"markdown": "x", "lark_via_cli": "yes"})

    assert r.status_code == 500
    assert r.json()["detail"].strip()
    names = [event["event_name"] for event in events]
    assert names == ["lark_export_started", "lark_export_completed", "task_failed"]
    assert events[1]["success"] is False
