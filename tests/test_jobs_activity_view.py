"""The restart guard asks a light list: whether each task is still working,
and nothing of its result.

Requirements (to-do 2026-11-25, in the user's terms):

- The full records list took 12 seconds on a busy machine with 300 tasks, past
  the guard's 10-second wait, so the guard always answered "could not tell"
  and no agent could restart the service. The guard asks for a list with only
  each task's status and activity, and answers within its wait.
- That list says "still working" for exactly the tasks whose own page does
  (``GET /jobs/{id}``, read from the whole result).
- It carries no result: opening one task still reads its whole result.
- The records page's list keeps what its cards show and leaves the rest of a
  result (the files a task made other than the cut copy, the previews' second
  copies) to the task's own read.
"""

from __future__ import annotations

import functools
import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store
from backend.routers import local_jobs

_GUARD_PATH = Path(__file__).resolve().parents[1] / "launchers" / "macos" / "count_active_jobs.py"
_spec = importlib.util.spec_from_file_location("count_active_jobs", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

# One task in each state a finished or unfinished task can be in, and whether
# it is still working.
_STATES = {
    "idle-done": ("completed", "completed", {}, False),
    "idle-failed": ("failed", "failed", {}, False),
    "transcribing": ("processing", None, {}, True),
    "queued": ("queued", None, {}, True),
    "note-pending": ("completed", "pending", {"summary_status": "pending"}, True),
    "cut-again": ("completed", "completed", {"debreath": {"status": "running", "ranges": [[0, 1]] * 50}}, True),
    "note-again": ("completed", "completed", {"visual_note": {"status": "running", "markdown": "x" * 500}}, True),
    "agent-judging": ("completed", "completed", {"agent_cut": {"status": "waiting"}}, False),
}


@pytest.fixture()
def jobs_app(monkeypatch, tmp_path):
    db = tmp_path / "jobs.sqlite"
    monkeypatch.setattr(local_jobs, "local_client_scope", lambda _request: None)
    for name in ("list_job_states", "list_job_summaries", "list_jobs", "get_job"):
        monkeypatch.setattr(local_jobs, name, functools.partial(getattr(job_store, name), db_path=db))
    app = FastAPI()
    app.include_router(local_jobs.router)
    return app, db


def _segments(count: int) -> list[dict]:
    return [{"start": i, "end": i + 1, "text": "这一句是转录稿里的一句话，用来撑起结果的大小。"} for i in range(count)]


def _add(db, task_id, status, summary_status, extra, segments=20):
    job_store.upsert_job(
        task_id=task_id, status=status, summary_status=summary_status, stage="done", db_path=db,
        metadata={"display_title": task_id},
        result={"task_id": task_id, "summary_markdown": "# 笔记\n" + "正文" * 200,
                "transcript_text": "转录稿" * 200, "raw_segments": _segments(segments),
                "artifacts": {kind: {"filename": f"{task_id}.{kind}", "kind": kind}
                              for kind in ("debreath_media", "playback_audio", "transcript_srt")},
                **extra},
    )


def test_the_light_list_carries_activity_and_no_result(jobs_app):
    app, db = jobs_app
    for task_id, (status, summary_status, extra, _busy) in _STATES.items():
        _add(db, task_id, status, summary_status, extra)
    client = TestClient(app)

    light = {job["task_id"]: job for job in client.get("/jobs?view=activity").json()["jobs"]}
    listed = {job["task_id"]: job for job in client.get("/jobs").json()["jobs"]}

    assert set(light) == set(_STATES)
    for task_id, (_status, _summary, _extra, busy) in _STATES.items():
        assert set(light[task_id]) == {"task_id", "status", "activity"}
        assert light[task_id]["activity"]["busy"] is busy, task_id
        assert light[task_id]["activity"] == client.get(f"/jobs/{task_id}").json()["activity"], task_id
        assert light[task_id]["activity"] == listed[task_id]["activity"], task_id
        assert guard.is_busy(light[task_id]) is busy

    # The page's list: what the card shows, and the two files the editor
    # plays from the row while it reads the task, not the rest of the result.
    row = listed["idle-done"]
    assert set(row["result"]["artifacts"]) == {"debreath_media", "playback_audio"}
    assert "transcript_text_preview" not in row["result"] and "summary_preview" not in row["result"]
    assert "raw_segments" not in row["result"]
    assert row["result"]["summary_chars"] == len("# 笔记\n" + "正文" * 200)
    assert row["result"]["display_title"] is None and row["metadata"]["display_title"] == "idle-done"
    assert not {"artifacts", "actions"} & set(row["task_snapshot"])

    # Opening one task still reads its whole result.
    one = client.get("/jobs/note-again").json()
    assert len(one["result"]["raw_segments"]) == 20
    assert one["result"]["visual_note"]["markdown"] == "x" * 500
    assert set(one["result"]["artifacts"]) == {"debreath_media", "playback_audio", "transcript_srt"}
    assert one["result"]["transcript_text"] == "转录稿" * 200


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_restart_guard_answers_within_its_wait_on_300_tasks(jobs_app):
    app, db = jobs_app
    asked: list[str] = []

    @app.middleware("http")
    async def record(request, call_next):
        asked.append(str(request.url.query))
        return await call_next(request)

    # 300 tasks with sizeable results, two of them still working.
    for index in range(298):
        _add(db, f"done-{index}", "completed", "completed", {}, segments=200)
    _add(db, "transcribing", "processing", None, {})
    _add(db, "note-pending", "completed", "pending", {"summary_status": "pending"})

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        started = time.monotonic()
        count = guard.active_job_count(f"http://127.0.0.1:{port}")
        took = time.monotonic() - started
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    assert count == 2
    assert asked and all(query == "view=activity&limit=100" for query in asked)
    # Both scopes are asked; each answer is a fraction of the guard's wait.
    assert took < guard.TIMEOUT_SECONDS / 2
