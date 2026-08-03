#!/usr/bin/env python3
"""Rewrite stored job results without their duplicate copies.

The transcription pipeline used to store the transcript three times and its
segments three times. New writes are already compacted by
`normalize_result_for_storage`; this brings existing rows in line.

Nothing is trusted:

* the database is copied to a timestamped backup first,
* every row is compacted in memory and re-read through the canonical accessors,
  and a row whose readable content changes at all is left exactly as it was,
* the write happens in one transaction, so an interrupted run changes nothing.

`--check` reports what would be reclaimed and writes nothing. `--restore` puts
the newest backup back.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.result_schema import (  # noqa: E402
    canonical_display_segments,
    canonical_raw_segments,
    normalize_result_for_read,
    normalize_result_for_storage,
)
from backend.core.runtime_paths import default_job_db_path, resolve_workspace  # noqa: E402

BACKUP_SUFFIX = ".before-compact-"


def _service_is_running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _readable(result):
    """Everything a consumer can observe. Must be identical after compaction."""
    read = normalize_result_for_read(result)
    if not isinstance(read, dict):
        return read
    return {
        "summary_markdown": read.get("summary_markdown"),
        "transcript_text": read.get("transcript_text"),
        "raw_transcript_text": read.get("raw_transcript_text"),
        "cleaned_transcript_text": read.get("cleaned_transcript_text") or read.get("transcript_text"),
        "raw_segments": canonical_raw_segments(read),
        "display_segments": canonical_display_segments(read),
        "subtitle_mode": read.get("subtitle_mode"),
    }


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _backups(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.name}{BACKUP_SUFFIX}*"), reverse=True)


def _restore(db_path: Path) -> int:
    backups = _backups(db_path)
    if not backups:
        print(f"No backup found next to {db_path}.")
        return 1
    newest = backups[0]
    shutil.copy2(newest, db_path)
    print(f"Restored {db_path}\n     from {newest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Drop duplicate copies from stored job results.")
    parser.add_argument("--check", action="store_true", help="Report what would change; write nothing")
    parser.add_argument("--restore", action="store_true", help="Put the newest backup back")
    parser.add_argument("--port", type=int, default=8000, help="Local service port to check first (default: 8000)")
    parser.add_argument("--db", help="Database path (default: the resolved workspace's)")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser() if args.db else default_job_db_path()
    if not db_path.exists():
        print(f"No job database at {db_path}")
        return 1
    if args.restore:
        return _restore(db_path)

    print(f"Workspace: {resolve_workspace().describe()}")
    print(f"Database : {db_path}")

    # Only the live workspace database is at risk from a running service. An
    # explicit --db elsewhere (a copy, a rehearsal) must stay runnable, or the
    # only way to rehearse this migration is against the data it would damage.
    targets_live_db = db_path.resolve() == default_job_db_path().resolve()
    if not args.check and targets_live_db and _service_is_running(args.port):
        print(f"\nFluentFlow is still running on port {args.port}.")
        print("Stop it first — rewriting rows under a live service risks a torn read.")
        return 1

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = list(conn.execute("SELECT task_id, result_json FROM jobs WHERE result_json IS NOT NULL"))

    planned: list[tuple[str, str]] = []
    skipped: list[str] = []
    before_total = after_total = 0
    for row in rows:
        original = row["result_json"] or ""
        try:
            result = json.loads(original)
        except json.JSONDecodeError:
            skipped.append(f"{row['task_id']} (unreadable JSON)")
            continue
        compacted = normalize_result_for_storage(result)
        # The whole safety argument: if a reader could tell the difference, this
        # row is not touched.
        if _readable(result) != _readable(compacted):
            skipped.append(f"{row['task_id']} (readable content would change)")
            continue
        encoded = json.dumps(compacted, ensure_ascii=False, sort_keys=True)
        before_total += len(original)
        after_total += len(encoded)
        if encoded != original:
            planned.append((row["task_id"], encoded))

    print(f"\nRows            : {len(rows)}")
    print(f"To rewrite      : {len(planned)}")
    print(f"Stored JSON     : {_format_size(before_total)} -> {_format_size(after_total)}"
          f"  ({_format_size(before_total - after_total)} reclaimed)")
    for note in skipped:
        print(f"  left untouched: {note}")

    if args.check:
        print("\n--check: nothing was written.")
        return 0
    if not planned:
        print("\nAlready compact. Nothing to do.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}{BACKUP_SUFFIX}{stamp}")
    shutil.copy2(db_path, backup)
    print(f"\nBackup          : {backup}")

    with sqlite3.connect(db_path, timeout=15) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for task_id, encoded in planned:
            conn.execute("UPDATE jobs SET result_json = ? WHERE task_id = ?", (encoded, task_id))
    with sqlite3.connect(db_path) as conn:
        conn.execute("VACUUM")

    print(f"Rewrote {len(planned)} row(s). File is now {_format_size(db_path.stat().st_size)}.")
    print("Start FluentFlow and confirm your records. To undo:")
    print(f"  python {Path(__file__).name} --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
