"""SQLite-backed task and result persistence for FluentFlow."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.core.runtime_paths import default_job_db_path
from backend.core.job_views import job_list_row
from backend.core.result_schema import normalize_result_for_read, normalize_result_for_storage
from backend.core.title_display import display_title_for_user

logger = logging.getLogger(__name__)

# Snapshot of the configured path at import time, kept for callers that want to
# report "which database would this process use". It must NEVER be a default
# argument: Python binds defaults once at import, so every function that took
# `db_path=DEFAULT_DB_PATH` ignored any later redirection of the configured
# path — a test that pointed FluentFlow at a temp database still wrote job rows
# into the developer's real one. Use `resolve_db_path` instead.
DEFAULT_DB_PATH = default_job_db_path()


def resolve_db_path(db_path: Path | str | None = None) -> Path:
    """Resolve an explicit path, or the configured default at call time."""
    if db_path is None:
        return default_job_db_path()
    return Path(db_path)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    task_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    client_id TEXT,
    stage TEXT,
    progress REAL,
    source_type TEXT,
    source_filename TEXT,
    source_file_size_mb REAL,
    summary_status TEXT,
    error_reason TEXT,
    result_json TEXT,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS job_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    step_key TEXT NOT NULL UNIQUE,
    step_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    run_after_at TEXT,
    locked_at TEXT,
    lock_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    input_json TEXT,
    result_json TEXT,
    error_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_steps_status_priority ON job_steps(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_job_steps_task ON job_steps(task_id, id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _result_json_dumps(result: dict[str, Any] | None) -> str | None:
    return _json_dumps(normalize_result_for_storage(result))


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _merged_metadata_for_upsert(
    conn: sqlite3.Connection,
    task_id: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if metadata is None:
        return None
    row = conn.execute("SELECT metadata_json FROM jobs WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        return metadata
    existing = _json_loads(row[0])
    if not isinstance(existing, dict):
        return metadata
    return {**existing, **metadata}


def ensure_job_db(db_path: Path | str | None = None) -> None:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "client_id" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN client_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_client_updated_at ON jobs(client_id, updated_at)")


def create_job_if_absent(
    *,
    task_id: str,
    status: str,
    client_id: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    db_path: Path | str | None = None,
) -> bool:
    """Atomically reserve a task id without changing an existing job."""
    if not task_id:
        return False
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path, timeout=10) as conn:
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                task_id, created_at, updated_at, status, client_id, stage, progress
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO NOTHING
            """,
            (task_id, now, now, status, client_id, stage, progress),
        )
        return bool(cursor.rowcount)


def upsert_job(
    *,
    task_id: str,
    status: str,
    client_id: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    source_type: str | None = None,
    source_filename: str | None = None,
    source_file_size_mb: float | None = None,
    summary_status: str | None = None,
    error_reason: str | None = None,
    result: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> None:
    if not task_id:
        return
    db_path = resolve_db_path(db_path)
    last_exc = None
    for attempt in range(3):
        try:
            ensure_job_db(db_path)
            now = _now_iso()
            with sqlite3.connect(db_path) as conn:
                merged_metadata = _merged_metadata_for_upsert(conn, task_id, metadata)
                conn.execute(
                    """
                    INSERT INTO jobs (
                        task_id, created_at, updated_at, status, client_id, stage, progress,
                        source_type, source_filename, source_file_size_mb, summary_status,
                        error_reason, result_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        updated_at=excluded.updated_at,
                        status=excluded.status,
                        client_id=COALESCE(excluded.client_id, jobs.client_id),
                        stage=COALESCE(excluded.stage, jobs.stage),
                        progress=COALESCE(excluded.progress, jobs.progress),
                        source_type=COALESCE(excluded.source_type, jobs.source_type),
                        source_filename=COALESCE(excluded.source_filename, jobs.source_filename),
                        source_file_size_mb=COALESCE(excluded.source_file_size_mb, jobs.source_file_size_mb),
                        summary_status=COALESCE(excluded.summary_status, jobs.summary_status),
                        error_reason=COALESCE(excluded.error_reason, jobs.error_reason),
                        result_json=COALESCE(excluded.result_json, jobs.result_json),
                        metadata_json=COALESCE(excluded.metadata_json, jobs.metadata_json)
                    WHERE jobs.status != 'cancelled' OR excluded.status = 'cancelled'
                    """,
                    (
                        task_id,
                        now,
                        now,
                        status,
                        client_id,
                        stage,
                        progress,
                        source_type,
                        source_filename,
                        source_file_size_mb,
                        summary_status,
                        error_reason,
                        _result_json_dumps(result),
                        _json_dumps(merged_metadata),
                    ),
                )
            return
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.1 * (attempt + 1))
    logger.error("Job store update failed for %s after 3 retries: %s", task_id, last_exc)
    raise last_exc


def get_job(
    task_id: str,
    db_path: Path | str | None = None,
    client_id: str | None = None,
) -> dict[str, Any] | None:
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if client_id is not None:
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_id = ? AND client_id = ?",
                (task_id, client_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM jobs WHERE task_id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(
    limit: int = 50,
    db_path: Path | str | None = None,
    client_id: str | None = None,
    include_result: bool = True,
) -> list[dict[str, Any]]:
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    safe_limit = max(1, min(int(limit or 50), 200))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if client_id is not None:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE client_id = ? ORDER BY updated_at DESC LIMIT ?",
                (client_id, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
    return [_row_to_dict(row) if include_result else _row_to_summary_dict(row) for row in rows]


def recent_local_folders(
    limit: int = 8,
    db_path: Path | str | None = None,
) -> list[str]:
    """Folders this machine has already been pointed at, newest first.

    Two things need them and both are the same question — where does this owner
    keep recordings? The file dialog opens in the newest one instead of wherever
    Finder happened to be last, and a dropped file is looked for in all of them.

    Only folders that still exist are returned: a directory that has been moved or
    unplugged is not somewhere to open a dialog, and it is not somewhere a file
    can be found.
    """
    resolved = resolve_db_path(db_path)
    ensure_job_db(resolved)
    with sqlite3.connect(resolved) as conn:
        rows = conn.execute(
            "SELECT metadata_json FROM jobs WHERE metadata_json LIKE '%folder_intake%' "
            "ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()
    folders: list[str] = []
    for (raw,) in rows:
        try:
            intake = (json.loads(raw or "{}") or {}).get("folder_intake") or {}
        except (TypeError, ValueError):
            continue
        folder = str(intake.get("folder") or "").strip()
        if not folder or folder in folders:
            continue
        if Path(folder).is_dir():
            folders.append(folder)
        if len(folders) >= max(1, limit):
            break
    return folders


def list_jobs_by_statuses(
    statuses: tuple[str, ...] | list[str],
    *,
    db_path: Path | str | None = None,
    include_result: bool = False,
) -> list[dict[str, Any]]:
    """Return every job in the requested states without the UI list cap."""
    status_values = [str(status).strip() for status in statuses if str(status).strip()]
    if not status_values:
        return []
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    placeholders = ",".join("?" for _ in status_values)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
            status_values,
        ).fetchall()
    return [
        _row_to_dict(row) if include_result else _row_to_summary_dict(row)
        for row in rows
    ]


def list_job_summaries(
    limit: int = 50,
    db_path: Path | str | None = None,
    client_id: str | None = None,
) -> list[dict[str, Any]]:
    return list_jobs(limit=limit, db_path=db_path, client_id=client_id, include_result=False)


def list_jobs_for_retention(
    db_path: Path | str | None = None,
    client_id: str | None = None,
) -> list[dict[str, Any]]:
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if client_id is not None:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE client_id = ? ORDER BY updated_at DESC",
                (client_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC").fetchall()
    return [_row_to_dict(row) for row in rows]


def update_job_result(
    task_id: str,
    result: dict[str, Any],
    db_path: Path | str | None = None,
    client_id: str | None = None,
    touch_updated_at: bool = True,
) -> dict[str, Any] | None:
    if not task_id:
        return None
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if client_id is not None:
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_id = ? AND client_id = ?",
                (task_id, client_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM jobs WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        if touch_updated_at:
            conn.execute(
                "UPDATE jobs SET updated_at = ?, result_json = ? WHERE task_id = ?",
                (now, _result_json_dumps(result), task_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET result_json = ? WHERE task_id = ?",
                (_result_json_dumps(result), task_id),
            )
        updated = conn.execute("SELECT * FROM jobs WHERE task_id = ?", (task_id,)).fetchone()
    return _row_to_dict(updated) if updated else None


# The only parts of a result a note regeneration would overwrite.
#
# The compare-and-swap below used to require the *whole* result dict to be
# byte-identical to the snapshot taken before generation. That made every
# unrelated background write veto a finished note: a retention sweep, an
# artifact refresh, or — as actually happened — a summary autosave that stored
# the very same text and only bumped `summary_edited_at`. Nine minutes of
# generation were discarded because a timestamp moved.
#
# Comparing the note and the transcript it was written from keeps the guarantee
# that matters (a real edit is never clobbered) and drops the false positives.
NOTE_CONFLICT_FIELDS: Final[tuple[str, ...]] = ("summary_markdown", "transcript_text")


def note_conflict_fingerprint(result: Any) -> tuple[str, ...] | None:
    """The editable state a regeneration competes with, or None for no result."""
    data = normalize_result_for_read(result)
    if not isinstance(data, dict):
        return None
    return tuple(str(data.get(field) or "") for field in NOTE_CONFLICT_FIELDS)


def finalize_job_result_if_unchanged(
    task_id: str,
    expected_result: Any,
    result: dict[str, Any],
    *,
    status: str,
    stage: str,
    progress: float | None,
    summary_status: str,
    error_reason: str | None = None,
    db_path: Path | str | None = None,
    client_id: str | None = None,
) -> dict[str, Any] | None:
    """Finalize an existing job only while its result still matches the caller's snapshot."""
    task_id_value = str(task_id or "").strip()
    if not task_id_value:
        return None
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if client_id is None:
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_id = ?",
                (task_id_value,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_id = ? AND client_id = ?",
                (task_id_value, client_id),
            ).fetchone()
        current_fingerprint = (
            note_conflict_fingerprint(_json_loads(row["result_json"])) if row else None
        )
        expected_fingerprint = note_conflict_fingerprint(expected_result)
        if row is None or row["status"] == "cancelled" or current_fingerprint != expected_fingerprint:
            conn.rollback()
            return None
        conn.execute(
            """
            UPDATE jobs
            SET updated_at = ?, status = ?, stage = ?, progress = COALESCE(?, progress),
                summary_status = ?, error_reason = ?, result_json = ?
            WHERE task_id = ?
            """,
            (
                now,
                status,
                stage,
                progress,
                summary_status,
                error_reason,
                _result_json_dumps(result),
                task_id_value,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM jobs WHERE task_id = ?",
            (task_id_value,),
        ).fetchone()
        conn.commit()
    return _row_to_dict(updated) if updated else None


def append_job_result_list_item(
    task_id: str,
    field: str,
    item: dict[str, Any],
    *,
    db_path: Path | str | None = None,
    client_id: str | None = None,
) -> dict[str, Any] | None:
    """Append one result item atomically while preserving concurrent edits."""
    task_id_value = str(task_id or "").strip()
    field_value = str(field or "").strip()
    if not task_id_value or not field_value:
        return None
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if client_id is None:
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_id = ?",
                (task_id_value,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_id = ? AND client_id = ?",
                (task_id_value, client_id),
            ).fetchone()
        if row is None:
            conn.rollback()
            return None
        result = normalize_result_for_read(_json_loads(row["result_json"]))
        next_result = dict(result) if isinstance(result, dict) else {}
        current_items = next_result.get(field_value)
        items = list(current_items) if isinstance(current_items, list) else []
        next_result[field_value] = [*items, dict(item)]
        conn.execute(
            "UPDATE jobs SET updated_at = ?, result_json = ? WHERE task_id = ?",
            (now, _result_json_dumps(next_result), task_id_value),
        )
        updated = conn.execute(
            "SELECT * FROM jobs WHERE task_id = ?",
            (task_id_value,),
        ).fetchone()
        conn.commit()
    return _row_to_dict(updated) if updated else None


def enqueue_job_step(
    *,
    task_id: str,
    step_type: str,
    input: dict[str, Any] | None = None,
    step_key: str | None = None,
    priority: int = 100,
    max_attempts: int = 1,
    run_after_at: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    task_id = str(task_id or "").strip()
    step_type = str(step_type or "").strip()
    if not task_id or not step_type:
        return None
    key = str(step_key or f"{task_id}:{step_type}").strip()
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO job_steps (
                task_id, step_key, step_type, status, priority, run_after_at,
                attempt_count, max_attempts, input_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(step_key) DO UPDATE SET
                updated_at=excluded.updated_at,
                input_json=CASE
                    WHEN job_steps.status IN ('queued', 'failed', 'cancelled') THEN excluded.input_json
                    ELSE job_steps.input_json
                END,
                status=CASE
                    WHEN job_steps.status IN ('failed', 'cancelled') THEN 'queued'
                    ELSE job_steps.status
                END,
                priority=CASE
                    WHEN job_steps.status IN ('queued', 'failed', 'cancelled') THEN excluded.priority
                    ELSE job_steps.priority
                END,
                run_after_at=CASE
                    WHEN job_steps.status IN ('queued', 'failed', 'cancelled') THEN excluded.run_after_at
                    ELSE job_steps.run_after_at
                END,
                lock_id=CASE
                    WHEN job_steps.status IN ('failed', 'cancelled') THEN NULL
                    ELSE job_steps.lock_id
                END,
                locked_at=CASE
                    WHEN job_steps.status IN ('failed', 'cancelled') THEN NULL
                    ELSE job_steps.locked_at
                END,
                attempt_count=CASE
                    WHEN job_steps.status IN ('failed', 'cancelled') THEN 0
                    ELSE job_steps.attempt_count
                END,
                started_at=CASE
                    WHEN job_steps.status IN ('failed', 'cancelled') THEN NULL
                    ELSE job_steps.started_at
                END,
                finished_at=CASE
                    WHEN job_steps.status IN ('failed', 'cancelled') THEN NULL
                    ELSE job_steps.finished_at
                END,
                error_reason=CASE
                    WHEN job_steps.status IN ('failed', 'cancelled') THEN NULL
                    ELSE job_steps.error_reason
                END
            """,
            (
                task_id,
                key,
                step_type,
                int(priority),
                run_after_at,
                max(1, int(max_attempts or 1)),
                _json_dumps(input or {}),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM job_steps WHERE step_key = ?", (key,)).fetchone()
    return _step_row_to_dict(row) if row else None


def acquire_next_job_step(
    *,
    step_types: tuple[str, ...] | list[str] | None = None,
    lock_timeout_seconds: float = 3600,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    lock_id = uuid.uuid4().hex
    step_type_values = [str(value).strip() for value in (step_types or []) if str(value).strip()]
    cutoff_ts = time.time() - max(float(lock_timeout_seconds or 3600), 60.0)
    cutoff = datetime.fromtimestamp(cutoff_ts, timezone.utc).astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        type_clause = ""
        if step_type_values:
            placeholders = ",".join("?" for _ in step_type_values)
            type_clause = f" AND step_type IN ({placeholders})"
        row = conn.execute(
            f"""
            SELECT * FROM job_steps
            WHERE (
                status = 'queued'
                OR (status = 'running' AND (locked_at IS NULL OR locked_at < ?))
            )
            AND (run_after_at IS NULL OR run_after_at <= ?)
            {type_clause}
            ORDER BY priority ASC, id ASC
            LIMIT 1
            """,
            [cutoff, now, *step_type_values],
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            """
            UPDATE job_steps
            SET status='running',
                locked_at=?,
                lock_id=?,
                attempt_count=attempt_count + 1,
                updated_at=?,
                started_at=COALESCE(started_at, ?),
                error_reason=NULL
            WHERE id=?
            """,
            (now, lock_id, now, now, row["id"]),
        )
        updated = conn.execute("SELECT * FROM job_steps WHERE id = ?", (row["id"],)).fetchone()
        conn.commit()
    return _step_row_to_dict(updated) if updated else None


def complete_job_step(
    step_id: int,
    *,
    lock_id: str,
    result: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    return _finish_job_step(
        step_id,
        lock_id=lock_id,
        status="completed",
        result=result,
        error_reason=None,
        db_path=resolve_db_path(db_path),
    )


def heartbeat_job_step(
    step_id: int,
    *,
    lock_id: str,
    db_path: Path | str | None = None,
) -> bool:
    """Extend a running step lease only while this worker still owns it."""
    if not step_id or not str(lock_id or "").strip():
        return False
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE job_steps
            SET locked_at=?, updated_at=?
            WHERE id=? AND status='running' AND lock_id=?
            """,
            (now, now, step_id, lock_id),
        )
        return bool(cursor.rowcount)


def fail_job_step(
    step_id: int,
    *,
    lock_id: str,
    error_reason: str,
    result: dict[str, Any] | None = None,
    retry: bool = False,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    db_path = resolve_db_path(db_path)
    if retry:
        ensure_job_db(db_path)
        now = _now_iso()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM job_steps WHERE id = ? AND status = 'running' AND lock_id = ?",
                (step_id, lock_id),
            ).fetchone()
            if row is None:
                return None
            can_retry = int(row["attempt_count"] or 0) < int(row["max_attempts"] or 1)
            status = "queued" if can_retry else "failed"
            cursor = conn.execute(
                """
                UPDATE job_steps
                SET status=?, updated_at=?, finished_at=?,
                    locked_at=NULL, lock_id=NULL, error_reason=?, result_json=?
                WHERE id=? AND status='running' AND lock_id=?
                """,
                (
                    status,
                    now,
                    None if can_retry else now,
                    error_reason,
                    _json_dumps(result),
                    step_id,
                    lock_id,
                ),
            )
            if not cursor.rowcount:
                return None
            updated = conn.execute(
                "SELECT * FROM job_steps WHERE id = ?",
                (step_id,),
            ).fetchone()
        return _step_row_to_dict(updated) if updated else None
    return _finish_job_step(
        step_id,
        lock_id=lock_id,
        status="failed",
        result=result,
        error_reason=error_reason,
        db_path=db_path,
    )


def cancel_job_steps(
    task_id: str,
    *,
    db_path: Path | str | None = None,
) -> int:
    task_id = str(task_id or "").strip()
    if not task_id:
        return 0
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE job_steps
            SET status='cancelled', updated_at=?, finished_at=?, locked_at=NULL, lock_id=NULL
            WHERE task_id = ? AND status IN ('queued', 'running')
            """,
            (now, now, task_id),
        )
        return int(cursor.rowcount or 0)


def list_job_steps(
    *,
    task_id: str | None = None,
    statuses: tuple[str, ...] | list[str] | None = None,
    limit: int = 100,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    safe_limit = max(1, min(int(limit or 100), 500))
    where: list[str] = []
    params: list[Any] = []
    if task_id:
        where.append("task_id = ?")
        params.append(str(task_id))
    status_values = [str(value).strip() for value in (statuses or []) if str(value).strip()]
    if status_values:
        placeholders = ",".join("?" for _ in status_values)
        where.append(f"status IN ({placeholders})")
        params.extend(status_values)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM job_steps {where_sql} ORDER BY id ASC LIMIT ?",
            [*params, safe_limit],
        ).fetchall()
    return [_step_row_to_dict(row) for row in rows]


def requeue_running_job_steps(
    *,
    db_path: Path | str | None = None,
) -> int:
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE job_steps
            SET status='queued', updated_at=?, locked_at=NULL, lock_id=NULL
            WHERE status='running'
            """,
            (now,),
        )
        return int(cursor.rowcount or 0)


def _finish_job_step(
    step_id: int,
    *,
    lock_id: str,
    status: str,
    result: dict[str, Any] | None,
    error_reason: str | None,
    db_path: Path,
) -> dict[str, Any] | None:
    ensure_job_db(db_path)
    now = _now_iso()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            UPDATE job_steps
            SET status=?, updated_at=?, finished_at=?, locked_at=NULL, lock_id=NULL,
                result_json=?, error_reason=?
            WHERE id=? AND status='running' AND lock_id=?
            """,
            (status, now, now, _json_dumps(result), error_reason, step_id, lock_id),
        )
        if not cursor.rowcount:
            return None
        row = conn.execute("SELECT * FROM job_steps WHERE id = ?", (step_id,)).fetchone()
    return _step_row_to_dict(row) if row else None


def migrate_job_display_titles(db_path: Path | str | None = None) -> int:
    """Backfill raw/display title semantics for existing job rows."""
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    changed = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        for row in rows:
            result = _json_loads(row["result_json"])
            metadata = _json_loads(row["metadata_json"])
            result_dict = result if isinstance(result, dict) else {}
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            raw_video_source = metadata_dict.get("video_source")
            video_source = raw_video_source if isinstance(raw_video_source, dict) else {}

            raw_title = str(
                metadata_dict.get("raw_title")
                or video_source.get("raw_title")
                or result_dict.get("raw_title")
                or video_source.get("title")
                or result_dict.get("filename")
                or row["source_filename"]
                or ""
            ).strip()
            display_candidate = str(
                metadata_dict.get("display_title")
                or video_source.get("display_title")
                or result_dict.get("display_title")
                or raw_title
            ).strip()
            display_title = display_title_for_user(display_candidate, row["source_filename"]).strip()
            if not display_title:
                continue

            next_metadata = dict(metadata_dict)
            next_result = dict(result_dict)
            row_changed = False
            if next_metadata.get("raw_title") != raw_title:
                next_metadata["raw_title"] = raw_title
                row_changed = True
            if next_metadata.get("display_title") != display_title:
                next_metadata["display_title"] = display_title
                row_changed = True
            if isinstance(raw_video_source, dict):
                next_video_source = dict(video_source)
                if next_video_source.get("raw_title") != raw_title:
                    next_video_source["raw_title"] = raw_title
                    row_changed = True
                if next_video_source.get("display_title") != display_title:
                    next_video_source["display_title"] = display_title
                    row_changed = True
                next_metadata["video_source"] = next_video_source
            if next_result:
                if next_result.get("raw_title") != raw_title:
                    next_result["raw_title"] = raw_title
                    row_changed = True
                if next_result.get("display_title") != display_title:
                    next_result["display_title"] = display_title
                    row_changed = True
            if not row_changed:
                continue
            conn.execute(
                "UPDATE jobs SET result_json = ?, metadata_json = ? WHERE task_id = ?",
                (
                    _json_dumps(next_result) if result is not None else row["result_json"],
                    _json_dumps(next_metadata),
                    row["task_id"],
                ),
            )
            changed += 1
    return changed


def delete_jobs(
    task_ids: list[str] | tuple[str, ...],
    db_path: Path | str | None = None,
    client_id: str | None = None,
) -> int:
    ids = [str(task_id).strip() for task_id in task_ids if str(task_id).strip()]
    if not ids:
        return 0
    db_path = resolve_db_path(db_path)
    ensure_job_db(db_path)
    placeholders = ",".join("?" for _ in ids)
    params: list[Any] = list(ids)
    where = f"task_id IN ({placeholders})"
    if client_id is not None:
        where += " AND client_id = ?"
        params.append(client_id)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"SELECT task_id FROM jobs WHERE {where}", params).fetchall()
        allowed_ids = [str(row[0]) for row in rows]
        if allowed_ids:
            step_placeholders = ",".join("?" for _ in allowed_ids)
            conn.execute(f"DELETE FROM job_steps WHERE task_id IN ({step_placeholders})", allowed_ids)
        cursor = conn.execute(f"DELETE FROM jobs WHERE {where}", params)
        return int(cursor.rowcount or 0)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "client_id": row["client_id"],
        "stage": row["stage"],
        "progress": row["progress"],
        "source_type": row["source_type"],
        "source_filename": row["source_filename"],
        "source_file_size_mb": row["source_file_size_mb"],
        "summary_status": row["summary_status"],
        "error_reason": row["error_reason"],
        "result": normalize_result_for_read(_json_loads(row["result_json"])),
        "metadata": _json_loads(row["metadata_json"]),
    }


def _step_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "step_key": row["step_key"],
        "step_type": row["step_type"],
        "status": row["status"],
        "priority": row["priority"],
        "run_after_at": row["run_after_at"],
        "locked_at": row["locked_at"],
        "lock_id": row["lock_id"],
        "attempt_count": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "input": _json_loads(row["input_json"]) or {},
        "result": _json_loads(row["result_json"]),
        "error_reason": row["error_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _row_to_summary_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = job_list_row(_json_loads(row["result_json"]))
    return {
        "task_id": row["task_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "client_id": row["client_id"],
        "stage": row["stage"],
        "progress": row["progress"],
        "source_type": row["source_type"],
        "source_filename": row["source_filename"],
        "source_file_size_mb": row["source_file_size_mb"],
        "summary_status": row["summary_status"],
        "error_reason": row["error_reason"],
        "result": result,
        "metadata": _json_loads(row["metadata_json"]),
    }
