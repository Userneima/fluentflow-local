import json
import sqlite3

from backend.core import job_store


def _status(db, task_id):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT summary_status FROM jobs WHERE task_id = ?", (task_id,)).fetchone()[0]


def test_a_note_rewritten_after_a_failure_clears_the_failed_status(tmp_path):
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(task_id="t1", status="completed", summary_status="failed", db_path=db)

    job_store.update_job_result("t1", {"summary_status": "completed", "summary_markdown": "# 笔记"}, db_path=db)

    assert _status(db, "t1") == "completed"


def test_a_result_without_a_note_status_leaves_the_column_alone(tmp_path):
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(task_id="t1", status="completed", summary_status="failed", db_path=db)

    job_store.update_job_result("t1", {"transcript_text": "文字"}, db_path=db)

    assert _status(db, "t1") == "failed"


def test_startup_repairs_columns_left_behind_by_older_versions(tmp_path):
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(task_id="stale", status="completed", summary_status="failed", db_path=db)
    job_store.upsert_job(task_id="fine", status="completed", summary_status="completed", db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE jobs SET result_json = ? WHERE task_id IN ('stale', 'fine')",
            (json.dumps({"summary_status": "completed"}),),
        )

    assert job_store.sync_summary_status_column(db) == 1
    assert _status(db, "stale") == "completed"
    assert job_store.sync_summary_status_column(db) == 0
