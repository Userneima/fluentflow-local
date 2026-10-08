"""A manual de-breath that would undo or desynchronise other work is refused.

Requirements, in the user's terms:

- While the note for a task is being written, cutting the same task is refused
  with a Chinese reason: both write the whole task result back when they
  finish, and whichever lands second would erase the other.
- A task whose transcript was made from its own automatically cut file is not
  cut again: the subtitles and the note are timed to that file, and a second
  cut moves every gap without moving them. The user is told to resubmit the
  original file.
- A task with neither of these goes ahead.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import local_job_debreath


def _client(monkeypatch, job: dict) -> TestClient:
    monkeypatch.setattr(local_job_debreath, "get_job", lambda task_id, client_id=None: dict(job))
    monkeypatch.setattr(local_job_debreath, "queue_is_busy", lambda: False)
    monkeypatch.setattr(local_job_debreath.debreath_job, "resolve_source", lambda task_id: Path("/tmp/x.mp4"))
    monkeypatch.setattr(local_job_debreath.debreath_job, "claim", lambda task_id: None)
    monkeypatch.setattr(local_job_debreath.debreath_job, "run_debreath", lambda *a, **k: None)
    monkeypatch.setattr(local_job_debreath.debreath_job, "release", lambda task_id: None)
    app = FastAPI()
    app.include_router(local_job_debreath.router)
    return TestClient(app)


def _job(**result) -> dict:
    return {"task_id": "t", "status": "completed", "result": {"transcript_text": "x", **result}}


def test_cutting_is_refused_while_the_frame_note_is_being_written(monkeypatch):
    r = _client(monkeypatch, _job(visual_note={"status": "running"})).post("/jobs/t/debreath", json={})

    assert r.status_code == 409
    assert "笔记正在写" in r.json()["detail"]


def test_cutting_is_refused_while_the_note_is_waiting_to_be_written(monkeypatch):
    r = _client(monkeypatch, _job(summary_status="pending")).post("/jobs/t/debreath", json={})

    assert r.status_code == 409
    assert "笔记正在写" in r.json()["detail"]


def test_a_task_transcribed_from_its_cut_file_is_not_cut_again(monkeypatch):
    job = _job(debreath={"status": "completed", "used_for_transcription": True, "ran_before_transcription": True})

    r = _client(monkeypatch, job).post("/jobs/t/debreath", json={})

    assert r.status_code == 409
    assert r.json()["detail"] == (
        "这个任务转写时已经去过气口，再剪一次会和转写稿、笔记的时间点对不上；请重新提交原文件。"
    )


def test_a_finished_task_with_a_written_note_and_no_earlier_cut_goes_ahead(monkeypatch):
    job = _job(summary_status="completed", summary_markdown="# 笔记", visual_note={"status": "completed"})

    r = _client(monkeypatch, job).post("/jobs/t/debreath", json={})

    assert r.status_code == 200 and r.json()["accepted"] is True


def test_a_task_cut_by_hand_after_transcription_can_be_cut_again(monkeypatch):
    """A manual cut never changed the transcript's clock, so re-cutting is safe."""
    job = _job(debreath={"status": "completed", "used_for_transcription": True})

    r = _client(monkeypatch, job).post("/jobs/t/debreath", json={})

    assert r.status_code == 200
