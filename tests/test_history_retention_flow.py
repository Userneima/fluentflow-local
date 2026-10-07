"""Retention takes the recording when its time is up, and nothing else.

A finished task keeps its uploaded recording for a few days so it can be
re-run; after that the file goes, but the note and the transcript the user
paid for stay, and the task still opens. A task still inside its retention
window is not touched at all, and a task that is still running is never
touched. The retention pass runs after every finished job on the local
edition, through ``local_processing._enforce_local_history_retention``.

``tests/test_job_store_concurrency.py`` checks what the pass *reads*; this
file checks what it does to the files and the record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core import job_store
from backend.core.storage_paths import _artifact_storage_dir, _source_storage_dir, find_source_file
from backend.routers import local_processing as lp

CLIENT = "retention-flow-test"


def _iso(days_from_now: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat(timespec="seconds")


def _finished_task(task_id: str, *, expires_in_days: float, status: str = "completed") -> dict:
    """A task with a real recording in the store and a real note artifact."""
    source_dir = _source_storage_dir() / task_id
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.mp4").write_bytes(b"the recording")
    artifact_dir = _artifact_storage_dir() / task_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "note.md").write_text("# 笔记\n\n正文", encoding="utf-8")
    (artifact_dir / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    result = {
        "task_id": task_id,
        "summary_markdown": "# 笔记\n\n正文",
        "transcript_text": "你好",
        "artifacts": {"note": str(artifact_dir / "note.md"), "transcript": str(artifact_dir / "transcript.srt")},
        "source_file_available": True,
        "source_file_storage": "local",
        "source_retention_status": "retained",
        "source_retention_expires_at": _iso(expires_in_days),
    }
    job_store.upsert_job(
        task_id=task_id,
        status=status,
        client_id=CLIENT,
        stage="done",
        progress=100,
        source_filename="lecture.mp4",
        metadata={"route": "/process", "display_title": "讲座"},
        result=result,
    )
    return {"source_dir": source_dir, "artifact_dir": artifact_dir, "result": result}


@pytest.fixture(autouse=True)
def retention_window(monkeypatch):
    """The source window these tests are written against."""
    monkeypatch.setenv("FLUENTFLOW_SOURCE_RETENTION_DAYS", "7")
    yield
    job_store.delete_jobs([job["task_id"] for job in job_store.list_jobs(client_id=CLIENT, limit=100)], client_id=CLIENT)


def test_an_expired_recording_is_removed_and_the_note_and_transcript_stay():
    expired = _finished_task("ret-expired", expires_in_days=-1)
    assert find_source_file("ret-expired") is not None

    outcome = lp._enforce_local_history_retention(CLIENT)

    assert outcome["expired_source_count"] == 1
    assert outcome["pruned_count"] == 0
    assert not expired["source_dir"].exists(), "the recording is gone"
    assert find_source_file("ret-expired") is None
    assert (expired["artifact_dir"] / "note.md").read_text(encoding="utf-8") == "# 笔记\n\n正文"
    assert (expired["artifact_dir"] / "transcript.srt").is_file()

    job = job_store.get_job("ret-expired", client_id=CLIENT)
    assert job["status"] == "completed", "the task still opens"
    result = job["result"]
    assert result["summary_markdown"] == "# 笔记\n\n正文"
    assert result["transcript_text"] == "你好"
    assert result["artifacts"] == expired["result"]["artifacts"]
    assert result["source_file_available"] is False
    assert result["source_retention_status"] == "expired"
    assert result["source_retention_cleaned_at"], "the record says when the recording went"


def test_a_recording_still_inside_its_window_is_left_exactly_as_it_was():
    fresh = _finished_task("ret-fresh", expires_in_days=5)
    before = job_store.get_job("ret-fresh", client_id=CLIENT)

    outcome = lp._enforce_local_history_retention(CLIENT)

    assert outcome["expired_source_count"] == 0
    assert (fresh["source_dir"] / "source.mp4").read_bytes() == b"the recording"
    assert job_store.get_job("ret-fresh", client_id=CLIENT) == before


def test_one_pass_handles_both_and_the_second_pass_finds_nothing_more():
    expired = _finished_task("ret-old", expires_in_days=-0.01)
    fresh = _finished_task("ret-new", expires_in_days=30)

    first = lp._enforce_local_history_retention(CLIENT)
    second = lp._enforce_local_history_retention(CLIENT)

    assert first["expired_source_count"] == 1 and second["expired_source_count"] == 0
    assert not expired["source_dir"].exists()
    assert (fresh["source_dir"] / "source.mp4").is_file()
    assert job_store.get_job("ret-new", client_id=CLIENT)["result"]["source_file_available"] is True


def test_a_running_task_is_never_touched_even_with_a_stale_expiry():
    """A task mid-run may carry a date from a previous attempt; the recording is in use."""
    running = _finished_task("ret-running", expires_in_days=-1, status="running")

    outcome = lp._enforce_local_history_retention(CLIENT)

    assert outcome["expired_source_count"] == 0
    assert (running["source_dir"] / "source.mp4").is_file()


def test_the_pass_only_looks_at_its_own_client():
    """Local has one owner, but the pass is scoped so nothing else's rows are read."""
    other = _finished_task("ret-other", expires_in_days=-1)

    outcome = lp._enforce_local_history_retention("someone-else")
    assert outcome["expired_source_count"] == 0
    assert (other["source_dir"] / "source.mp4").is_file()
    assert lp._enforce_local_history_retention(None)["pruned_count"] == 0


# ── nothing a task produced expires, however old it is ──────────────────────

def _age(task_id: str, days: float) -> None:
    """Move a task's last change back in time, as if it had sat untouched."""
    import sqlite3

    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(job_store.DEFAULT_DB_PATH) as conn:
        conn.execute("UPDATE jobs SET updated_at = ?, created_at = ? WHERE task_id = ?", (stamp, stamp, task_id))


def test_a_year_old_task_keeps_everything_but_fluentflows_copy_of_the_upload():
    """Owner's rule (2026-10-07): the de-breathed audio and video are never
    deleted, no frame is deleted, and the user's own originals are theirs to
    manage. Only the private copy FluentFlow made of an upload expires."""
    task = _finished_task("ret-year-old", expires_in_days=-358)
    artifact_dir = task["artifact_dir"]
    (artifact_dir / "debreath").mkdir(exist_ok=True)
    cut = artifact_dir / "debreath" / "lecture_debreath.mp4"
    cut.write_bytes(b"v" * 1000)
    playback = artifact_dir / "lecture_audio.mp3"
    playback.write_bytes(b"a" * 500)
    frames = artifact_dir / "frames"
    frames.mkdir(exist_ok=True)
    (frames / "note_0001.jpg").write_bytes(b"shown in the note")
    (frames / "note_0002.jpg").write_bytes(b"only a candidate")
    _age("ret-year-old", 365)

    lp._enforce_local_history_retention(CLIENT)

    job = job_store.get_job("ret-year-old")
    assert job is not None, "the task is not deleted"
    assert job["result"]["transcript_text"] == "你好"
    assert job["result"]["summary_markdown"] == "# 笔记\n\n正文"
    for kept in (cut, playback, frames / "note_0001.jpg", frames / "note_0002.jpg",
                 artifact_dir / "note.md", artifact_dir / "transcript.srt"):
        assert kept.is_file(), f"{kept.name} must never be removed by retention"
    assert not (task["source_dir"] / "source.mp4").exists(), "FluentFlow's own copy expires"
