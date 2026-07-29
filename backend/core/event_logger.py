"""Best-effort SQLite event logging for FluentFlow.

This module is intentionally small and defensive: analytics failures must never
interrupt the local processing workflow.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.runtime_paths import default_event_db_path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = default_event_db_path()

EVENT_COLUMNS = [
    "event_id",
    "task_id",
    "event_name",
    "created_at",
    "source_type",
    "source_filename",
    "source_duration_seconds",
    "source_file_size_mb",
    "transcript_length",
    "summary_length",
    "stage",
    "duration_seconds",
    "success",
    "error_reason",
    "export_target",
    "feishu_doc_url",
    "metadata",
]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_type TEXT,
    source_filename TEXT,
    source_duration_seconds REAL,
    source_file_size_mb REAL,
    transcript_length INTEGER,
    summary_length INTEGER,
    stage TEXT,
    duration_seconds REAL,
    success INTEGER,
    error_reason TEXT,
    export_target TEXT,
    feishu_doc_url TEXT,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_event_name ON events(event_name);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _success_value(success: bool | None) -> int | None:
    if success is None:
        return None
    return 1 if success else 0


def ensure_event_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)


def log_event(
    *,
    task_id: str,
    event_name: str,
    source_type: str | None = None,
    source_filename: str | None = None,
    source_duration_seconds: float | None = None,
    source_file_size_mb: float | None = None,
    transcript_length: int | None = None,
    summary_length: int | None = None,
    stage: str | None = None,
    duration_seconds: float | None = None,
    success: bool | None = None,
    error_reason: str | None = None,
    export_target: str | None = None,
    feishu_doc_url: str | None = None,
    metadata: dict[str, Any] | list[Any] | str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str | None:
    """Insert one analytics event and swallow all logging failures."""

    try:
        if not task_id or not event_name:
            return None

        ensure_event_db(db_path)
        event_id = uuid.uuid4().hex
        row = {
            "event_id": event_id,
            "task_id": task_id,
            "event_name": event_name,
            "created_at": _now_iso(),
            "source_type": source_type,
            "source_filename": source_filename,
            "source_duration_seconds": source_duration_seconds,
            "source_file_size_mb": source_file_size_mb,
            "transcript_length": transcript_length,
            "summary_length": summary_length,
            "stage": stage,
            "duration_seconds": duration_seconds,
            "success": _success_value(success),
            "error_reason": error_reason,
            "export_target": export_target,
            "feishu_doc_url": feishu_doc_url,
            "metadata": _json_dumps(metadata),
        }

        placeholders = ", ".join(["?"] * len(EVENT_COLUMNS))
        column_sql = ", ".join(EVENT_COLUMNS)
        values = [row[col] for col in EVENT_COLUMNS]
        with sqlite3.connect(Path(db_path)) as conn:
            conn.execute(
                f"INSERT INTO events ({column_sql}) VALUES ({placeholders})",
                values,
            )
        return event_id
    except Exception as exc:  # pragma: no cover - defensive isolation
        logger.warning("Event logging failed for %s: %s", event_name, exc)
        return None


def delete_events_for_tasks(task_ids: list[str] | tuple[str, ...], db_path: Path | str = DEFAULT_DB_PATH) -> int:
    ids = [str(task_id).strip() for task_id in task_ids if str(task_id).strip()]
    if not ids:
        return 0
    ensure_event_db(db_path)
    placeholders = ",".join("?" for _ in ids)
    with sqlite3.connect(Path(db_path)) as conn:
        cursor = conn.execute(f"DELETE FROM events WHERE task_id IN ({placeholders})", ids)
    return int(cursor.rowcount or 0)
