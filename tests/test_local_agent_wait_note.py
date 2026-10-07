"""An agent that waits for a task gets the note, not an empty one.

A local task reads ``completed`` as soon as its transcript is stored; the note
is written after that, from the cut media. Requirements, from the agent's side:

- while the note is still being written, ``wait`` is not done and says the
  note is pending, and ``get_task`` / the package say the same;
- once the note is written, failed, or deliberately skipped, ``wait`` is done
  and the note's status is in the answer;
- a wait already in progress returns the written note when it lands;
- a task whose note can never finish (failed task, note stranded by a restart)
  never keeps an agent waiting.
"""

from __future__ import annotations

import functools
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store
from backend.core.local_request_scope import LOCAL_OWNER_ID
import backend.routers.local_agent as local_agent
from backend.routers.local_agent import router as agent_router

_TOKEN = "local-secret-token"
_HEADERS = {"x-fluentflow-access-token": _TOKEN}
_TRANSCRIPT = {"transcript_text": "大家好，这是一段转录。"}


@pytest.fixture()
def jobs_db(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path / "data"))
    db = tmp_path / "jobs.sqlite"
    monkeypatch.setattr(local_agent, "get_job", functools.partial(job_store.get_job, db_path=db))
    monkeypatch.setattr(local_agent, "_local_client_scope", lambda request: LOCAL_OWNER_ID)
    return db


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(agent_router)
    return TestClient(app)


def _store(db, task_id: str, *, status="completed", stage, summary_status, result):
    job_store.upsert_job(
        task_id=task_id, status=status, stage=stage, client_id=LOCAL_OWNER_ID,
        summary_status=summary_status, result={"task_id": task_id, **result}, db_path=db,
    )


def _note_being_written(db, task_id: str) -> None:
    _store(db, task_id, stage="note", summary_status="pending", result={
        **_TRANSCRIPT, "summary_markdown": "", "summary_skipped": False, "summary_status": "pending",
        "visual_note": {"status": "running"},
    })


def _note_written(db, task_id: str) -> None:
    _store(db, task_id, stage="done", summary_status="completed", result={
        **_TRANSCRIPT, "summary_markdown": "# 笔记", "summary_skipped": False,
        "summary_status": "completed", "visual_note": {"status": "completed"},
    })


def _wait(client, task_id, **payload):
    response = client.post(f"/agent/v1/tasks/{task_id}/wait", headers=_HEADERS, json=payload)
    assert response.status_code == 200
    return response.json()


def test_wait_is_not_done_while_a_completed_tasks_note_is_being_written(jobs_db):
    _note_being_written(jobs_db, "t-pending")
    client = _client()

    waited = _wait(client, "t-pending", timeout_seconds=0)

    assert waited["done"] is False
    assert waited["note_pending"] is True
    assert waited["task"]["status"] == "completed"
    task = client.get("/agent/v1/tasks/t-pending", headers=_HEADERS).json()["task"]
    assert task["done"] is False and task["note_pending"] is True
    package = client.get("/agent/v1/tasks/t-pending/package", headers=_HEADERS).json()
    assert package["note_pending"] is True


def test_wait_returns_the_note_once_it_is_written(jobs_db):
    _note_written(jobs_db, "t-done")
    client = _client()

    waited = _wait(client, "t-done", timeout_seconds=0)

    assert waited["done"] is True
    assert waited["note_status"] == "completed"
    assert waited["package"]["note"]["markdown"] == "# 笔记"
    assert waited["package"]["note_pending"] is False
    task = client.get("/agent/v1/tasks/t-done", headers=_HEADERS).json()["task"]
    assert task["done"] is True and task["note_pending"] is False


def test_a_wait_in_progress_returns_the_note_when_it_lands(jobs_db):
    _note_being_written(jobs_db, "t-lands")
    timer = threading.Timer(0.8, _note_written, args=(jobs_db, "t-lands"))
    timer.start()
    try:
        waited = _wait(_client(), "t-lands", timeout_seconds=10, poll_interval_seconds=0.5)
    finally:
        timer.cancel()

    assert waited["done"] is True
    assert waited["package"]["note"]["markdown"] == "# 笔记"


def test_a_failed_note_ends_the_wait_with_the_failure_visible(jobs_db):
    _store(jobs_db, "t-failed-note", stage="done", summary_status="failed", result={
        **_TRANSCRIPT, "summary_markdown": "", "summary_status": "failed",
        "summary_error": "Claude 登录已过期", "visual_note": {"status": "failed"},
    })

    waited = _wait(_client(), "t-failed-note", timeout_seconds=0)

    assert waited["done"] is True
    assert waited["note_status"] == "failed"


def test_a_transcript_only_task_is_done_without_a_note(jobs_db):
    _store(jobs_db, "t-skip", stage="done", summary_status="skipped", result={
        **_TRANSCRIPT, "summary_markdown": "", "summary_skipped": True, "summary_status": "skipped",
    })

    waited = _wait(_client(), "t-skip", timeout_seconds=0)

    assert waited["done"] is True
    assert waited["note_status"] == "skipped"


def test_rewriting_an_existing_note_keeps_the_wait_open(jobs_db):
    # A redo keeps the old note's "completed" until the new one is written.
    _store(jobs_db, "t-redo", stage="done", summary_status="completed", result={
        **_TRANSCRIPT, "summary_markdown": "# 旧笔记", "summary_status": "completed",
        "visual_note": {"status": "running"},
    })

    waited = _wait(_client(), "t-redo", timeout_seconds=0)

    assert waited["done"] is False
    assert waited["note_pending"] is True


def test_a_note_stranded_by_a_restart_does_not_keep_the_agent_waiting(jobs_db):
    # Startup recovery fails the note in the result but leaves the job's stage.
    _store(jobs_db, "t-stranded", stage="note", summary_status="pending", result={
        **_TRANSCRIPT, "summary_markdown": "", "summary_status": "failed",
        "summary_error": "服务重启中断了笔记生成，重新写一次笔记即可。",
        "visual_note": {"status": "failed"},
    })

    waited = _wait(_client(), "t-stranded", timeout_seconds=0)

    assert waited["done"] is True
    assert waited["note_status"] == "failed"


def test_a_deferred_note_that_has_not_started_yet_is_not_read_as_skipped(jobs_db):
    # The pipeline stores the transcript as "skipped" when the frame note will
    # follow; the note step marks it pending a moment later.
    _store(jobs_db, "t-handover", stage="done", summary_status="skipped", result={
        **_TRANSCRIPT, "summary_markdown": "", "summary_skipped": True, "summary_status": "skipped",
        "note_deferred_to_visual_note": True,
    })

    waited = _wait(_client(), "t-handover", timeout_seconds=0)

    assert waited["done"] is False
    assert waited["note_pending"] is True


def test_a_deferred_note_that_never_started_stops_holding_the_agent(jobs_db, monkeypatch):
    _store(jobs_db, "t-never", stage="done", summary_status="skipped", result={
        **_TRANSCRIPT, "summary_markdown": "", "summary_skipped": True, "summary_status": "skipped",
        "note_deferred_to_visual_note": True,
    })
    monkeypatch.setattr(local_agent, "_DEFERRED_NOTE_HANDOVER_SECONDS", -1)

    waited = _wait(_client(), "t-never", timeout_seconds=0)

    assert waited["done"] is True
    assert waited["note_status"] == "skipped"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_a_task_that_ended_without_a_transcript_is_done(jobs_db, status):
    _store(jobs_db, f"t-{status}", status=status, stage="note", summary_status="pending", result={})

    assert _wait(_client(), f"t-{status}", timeout_seconds=0)["done"] is True
