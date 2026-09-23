"""Local composition root: route contract, HTTP boundary, single-owner proof,
and stale-job recovery."""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.core import job_store
from backend.core.local_request_scope import LOCAL_OWNER_ID
import backend.local_main as local_main
import backend.routers.local_job_edit as local_job_edit
import backend.routers.local_job_mutation as local_job_mutation
import backend.routers.local_jobs as local_jobs
from backend.local_main import create_local_app, recover_stale_jobs

_REQUIRED_ROUTE_PREFIXES = (
    "/health", "/version", "/credentials", "/runtime-config",
    "/speaker-diarization", "/jobs", "/video-sources", "/queue/process",
    "/process", "/export-lark", "/regenerate-summary",
    "/summarize-transcript-file", "/agent/v1",
)
_FORBIDDEN_ROUTE_PREFIXES = (
    "/admin", "/auth", "/account", "/guest-trial", "/oss-upload-sessions",
    "/desktop-sync",
)


def _app_paths() -> set[str]:
    return {route.path for route in create_local_app().routes if isinstance(route, APIRoute)}


# ---- route contract --------------------------------------------------------------

def test_every_required_route_prefix_is_served():
    paths = _app_paths()
    for prefix in _REQUIRED_ROUTE_PREFIXES:
        assert any(
            path == prefix or path.startswith(prefix + "/") for path in paths
        ), f"required local route family missing: {prefix}"


def test_no_forbidden_route_prefix_is_served():
    paths = _app_paths()
    for prefix in _FORBIDDEN_ROUTE_PREFIXES:
        offenders = [p for p in paths if p == prefix or p.startswith(prefix + "/")]
        assert offenders == [], f"forbidden route family present: {offenders}"


# ---- HTTP boundary ----------------------------------------------------------------

def _get_as_peer(peer: str, path: str = "/health") -> httpx.Response:
    import asyncio

    async def scenario():
        transport = httpx.ASGITransport(app=create_local_app(), client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://local") as client:
            return await client.get(path)

    return asyncio.run(scenario())


def test_non_loopback_peer_is_rejected(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", raising=False)
    r = _get_as_peer("203.0.113.5")
    assert r.status_code == 403
    assert "本机" in r.json()["detail"]


def test_foreign_browser_origin_is_rejected(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", raising=False)
    client = TestClient(create_local_app())
    r = client.get("/health", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.get("/health", headers={"origin": "http://localhost:5173"})
    assert ok.status_code == 200


def test_null_origin_is_rejected():
    r = TestClient(create_local_app()).get("/health", headers={"origin": "null"})
    assert r.status_code == 403


def test_loopback_requests_pass():
    r = TestClient(create_local_app()).get("/health")
    assert r.status_code == 200


def test_non_loopback_override_env(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    assert _get_as_peer("203.0.113.5").status_code == 200


# ---- one owner through the assembled app ------------------------------------------

@pytest.fixture()
def scoped_stack(monkeypatch, tmp_path):
    """Real tmp SQLite behind the assembled app's job routers."""
    jobs_db = tmp_path / "jobs.sqlite"
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path / "data"))
    for module in (local_jobs, local_job_mutation, local_job_edit):
        monkeypatch.setattr(
            module, "get_job", functools.partial(job_store.get_job, db_path=jobs_db),
            raising=False,
        )
    monkeypatch.setattr(
        local_job_mutation, "upsert_job", functools.partial(job_store.upsert_job, db_path=jobs_db)
    )
    monkeypatch.setattr(
        local_job_mutation, "cancel_job_steps",
        functools.partial(job_store.cancel_job_steps, db_path=jobs_db),
    )
    monkeypatch.setattr(
        local_job_edit, "update_job_result",
        functools.partial(job_store.update_job_result, db_path=jobs_db),
        raising=False,
    )
    monkeypatch.setattr(local_job_mutation, "log_event", lambda **values: None)
    job_store.upsert_job(
        task_id="b-task",
        status="running",
        client_id=LOCAL_OWNER_ID,
        stage="stt",
        progress=40,
        result={"task_id": "b-task", "transcript_text": "B 的内容"},
        db_path=jobs_db,
    )
    return jobs_db


def test_any_client_id_reads_and_mutates_the_same_task(scoped_stack):
    # The page, the MCP server and scripts each send their own client id (or
    # none). Local has one user, so a task filed by one caller must be visible
    # and editable from every other.
    client = TestClient(create_local_app())
    a = {"x-fluentflow-client-id": "desktop-a"}
    b = {"x-fluentflow-client-id": "desktop-b"}

    for headers in (a, b, {}):
        response = client.get("/jobs/b-task", headers=headers)
        assert response.status_code == 200
        assert response.json()["result"]["transcript_text"] == "B 的内容"

    edited = client.patch(
        "/jobs/b-task/transcript", headers=a, json={"transcript_text": "A 改过的内容"}
    )
    assert edited.status_code == 200
    assert client.post("/jobs/b-task/cancel", headers={}).status_code == 200

    stored = job_store.get_job("b-task", db_path=scoped_stack, client_id=LOCAL_OWNER_ID)
    assert stored["status"] == "cancelled"
    assert stored["result"]["transcript_text"] == "A 改过的内容"

    # A task id that exists nowhere is still a 404, whoever asks.
    for headers in (a, {}):
        assert client.get("/jobs/no-such-task", headers=headers).status_code == 404
        assert client.post("/jobs/no-such-task/cancel", headers=headers).status_code == 404


def test_adopt_jobs_for_owner_moves_every_task_once_and_backs_up_first(tmp_path):
    import sqlite3

    jobs_db = tmp_path / "jobs.sqlite"
    for task_id, client_id in (
        ("page-task", "desktop-a"),
        ("mcp-task", "mcp-server"),
        ("old-task", "anonymous"),
        ("orphan-task", "anonymous"),
        ("owned-task", LOCAL_OWNER_ID),
    ):
        job_store.upsert_job(
            task_id=task_id, status="completed", client_id=client_id, db_path=jobs_db
        )
    with sqlite3.connect(jobs_db) as conn:
        conn.execute("UPDATE jobs SET client_id = NULL WHERE task_id = 'orphan-task'")

    def backups():
        return sorted(tmp_path.glob("*.backup-before-owner-merge-*"))

    assert job_store.adopt_jobs_for_owner(LOCAL_OWNER_ID, db_path=jobs_db) == 4
    with sqlite3.connect(jobs_db) as conn:
        owners = {row[0] for row in conn.execute("SELECT client_id FROM jobs")}
    assert owners == {LOCAL_OWNER_ID}
    assert len(backups()) == 1
    with sqlite3.connect(backups()[0]) as conn:
        (orphan_owner,) = conn.execute(
            "SELECT client_id FROM jobs WHERE task_id = 'orphan-task'"
        ).fetchone()
    assert orphan_owner is None, "the backup keeps the ownership from before the move"

    # Nothing left to move: no change and no second backup.
    assert job_store.adopt_jobs_for_owner(LOCAL_OWNER_ID, db_path=jobs_db) == 0
    assert len(backups()) == 1


def test_adopt_jobs_for_owner_leaves_an_all_owned_database_alone(tmp_path):
    jobs_db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(
        task_id="owned-task", status="completed", client_id=LOCAL_OWNER_ID, db_path=jobs_db
    )

    assert job_store.adopt_jobs_for_owner(LOCAL_OWNER_ID, db_path=jobs_db) == 0
    assert list(tmp_path.glob("*.backup-before-owner-merge-*")) == []


# ---- startup recovery ----------------------------------------------------------------

def test_recover_stale_jobs_fails_stranded_work(monkeypatch, tmp_path):
    jobs_db = tmp_path / "jobs.sqlite"
    active_ids = [f"s-{index}" for index in range(205)]
    for task_id in active_ids:
        job_store.upsert_job(
            task_id=task_id, status="queued", client_id="desktop-a", db_path=jobs_db
        )
    job_store.upsert_job(
        task_id="completed", status="completed", client_id="desktop-a", db_path=jobs_db
    )
    monkeypatch.setattr(
        local_main,
        "list_jobs_by_statuses",
        functools.partial(job_store.list_jobs_by_statuses, db_path=jobs_db),
    )
    monkeypatch.setattr(
        local_main, "upsert_job", functools.partial(job_store.upsert_job, db_path=jobs_db)
    )

    assert recover_stale_jobs() == len(active_ids)
    for task_id in active_ids:
        job = job_store.get_job(task_id, db_path=jobs_db)
        assert job["status"] == "failed"
        assert job["stage"] == "recovery"
    assert job_store.get_job("completed", db_path=jobs_db)["status"] == "completed"


def test_local_main_loads_env_before_job_store_defaults(tmp_path):
    expected_db = tmp_path / "configured.sqlite"
    script = f"""
import os
os.environ.pop('FLUENTFLOW_JOB_DB_PATH', None)
from backend.core import local_config
local_config.load_project_env = lambda: os.environ.__setitem__(
    'FLUENTFLOW_JOB_DB_PATH', {str(expected_db)!r}
)
import backend.local_main
from backend.core import job_store
assert str(job_store.DEFAULT_DB_PATH) == {str(expected_db)!r}
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
