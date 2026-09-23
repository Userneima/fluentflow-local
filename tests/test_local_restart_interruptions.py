"""Tasks a service restart cut off: recovery records it, the app is told once.

A restart marks every queued/running task failed. Among a long records list a
few more failed cards are easy to miss, so recovery stamps each one and the app
asks the store (not the browser) which ones the user has not been told about.
"""

from __future__ import annotations

import functools

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.local_main as local_main
import backend.routers.local_jobs as local_jobs
from backend.core import job_store
from backend.core.local_request_scope import LOCAL_OWNER_ID


def _recover_into(monkeypatch, jobs_db):
    monkeypatch.setattr(
        local_main,
        "list_jobs_by_statuses",
        functools.partial(job_store.list_jobs_by_statuses, db_path=jobs_db),
    )
    monkeypatch.setattr(
        local_main, "upsert_job", functools.partial(job_store.upsert_job, db_path=jobs_db)
    )
    return local_main.recover_stale_jobs()


def _client(monkeypatch, jobs_db) -> TestClient:
    monkeypatch.setattr(
        local_jobs,
        "list_unacknowledged_restart_interruptions",
        functools.partial(job_store.list_unacknowledged_restart_interruptions, db_path=jobs_db),
    )
    monkeypatch.setattr(
        local_jobs,
        "acknowledge_restart_interruptions",
        functools.partial(job_store.acknowledge_restart_interruptions, db_path=jobs_db),
    )
    app = FastAPI()
    app.include_router(local_jobs.router)
    return TestClient(app)


def _seed(jobs_db, tmp_path):
    kept = tmp_path / "kept.mp4"
    kept.write_bytes(b"x")
    job_store.upsert_job(
        task_id="running-kept",
        status="running",
        client_id=LOCAL_OWNER_ID,
        stage="stt",
        progress=40,
        source_filename="kept.mp4",
        metadata={
            "display_title": "Kept lecture",
            "folder_intake": {"original_path": str(kept)},
        },
        db_path=jobs_db,
    )
    moved = tmp_path / "moved-away.mp4"
    job_store.upsert_job(
        task_id="queued-moved",
        status="queued",
        client_id=LOCAL_OWNER_ID,
        stage="queued",
        progress=0,
        source_filename="moved-away.mp4",
        metadata={
            "folder_intake": {"original_path": str(moved)},
            "queue_wait": {"waiting_for": "running-kept", "since": "2026-09-23T10:00:00+00:00"},
        },
        db_path=jobs_db,
    )
    job_store.upsert_job(
        task_id="done",
        status="completed",
        client_id=LOCAL_OWNER_ID,
        db_path=jobs_db,
    )


def test_recovery_records_the_interruption_and_whether_it_had_started(monkeypatch, tmp_path):
    jobs_db = tmp_path / "jobs.sqlite"
    _seed(jobs_db, tmp_path)

    assert _recover_into(monkeypatch, jobs_db) == 2

    running = job_store.get_job("running-kept", db_path=jobs_db)
    queued = job_store.get_job("queued-moved", db_path=jobs_db)
    assert running["status"] == queued["status"] == "failed"
    stamp = running["metadata"]["restart_interruption"]
    assert stamp["started"] is True
    assert stamp["stage"] == "stt"
    assert stamp["progress"] == 40
    assert stamp["interrupted_at"]
    assert "acknowledged_at" not in stamp
    # Existing metadata survives the merge; the stale queue wait does not.
    assert running["metadata"]["display_title"] == "Kept lecture"
    assert queued["metadata"]["restart_interruption"]["started"] is False
    assert queued["metadata"]["queue_wait"] is None
    assert "restart_interruption" not in (job_store.get_job("done", db_path=jobs_db)["metadata"] or {})


def test_interrupted_route_lists_them_with_retry_and_original_location(monkeypatch, tmp_path):
    jobs_db = tmp_path / "jobs.sqlite"
    _seed(jobs_db, tmp_path)
    _recover_into(monkeypatch, jobs_db)
    client = _client(monkeypatch, jobs_db)

    response = client.get("/jobs/interrupted")

    assert response.status_code == 200
    tasks = {task["task_id"]: task for task in response.json()["tasks"]}
    assert set(tasks) == {"running-kept", "queued-moved"}
    assert tasks["running-kept"]["title"] == "Kept lecture"
    assert tasks["running-kept"]["started"] is True
    assert tasks["running-kept"]["retryable"] is True
    assert tasks["running-kept"]["original_path"] is None
    assert tasks["queued-moved"]["started"] is False
    assert tasks["queued-moved"]["retryable"] is False
    assert tasks["queued-moved"]["original_path"] == str(tmp_path / "moved-away.mp4")
    assert tasks["queued-moved"]["title"] == "moved-away.mp4"


def test_acknowledge_makes_them_disappear_for_every_window(monkeypatch, tmp_path):
    jobs_db = tmp_path / "jobs.sqlite"
    _seed(jobs_db, tmp_path)
    _recover_into(monkeypatch, jobs_db)
    client = _client(monkeypatch, jobs_db)
    before = job_store.get_job("running-kept", db_path=jobs_db)["updated_at"]

    response = client.post(
        "/jobs/interrupted/acknowledge", json={"task_ids": ["running-kept", "unknown"]}
    )

    assert response.status_code == 200
    assert response.json()["acknowledged"] == ["running-kept"]
    remaining = [task["task_id"] for task in client.get("/jobs/interrupted").json()["tasks"]]
    assert remaining == ["queued-moved"]
    after = job_store.get_job("running-kept", db_path=jobs_db)
    assert after["metadata"]["restart_interruption"]["acknowledged_at"]
    assert after["updated_at"] == before
    assert after["status"] == "failed"

    client.post("/jobs/interrupted/acknowledge", json={"task_ids": ["queued-moved"]})
    assert client.get("/jobs/interrupted").json()["tasks"] == []


def test_acknowledge_requires_a_list_of_ids(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path / "jobs.sqlite")

    assert client.post("/jobs/interrupted/acknowledge", json={}).status_code == 422


def test_interrupted_routes_are_not_shadowed_in_the_assembled_app(monkeypatch, tmp_path):
    jobs_db = tmp_path / "jobs.sqlite"
    _seed(jobs_db, tmp_path)
    _recover_into(monkeypatch, jobs_db)
    _client(monkeypatch, jobs_db)
    client = TestClient(local_main.create_local_app())

    listed = client.get("/jobs/interrupted")
    assert listed.status_code == 200
    assert len(listed.json()["tasks"]) == 2
    acked = client.post("/jobs/interrupted/acknowledge", json={"task_ids": ["queued-moved"]})
    assert acked.status_code == 200
    assert acked.json()["acknowledged"] == ["queued-moved"]


def test_job_list_returns_every_job_and_can_poll_only_what_changed(tmp_path):
    jobs_db = tmp_path / "jobs.sqlite"
    for index in range(260):
        job_store.upsert_job(
            task_id=f"t-{index}", status="completed", client_id=LOCAL_OWNER_ID, db_path=jobs_db
        )

    everything = job_store.list_job_summaries(limit=None, client_id=LOCAL_OWNER_ID, db_path=jobs_db)
    assert len(everything) == 260

    newest = max(job["updated_at"] for job in everything)
    changed = job_store.list_job_summaries(
        limit=None, client_id=LOCAL_OWNER_ID, db_path=jobs_db, updated_since=newest
    )
    assert changed
    assert all(job["updated_at"] >= newest for job in changed)
    assert job_store.list_job_summaries(
        limit=None, client_id=LOCAL_OWNER_ID, db_path=jobs_db, updated_since="9999-01-01"
    ) == []
