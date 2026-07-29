#!/usr/bin/env python3
"""Export FluentFlow SQLite event logs as JSON or CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.runtime_paths import default_event_db_path  # noqa: E402

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


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM events ORDER BY created_at, event_id"
        ).fetchall()
    return [dict(row) for row in rows]


def normalize_for_json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        item = dict(row)
        if item.get("success") is not None:
            item["success"] = bool(item["success"])
        metadata = item.get("metadata")
        if metadata:
            try:
                item["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                pass
        normalized.append(item)
    return normalized


def write_json(rows: list[dict[str, Any]], output: Path | None) -> None:
    text = json.dumps(normalize_for_json(rows), ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def write_csv(rows: list[dict[str, Any]], output: Path | None) -> None:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        handle = output.open("w", newline="", encoding="utf-8")
        close_handle = True
    else:
        handle = sys.stdout
        close_handle = False
    try:
        writer = csv.DictWriter(handle, fieldnames=EVENT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col) for col in EVENT_COLUMNS})
    finally:
        if close_handle:
            handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    rows = load_rows(args.db)
    if args.format == "json":
        write_json(rows, args.output)
    else:
        write_csv(rows, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
