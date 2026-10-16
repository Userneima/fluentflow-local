"""The job store under the load the queue actually puts on it."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from backend.core import history_retention, job_store


def test_metadata_written_by_several_writers_at_once_is_all_kept(tmp_path):
    """The queue-wait bookkeeping and the progress loop both write one row's
    metadata at the same moment; every key either wrote must survive."""
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(task_id="t", status="queued", metadata={"seed": True}, db_path=db)
    writers = 12
    ready = threading.Barrier(writers)

    def write(i: int) -> None:
        ready.wait()
        job_store.upsert_job(task_id="t", status="running", metadata={f"k{i}": i}, db_path=db)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    metadata = job_store.get_job("t", db_path=db)["metadata"]
    assert metadata["seed"] is True
    assert {f"k{i}" for i in range(writers)} <= set(metadata)


def test_the_schema_is_set_up_once_per_database_and_again_if_the_file_goes(tmp_path, monkeypatch):
    """The transcription progress loop touches the store every couple of
    seconds; it must not re-run the schema script each time."""
    db = tmp_path / "jobs.sqlite"
    opened: list[str] = []
    real_connect = sqlite3.connect

    def counting_connect(path, *args, **kwargs):
        opened.append(str(path))
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(job_store.sqlite3, "connect", counting_connect)
    job_store.reset_job_db_cache()

    job_store.ensure_job_db(db)
    job_store.ensure_job_db(db)
    job_store.ensure_job_db(db)
    assert opened.count(str(db)) == 1

    db.unlink()
    job_store.ensure_job_db(db)
    assert opened.count(str(db)) == 2 and db.is_file()


def _big_result(task_id: str, **extra) -> dict:
    return {"task_id": task_id, "transcript_text": "字" * 20000, "summary_markdown": "# 笔记", **extra}


def test_retention_reads_only_what_it_needs_and_still_expires_a_due_source(tmp_path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(
        task_id="old", status="completed", client_id="me", db_path=db,
        result=_big_result("old", source_file_available=True, source_retention_expires_at="2000-01-01T00:00:00+00:00"),
        metadata={"route": "/process"},
    )
    job_store.upsert_job(
        task_id="fresh", status="completed", client_id="me", db_path=db,
        result=_big_result("fresh", source_file_available=True, source_retention_expires_at="2999-01-01T00:00:00+00:00"),
    )
    job_store.upsert_job(task_id="plain", status="completed", client_id="me", db_path=db, result=_big_result("plain"))
    job_store.upsert_job(task_id="running", status="running", client_id="me", db_path=db)

    listed = {job["task_id"]: job for job in job_store.list_jobs_for_retention(db_path=db, client_id="me")}
    assert set(listed) == {"old", "fresh", "plain", "running"}
    assert "transcript_text" not in listed["plain"]["result"], "a row retention never writes is not parsed in full"
    assert listed["plain"]["result"]["source_file_available"] is None
    assert listed["running"]["result"]["source_retention_expires_at"] is None
    assert listed["old"]["metadata"] == {"route": "/process"}

    monkeypatch.setattr(history_retention, "cleanup_task_source_files", lambda task_id, metadata: {"source_retention_cleaned_at": "now"})
    outcome = history_retention.enforce_history_retention(
        "me", keep_count=0, artifact_days=0, source_days=1,
        list_jobs=lambda client_id: job_store.list_jobs_for_retention(db_path=db, client_id=client_id),
        update_result=lambda task_id, result, **kw: job_store.update_job_result(task_id, result, db_path=db, **kw),
        delete_jobs=lambda ids, client_id=None: job_store.delete_jobs(ids, db_path=db, client_id=client_id),
    )

    assert outcome["expired_source_count"] == 1
    expired = job_store.get_job("old", db_path=db)["result"]
    assert expired["source_file_available"] is False
    assert expired["source_retention_status"] == "expired"
    assert expired["transcript_text"] == "字" * 20000, "expiring the source keeps the rest of the result"
    untouched = job_store.get_job("fresh", db_path=db)["result"]
    assert untouched["source_file_available"] is True


# ── two steps of one task saving side by side ─────────────────────────────
#
# Requirement (owner, 2026-10-16, to-do batch three): the cut renders while the
# note is written. Each read the task, works for minutes and saves; whichever
# saves second must not erase what the first one saved.

def _two_steps_read_the_same_result(db):
    job_store.upsert_job(task_id="t", status="completed", result={
        "task_id": "t", "transcript_text": "原片的字幕", "summary_status": "pending",
        "artifacts": {"transcript_txt": {"kind": "transcript_txt", "filename": "a.txt"}},
    }, db_path=db)
    read = job_store.get_job("t", db_path=db)["result"]
    return dict(read), dict(read)


def _cut_saves(db, before):
    after = {
        **before, "transcript_text": "剪后的字幕", "debreath": {"status": "completed"},
        "artifacts": {**before["artifacts"], "debreath_media": {"kind": "debreath_media", "filename": "cut.mp4"}},
    }
    job_store.save_result_changes("t", before, after, db_path=db)


def _note_saves(db, before):
    after = {
        **before, "summary_markdown": "# 笔记", "summary_status": "completed", "visual_note": {"status": "completed"},
        "artifacts": {**before["artifacts"], "visual_note": {"kind": "visual_note", "filename": "note.md"}},
    }
    job_store.save_result_changes("t", before, after, db_path=db)


@pytest.mark.parametrize("cut_first", [True, False])
def test_the_cut_and_the_note_saving_from_the_same_read_both_keep_their_work(tmp_path, cut_first):
    db = tmp_path / "jobs.sqlite"
    cut_read, note_read = _two_steps_read_the_same_result(db)
    if cut_first:
        _cut_saves(db, cut_read)
        _note_saves(db, note_read)
    else:
        _note_saves(db, note_read)
        _cut_saves(db, cut_read)

    job = job_store.get_job("t", db_path=db)
    result = job["result"]
    assert result["transcript_text"] == "剪后的字幕"
    assert result["debreath"]["status"] == "completed"
    assert result["summary_markdown"] == "# 笔记"
    assert result["visual_note"]["status"] == "completed"
    assert set(result["artifacts"]) == {"transcript_txt", "debreath_media", "visual_note"}
    assert job["summary_status"] == "completed"


def test_a_field_a_step_removed_is_gone_and_one_it_left_alone_is_not_put_back(tmp_path):
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(task_id="t", status="completed", result={
        "task_id": "t", "playback_media_kind": "debreath_media", "lark_error": "旧的错误",
    }, db_path=db)
    stale = dict(job_store.get_job("t", db_path=db)["result"])
    # Another step clears the error after this one read.
    fresh = dict(job_store.get_job("t", db_path=db)["result"])
    job_store.save_result_changes("t", fresh, {**fresh, "lark_error": None}, db_path=db)

    after = dict(stale)
    after.pop("playback_media_kind")
    job_store.save_result_changes("t", stale, after, db_path=db)

    result = job_store.get_job("t", db_path=db)["result"]
    assert "playback_media_kind" not in result
    assert result["lark_error"] is None


def test_saves_from_many_threads_at_once_all_land(tmp_path):
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(task_id="t", status="completed", result={"task_id": "t"}, db_path=db)
    before = dict(job_store.get_job("t", db_path=db)["result"])
    writers = 12
    ready = threading.Barrier(writers)

    def write(i: int) -> None:
        ready.wait()
        job_store.save_result_changes("t", before, {**before, f"k{i}": i}, db_path=db)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = job_store.get_job("t", db_path=db)["result"]
    assert {f"k{i}" for i in range(writers)} <= set(result)
