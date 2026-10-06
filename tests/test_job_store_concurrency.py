"""The job store under the load the queue actually puts on it."""

from __future__ import annotations

import sqlite3
import threading

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
    monkeypatch.setattr(history_retention, "cleanup_task_media_files", lambda task_id, metadata, **kw: {})
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
