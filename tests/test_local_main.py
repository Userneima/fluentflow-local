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


def _request_as_peer(peer: str, method: str, path: str, **kwargs) -> httpx.Response:
    import asyncio

    async def scenario():
        transport = httpx.ASGITransport(app=create_local_app(), client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://local") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(scenario())


_LAN = "203.0.113.5"
_ACK = "/jobs/interrupted/acknowledge"


def test_lan_mode_still_refuses_a_page_served_from_elsewhere(monkeypatch):
    """Opening the LAN opens it to API clients, not to any web page a LAN
    machine happens to have open: a cross-site POST is still refused."""
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    r = _request_as_peer(_LAN, "GET", "/health", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = _request_as_peer(
        _LAN, "POST", _ACK, json={"task_ids": []},
        headers={"origin": "https://evil.example", "authorization": "Bearer lan-secret"},
    )
    assert r.status_code == 403, "a token does not excuse a foreign page"
    r = _request_as_peer("127.0.0.1", "GET", "/health", headers={"origin": "null"})
    assert r.status_code == 403


def test_lan_mode_asks_another_device_for_the_access_token_to_write(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    assert _request_as_peer(_LAN, "GET", "/health").status_code == 200
    refused = _request_as_peer(_LAN, "POST", _ACK, json={"task_ids": []})
    assert refused.status_code == 401
    assert "令牌" in refused.json()["detail"]
    wrong = _request_as_peer(_LAN, "POST", _ACK, json={"task_ids": []}, headers={"authorization": "Bearer nope"})
    assert wrong.status_code == 401
    allowed = _request_as_peer(_LAN, "POST", _ACK, json={"task_ids": []}, headers={"authorization": "Bearer lan-secret"})
    assert allowed.status_code == 200


def test_lan_mode_without_a_configured_token_cannot_be_written_to_from_the_lan(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.delenv("FLUENTFLOW_ACCESS_TOKEN", raising=False)

    r = _request_as_peer(_LAN, "POST", _ACK, json={"task_ids": []})

    assert r.status_code == 403
    assert "FLUENTFLOW_ACCESS_TOKEN" in r.json()["detail"]


def test_lan_mode_does_not_make_the_owners_own_page_carry_a_token(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    r = _request_as_peer("127.0.0.1", "POST", _ACK, json={"task_ids": []}, headers={"origin": "http://localhost:5173"})

    assert r.status_code == 200


def _lan_page_request(method: str, *, host: str = "192.168.1.5:8000", origin: str = "http://192.168.1.5:8000", **kwargs):
    import asyncio

    async def scenario():
        transport = httpx.ASGITransport(app=create_local_app(), client=("192.168.1.20", 40000))
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
            return await client.request(method, _ACK, json={"task_ids": []}, headers={"origin": origin, **kwargs})

    return asyncio.run(scenario())


def test_lan_mode_lets_the_page_opened_on_another_device_write_with_the_token(monkeypatch):
    """Requirement: with LAN mode and an access token configured, the page
    opened from another device at this machine's LAN address can change
    things once that device has the token, instead of being refused as a
    foreign site."""
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    r = _lan_page_request("POST", **{"x-fluentflow-access-token": "lan-secret"})

    assert r.status_code == 200


def test_lan_mode_page_without_the_token_is_told_where_to_enter_it(monkeypatch):
    """Requirement: without the token the LAN page is told, in Chinese, to
    enter the access token in 「菜单 → Agent 接入」 on that device."""
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    r = _lan_page_request("POST")

    assert r.status_code == 401
    assert "访问令牌" in r.json()["detail"] and "菜单 → Agent 接入" in r.json()["detail"]


# Requirement (2026-10-09): in LAN mode another device needs the access token
# for everything, reads included, because transcripts and notes are private.
# Only what loads the page and lets the user reach 「菜单 → Agent 接入」 to enter
# the token is served without it.

def test_lan_mode_another_device_cannot_read_tasks_without_the_token(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    for path in ("/jobs", "/runtime-config", "/version", "/agent/v1/tasks", "/video-sources/jobs"):
        r = _request_as_peer(_LAN, "GET", path)
        assert r.status_code == 401, path
        assert "菜单 → Agent 接入" in r.json()["detail"], path


def test_lan_mode_another_device_reads_with_the_token(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    r = _request_as_peer(_LAN, "GET", "/jobs", headers={"x-fluentflow-access-token": "lan-secret"})

    assert r.status_code == 200


def test_lan_mode_without_a_configured_token_serves_another_device_no_data(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.delenv("FLUENTFLOW_ACCESS_TOKEN", raising=False)

    r = _request_as_peer(_LAN, "GET", "/jobs")

    assert r.status_code == 403
    assert "FLUENTFLOW_ACCESS_TOKEN" in r.json()["detail"]


def test_lan_mode_serves_the_page_without_a_token_so_the_token_can_be_entered(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    # The page (its routes all serve index.html; 503 when the frontend is not
    # built on this checkout), its static files and the health check.
    for path in ("/", "/agent", "/editor/some-task", "/favicon.svg", "/health"):
        r = _request_as_peer(_LAN, "GET", path)
        assert r.status_code not in (401, 403), path
    asset = _request_as_peer(_LAN, "GET", "/assets/does-not-exist.js")
    assert asset.status_code not in (401, 403)


def test_lan_mode_does_not_ask_this_machine_for_a_token_to_read(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    assert _request_as_peer("127.0.0.1", "GET", "/jobs").status_code == 200


def test_lan_mode_accepts_this_machines_own_local_name_as_its_page(monkeypatch):
    """Opened as http://<this-mac>.local:8000 on another device, the page is
    this server's own page, whichever way the hostname is reported."""
    import backend.core.local_http_boundary as boundary

    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")
    for reported in ("Studio-Mac.local", "Studio-Mac"):
        monkeypatch.setattr(boundary.socket, "gethostname", lambda reported=reported: reported)
        r = _lan_page_request(
            "POST", host="studio-mac.local:8000", origin="http://studio-mac.local:8000",
            **{"x-fluentflow-access-token": "lan-secret"},
        )
        assert r.status_code == 200, reported


def test_lan_mode_refuses_another_devices_local_name(monkeypatch):
    """A device on the network can announce any .local name and point it at
    this machine; a page served under a name that is not this machine's is
    refused even with matching Host and the token (mDNS rebinding)."""
    import backend.core.local_http_boundary as boundary

    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")
    monkeypatch.setattr(boundary.socket, "gethostname", lambda: "Studio-Mac.local")

    r = _lan_page_request(
        "POST", host="printer.local:8000", origin="http://printer.local:8000",
        **{"x-fluentflow-access-token": "lan-secret"},
    )

    assert r.status_code == 403


def test_lan_mode_still_refuses_a_rebound_domain_posing_as_this_machine(monkeypatch):
    """Requirement: cross-site protection stays. A site whose own domain name
    is pointed at this machine sends matching Origin and Host; it is still
    refused, because only an IP address or a .local name counts as this page."""
    monkeypatch.setenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    r = _lan_page_request(
        "POST", host="evil.example:8000", origin="http://evil.example:8000",
        **{"x-fluentflow-access-token": "lan-secret"},
    )
    assert r.status_code == 403
    r = _lan_page_request(
        "POST", origin="http://192.168.1.5:9999", **{"x-fluentflow-access-token": "lan-secret"},
    )
    assert r.status_code == 403, "another port on the same address is another site"


def test_without_lan_mode_a_lan_origin_is_still_foreign(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", raising=False)

    r = TestClient(create_local_app()).get(
        "/health", headers={"origin": "http://testserver", "host": "testserver"}
    )

    assert r.status_code == 403


def test_strict_mode_is_unchanged_by_a_configured_token(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_ALLOW_NON_LOOPBACK", raising=False)
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "lan-secret")

    r = _request_as_peer(_LAN, "POST", _ACK, json={"task_ids": []}, headers={"authorization": "Bearer lan-secret"})

    assert r.status_code == 403


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


def test_screenshots_load_on_another_device_with_the_token_cookie(monkeypatch):
    """In LAN mode a note's pictures are plain <img> tags, which cannot send the
    token header; the page keeps the token in a cookie, accepted for reads."""
    from starlette.requests import Request
    from backend.core import local_http_boundary as b

    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "tok-123")

    def req(method, cookie):
        headers = [(b"cookie", f"fluentflow_access_token={cookie}".encode())] if cookie else []
        return Request({"type": "http", "method": method, "path": "/jobs/t/artifacts/frame", "headers": headers, "query_string": b""})

    assert b._cookie_token_ok(req("GET", "tok-123")) is True
    assert b._cookie_token_ok(req("GET", "wrong")) is False
    assert b._cookie_token_ok(req("GET", None)) is False
    assert b._cookie_token_ok(req("POST", "tok-123")) is False, "never for writes"
