"""Local Agent API surface: token gate, task lifecycle, local package."""

from __future__ import annotations

import functools
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.local_entry_guards as guards
from backend.core import job_store
import backend.routers.local_agent as local_agent
import backend.routers.local_video_sources as local_video_sources
from backend.routers.local_agent import router as agent_router
from backend.routers.local_video_sources import router as video_router

_TOKEN = "local-secret-token"
_HEADERS = {
    "x-fluentflow-client-id": "desktop-a",
    "x-fluentflow-access-token": _TOKEN,
}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent_router)
    app.include_router(video_router)
    return app


@pytest.fixture()
def agent_stack(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path / "data"))
    jobs_db = tmp_path / "jobs.sqlite"
    for module in (local_agent, local_video_sources, guards):
        monkeypatch.setattr(
            module, "upsert_job", functools.partial(job_store.upsert_job, db_path=jobs_db)
        )
        monkeypatch.setattr(
            module, "get_job", functools.partial(job_store.get_job, db_path=jobs_db),
            raising=False,
        )
    monkeypatch.setattr(
        local_agent,
        "append_job_result_list_item",
        functools.partial(job_store.append_job_result_list_item, db_path=jobs_db),
    )
    monkeypatch.setattr(
        local_agent,
        "finalize_job_result_if_unchanged",
        functools.partial(job_store.finalize_job_result_if_unchanged, db_path=jobs_db),
    )
    monkeypatch.setattr(
        guards, "create_job_if_absent",
        functools.partial(job_store.create_job_if_absent, db_path=jobs_db),
    )
    monkeypatch.setattr(
        local_video_sources, "list_jobs",
        functools.partial(job_store.list_jobs, db_path=jobs_db),
    )
    telemetry: list[dict] = []
    for module in (local_agent, local_video_sources, guards):
        monkeypatch.setattr(module, "log_event", lambda **values: telemetry.append(values))
    monkeypatch.setattr(local_agent, "_attach_result_artifacts", lambda task_id, result: result)
    return {"jobs_db": jobs_db, "telemetry": telemetry}


def _summary_result(markdown: str = "# Agent 笔记") -> SimpleNamespace:
    return SimpleNamespace(
        markdown=markdown, requested_mode="auto", resolved_mode="direct", chunk_count=1
    )


# ---- token gate ----------------------------------------------------------------

def test_agent_api_disabled_without_configured_token(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_ACCESS_TOKEN", raising=False)
    r = TestClient(_app()).get("/tasks/t-1", headers=_HEADERS)  # not under /agent
    r = TestClient(_app()).get("/agent/v1/tasks/t-1", headers=_HEADERS)
    assert r.status_code == 403
    assert "未启用" in r.json()["detail"]


def test_agent_api_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    r = TestClient(_app()).get(
        "/agent/v1/tasks/t-1",
        headers={**_HEADERS, "x-fluentflow-access-token": "wrong"},
    )
    assert r.status_code == 401


def test_agent_api_accepts_bearer_token(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    r = TestClient(_app()).get(
        "/agent/v1/tasks/t-1",
        headers={
            "authorization": f"Bearer {_TOKEN}",
            "x-fluentflow-client-id": "desktop-a",
        },
    )
    assert r.status_code == 404


# ---- transcript task ------------------------------------------------------------

def test_transcript_task_completes_with_local_package(monkeypatch, agent_stack):
    monkeypatch.setattr(
        local_agent, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result(),
    )
    client = TestClient(_app())

    r = client.post(
        "/agent/v1/tasks",
        headers=_HEADERS,
        json={"transcript_text": "大家好，本地版。", "title": "演讲"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    package = body["package"]
    assert package["note"]["markdown"] == "# Agent 笔记"
    assert "usage" not in package and "execution" not in package  # local package

    # Follow-up reads work through the same surface.
    task_id = body["task_id"]
    got = client.get(f"/agent/v1/tasks/{task_id}", headers=_HEADERS)
    assert got.json()["task"]["status"] == "completed"
    pkg = client.get(f"/agent/v1/tasks/{task_id}/package", headers=_HEADERS)
    assert pkg.json()["note"]["status"] == "completed"
    diag = client.get(f"/agent/v1/tasks/{task_id}/diagnosis", headers=_HEADERS)
    assert diag.json()["note"]["code"] == "note_completed"
    waited = client.post(f"/agent/v1/tasks/{task_id}/wait", headers=_HEADERS, json={})
    assert waited.json()["done"] is True


def test_transcript_task_uses_strict_local_keys(monkeypatch, agent_stack):
    monkeypatch.setattr(
        guards, "resolve_secret",
        lambda form_value, name: (form_value or "").strip()
        or ("ds-secret" if name == "deepseek_api_key" else None),
    )
    seen: dict = {}
    monkeypatch.setattr(
        local_agent, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: seen.update(kwargs) or _summary_result(),
    )
    r = TestClient(_app()).post(
        "/agent/v1/tasks",
        headers=_HEADERS,
        json={"transcript_text": "文本", "ai_provider": "qwen"},
    )
    assert r.status_code == 200
    assert seen["provider"] == "qwen"
    assert "api_key" not in seen  # deepseek key must not leak to qwen


def test_transcript_task_rejects_duplicate_task_id(monkeypatch, agent_stack):
    job_store.upsert_job(
        task_id="taken-1", status="completed", client_id="desktop-a",
        db_path=agent_stack["jobs_db"],
    )
    r = TestClient(_app()).post(
        "/agent/v1/tasks",
        headers=_HEADERS,
        json={"transcript_text": "文本", "task_id": "taken-1"},
    )
    assert r.status_code == 409


def test_transcript_task_finalize_failure_reaches_failed_state(monkeypatch, agent_stack):
    monkeypatch.setattr(
        local_agent, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result(),
    )
    monkeypatch.setattr(
        local_agent,
        "_attach_result_artifacts",
        lambda task_id, result: (_ for _ in ()).throw(OSError("disk full")),
    )
    client = TestClient(_app(), raise_server_exceptions=False)

    r = client.post(
        "/agent/v1/tasks",
        headers=_HEADERS,
        json={"transcript_text": "需要持久化的文字稿"},
    )

    assert r.status_code == 500
    jobs = job_store.list_jobs(db_path=agent_stack["jobs_db"])
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["stage"] == "finalize"


# ---- video link task -------------------------------------------------------------

def test_video_link_task_starts_and_stays_out_of_video_sources_list(monkeypatch, agent_stack):
    submitted: dict = {}

    async def fake_submit(**kwargs):
        submitted.update(kwargs)
        job_store.upsert_job(
            task_id="agent-vid-1", status="queued", client_id=kwargs["client_id"],
            stage="queued", progress=0,
            metadata={"route": kwargs["route"], "agent_input_type": "video_link"},
            db_path=agent_stack["jobs_db"],
        )
        return {"task_id": "agent-vid-1", "status": "queued", "stage": "queued"}

    monkeypatch.setattr(local_agent, "submit_video_source_job", fake_submit)
    client = TestClient(_app())

    r = client.post(
        "/agent/v1/tasks",
        headers=_HEADERS,
        json={"input": "https://example.com/v/1", "options": {"note_mode": "direct"}},
    )

    assert r.status_code == 200
    assert r.json()["task_id"] == "agent-vid-1"
    assert submitted["route"] == "/agent/v1/tasks"
    assert submitted["extra_metadata"] == {"agent_input_type": "video_link"}

    # Agent-submitted jobs are not mixed into the manual video-sources list.
    listed = client.get("/video-sources/jobs", headers=_HEADERS)
    assert listed.json()["jobs"] == []


def test_video_link_worker_keeps_agent_route_identity(monkeypatch, agent_stack):
    seen: dict = {}

    async def fake_worker(**kwargs):
        seen.update(kwargs)
        job_store.upsert_job(
            task_id=kwargs["task_id"],
            status="completed",
            client_id=kwargs["client_id"],
            stage="done",
            progress=100,
            metadata={"route": kwargs["route"], "agent_input_type": "video_link"},
            db_path=agent_stack["jobs_db"],
        )

    async def run_now(task_id, runner):
        await runner()

    monkeypatch.setattr(local_video_sources, "_run_local_video_source_job", fake_worker)
    monkeypatch.setattr(local_video_sources.JOB_EVENTS, "start", run_now)

    r = TestClient(_app()).post(
        "/agent/v1/tasks",
        headers=_HEADERS,
        json={"input": "https://example.com/v/agent-route"},
    )

    assert r.status_code == 200
    assert seen["route"] == "/agent/v1/tasks"
    listed = TestClient(_app()).get("/video-sources/jobs", headers=_HEADERS)
    assert listed.json()["jobs"] == []


def test_cross_client_task_is_not_visible(agent_stack):
    job_store.upsert_job(
        task_id="their-1", status="completed", client_id="desktop-b",
        db_path=agent_stack["jobs_db"],
    )
    r = TestClient(_app()).get("/agent/v1/tasks/their-1", headers=_HEADERS)
    assert r.status_code == 404


# ---- regenerate + export ----------------------------------------------------------

def test_note_regenerate_updates_result(monkeypatch, agent_stack):
    job_store.upsert_job(
        task_id="t-r1", status="completed", client_id="desktop-a", stage="done",
        result={"task_id": "t-r1", "transcript_text": "旧的文字", "summary_markdown": "旧"},
        db_path=agent_stack["jobs_db"],
    )
    monkeypatch.setattr(
        local_agent, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result("# 新笔记"),
    )
    r = TestClient(_app()).post("/agent/v1/tasks/t-r1/note/regenerate", headers=_HEADERS, json={})
    assert r.status_code == 200
    assert r.json()["package"]["note"]["markdown"] == "# 新笔记"
    stored = job_store.get_job("t-r1", db_path=agent_stack["jobs_db"], client_id="desktop-a")
    assert stored["result"]["summary_markdown"] == "# 新笔记"


def test_note_regenerate_without_transcript_is_400(agent_stack):
    job_store.upsert_job(
        task_id="t-r2", status="completed", client_id="desktop-a",
        result={"task_id": "t-r2"}, db_path=agent_stack["jobs_db"],
    )
    r = TestClient(_app()).post("/agent/v1/tasks/t-r2/note/regenerate", headers=_HEADERS, json={})
    assert r.status_code == 400


def test_note_regenerate_rejects_concurrent_edit(monkeypatch, agent_stack):
    job_store.upsert_job(
        task_id="t-r3", status="completed", client_id="desktop-a", stage="done",
        result={"task_id": "t-r3", "transcript_text": "旧的文字", "summary_markdown": "旧"},
        db_path=agent_stack["jobs_db"],
    )

    def summarize(transcript, **kwargs):
        job = job_store.get_job(
            "t-r3", db_path=agent_stack["jobs_db"], client_id="desktop-a"
        )
        edited = dict(job["result"])
        edited["summary_markdown"] = "用户生成期间的编辑"
        job_store.upsert_job(
            task_id="t-r3", status="completed", client_id="desktop-a",
            result=edited, db_path=agent_stack["jobs_db"],
        )
        return _summary_result("# AI 新稿")

    monkeypatch.setattr(local_agent, "summarize_transcript_with_metadata", summarize)
    r = TestClient(_app()).post(
        "/agent/v1/tasks/t-r3/note/regenerate", headers=_HEADERS, json={}
    )

    assert r.status_code == 409
    stored = job_store.get_job(
        "t-r3", db_path=agent_stack["jobs_db"], client_id="desktop-a"
    )
    assert stored["result"]["summary_markdown"] == "用户生成期间的编辑"


def test_note_regenerate_rejects_edit_saved_after_conflict_check(monkeypatch, agent_stack):
    job_store.upsert_job(
        task_id="t-r4", status="completed", client_id="desktop-a", stage="done",
        result={"task_id": "t-r4", "transcript_text": "旧的文字", "summary_markdown": "旧"},
        db_path=agent_stack["jobs_db"],
    )
    monkeypatch.setattr(
        local_agent,
        "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result("# AI 新稿"),
    )

    def edit_after_check(task_id, result):
        job = job_store.get_job(
            task_id, db_path=agent_stack["jobs_db"], client_id="desktop-a"
        )
        edited = dict(job["result"])
        edited["summary_markdown"] = "用户在检查后保存的编辑"
        job_store.upsert_job(
            task_id=task_id, status="completed", client_id="desktop-a",
            result=edited, db_path=agent_stack["jobs_db"],
        )
        return result

    monkeypatch.setattr(local_agent, "_attach_result_artifacts", edit_after_check)
    r = TestClient(_app()).post(
        "/agent/v1/tasks/t-r4/note/regenerate", headers=_HEADERS, json={}
    )

    assert r.status_code == 409
    stored = job_store.get_job(
        "t-r4", db_path=agent_stack["jobs_db"], client_id="desktop-a"
    )
    assert stored["result"]["summary_markdown"] == "用户在检查后保存的编辑"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", "abc"),
        ("timeout_seconds", "nan"),
        ("poll_interval_seconds", "abc"),
        ("poll_interval_seconds", "inf"),
    ],
)
def test_wait_rejects_non_numeric_or_non_finite_timing(field, value, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    response = TestClient(_app(), raise_server_exceptions=False).post(
        "/agent/v1/tasks/any/wait",
        headers=_HEADERS,
        json={field: value},
    )

    assert response.status_code == 422
    assert field in response.json()["detail"]


def test_export_records_export_and_rejects_hosted_oauth(monkeypatch, agent_stack):
    job_store.upsert_job(
        task_id="t-e1", status="completed", client_id="desktop-a",
        result={"task_id": "t-e1", "summary_markdown": "# 笔记"},
        db_path=agent_stack["jobs_db"],
    )
    monkeypatch.setattr(
        local_agent, "export_markdown_via_lark_cli",
        lambda title, markdown: {"ok": True, "url": "https://example.feishu.cn/wiki/w1", "via": "lark_cli"},
    )
    client = TestClient(_app())

    r = client.post(
        "/agent/v1/tasks/t-e1/exports",
        headers=_HEADERS,
        json={"lark_via_cli": "1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["export"]["route"] == "lark_cli"
    assert body["export"]["url"] == "https://example.feishu.cn/wiki/w1"
    stored = job_store.get_job("t-e1", db_path=agent_stack["jobs_db"], client_id="desktop-a")
    assert stored["result"]["exports"][0]["url"] == "https://example.feishu.cn/wiki/w1"

    oauth = client.post(
        "/agent/v1/tasks/t-e1/exports",
        headers=_HEADERS,
        json={"lark_export_route": "feishu_user_oauth"},
    )
    assert oauth.status_code == 400


def test_export_preserves_edit_saved_during_remote_call(monkeypatch, agent_stack):
    job_store.upsert_job(
        task_id="t-e2", status="completed", client_id="desktop-a", stage="done",
        result={"task_id": "t-e2", "summary_markdown": "导出前旧稿"},
        db_path=agent_stack["jobs_db"],
    )

    def export(title, markdown):
        job = job_store.get_job(
            "t-e2", db_path=agent_stack["jobs_db"], client_id="desktop-a"
        )
        edited = dict(job["result"])
        edited["summary_markdown"] = "用户导出期间的编辑"
        job_store.upsert_job(
            task_id="t-e2", status="completed", client_id="desktop-a",
            result=edited, db_path=agent_stack["jobs_db"],
        )
        return {"ok": True, "url": "https://example.feishu.cn/wiki/w2"}

    monkeypatch.setattr(local_agent, "export_markdown_via_lark_cli", export)
    r = TestClient(_app()).post(
        "/agent/v1/tasks/t-e2/exports",
        headers=_HEADERS,
        json={"lark_via_cli": "1"},
    )

    assert r.status_code == 200
    stored = job_store.get_job(
        "t-e2", db_path=agent_stack["jobs_db"], client_id="desktop-a"
    )
    assert stored["result"]["summary_markdown"] == "用户导出期间的编辑"
    assert stored["result"]["exports"][0]["url"] == "https://example.feishu.cn/wiki/w2"


# ---- a recording that stays where it is ----------------------------------------

def test_a_path_is_refused_when_the_request_is_not_from_this_machine(agent_stack, monkeypatch):
    """A server that accepts a filesystem path reads its own disk for whoever asks.

    The page entry has always been localhost-only and this entry cannot be looser.
    TestClient reads as localhost, so the non-local case has to be asked for.
    """
    monkeypatch.setattr(local_agent, "request_is_localhost", lambda _request: False)

    r = TestClient(_app()).post(
        "/agent/v1/tasks", headers=_HEADERS,
        json={"path": "/etc/passwd", "input_type": "local_path"},
    )

    assert r.status_code == 403
    assert "只有本机" in r.json()["detail"]


def test_a_path_that_is_not_a_readable_recording_is_refused_before_a_task_exists(
    agent_stack, monkeypatch, tmp_path
):
    """Refused up front, so an unusable path leaves no queued task that can never run."""
    monkeypatch.setattr(local_agent, "request_is_localhost", lambda _request: True)

    r = TestClient(_app()).post(
        "/agent/v1/tasks", headers=_HEADERS,
        json={"path": str(tmp_path / "not-here.m4a"), "input_type": "local_path"},
    )

    assert r.status_code == 400


def test_a_local_path_is_queued_through_the_page_entry_helper(agent_stack, monkeypatch, tmp_path):
    """The same helper the file dialog uses, not a parallel copy of it.

    Preflight, the task row, ownership and the terminal-state guarantee live in that
    helper; a second implementation is how one entry ends up missing a guard the
    other one has.
    """
    media = tmp_path / "talk.m4a"
    media.write_bytes(b"x")
    seen: dict = {}

    async def _queued(source, **kwargs):
        seen.update(source=source, **kwargs)
        return {"task_id": "t-local-1", "status": "queued"}

    monkeypatch.setattr(local_agent, "request_is_localhost", lambda _request: True)
    monkeypatch.setattr(local_agent.local_folder_intake, "resolve_media_file", lambda raw: media)
    monkeypatch.setattr(local_agent, "queue_local_media_file", _queued)

    r = TestClient(_app()).post(
        "/agent/v1/tasks", headers=_HEADERS,
        json={"path": str(media), "input_type": "local_path", "title": "Talk"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "t-local-1"
    assert body["package_url"] == "/agent/v1/tasks/t-local-1/package"
    assert seen["source"] == media
    assert seen["origin"]["chosen_with"] == "agent_api"


def test_empty_submission_names_this_edition(agent_stack) -> None:
    # An input list alone reads like a missing capability rather than a malformed
    # call, and both editions used to answer with the same sentence (2026-09-15).
    client = TestClient(_app())

    response = client.post("/agent/v1/tasks", json={}, headers=_HEADERS)

    assert response.status_code == 400
    assert "FluentFlow Local" in response.json()["detail"]
