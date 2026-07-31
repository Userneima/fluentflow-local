#!/usr/bin/env python3
"""Merge historical job ownership into the local single-user identity.

Tasks created through the hosted localhost flow carry account
(``user:<id>``), desktop-pairing (``desktop-*``), legacy (``local-client``),
or NULL client ids. The local edition reads jobs under one identity
(``local-single-user`` by default), so on a single-user machine those
historical records are invisible until their ownership is merged.

Default is a DRY RUN that only reports what would change. ``--apply`` first
copies the jobs database to
``fluentflow_jobs.backup-before-owner-merge-<timestamp>.sqlite`` next to it
(the same convention as earlier manual merges), then rewrites
``jobs.client_id``. Steps, events, and artifacts key off ``task_id`` and are
not touched. Restore = replace the database file with the backup.

Do NOT run this on a genuinely multi-user deployment: it collapses every
owner into one.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.runtime_paths import default_job_db_path  # noqa: E402

DEFAULT_TARGET = "local-single-user"


def owner_counts(con: sqlite3.Connection, target: str) -> list[tuple[str, int]]:
    rows = con.execute(
        "SELECT COALESCE(client_id, '<null>') AS owner, COUNT(*) FROM jobs "
        "WHERE client_id IS NULL OR client_id != ? GROUP BY owner ORDER BY 2 DESC",
        (target,),
    ).fetchall()
    return [(str(owner), int(count)) for owner, count in rows]


def merge_ownership(db_path: Path, target: str, apply: bool) -> int:
    if not db_path.exists():
        print(f"任务数据库不存在：{db_path}")
        return 1
    con = sqlite3.connect(db_path)
    try:
        pending = owner_counts(con, target)
        total = sum(count for _, count in pending)
        if not pending:
            print(f"没有需要合并的任务：所有记录已属于 {target}")
            return 0
        print(f"数据库：{db_path}")
        print(f"将合并到 {target} 的任务：")
        for owner, count in pending:
            print(f"  {owner}: {count}")
        print(f"合计：{total}")
        if not apply:
            print("\n试运行结束（未修改任何数据）。确认无误后加 --apply 执行。")
            return 0
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.stem}.backup-before-owner-merge-{stamp}{db_path.suffix}")
        con.close()
        shutil.copy2(db_path, backup)
        con = sqlite3.connect(db_path)
        with con:
            changed = con.execute(
                "UPDATE jobs SET client_id = ? WHERE client_id IS NULL OR client_id != ?",
                (target, target),
            ).rowcount
        print(f"\n已备份：{backup}")
        print(f"已合并 {changed} 条任务到 {target}")
        return 0
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行合并（默认只预览）")
    parser.add_argument("--target", default=DEFAULT_TARGET, help=f"目标身份（默认 {DEFAULT_TARGET}）")
    parser.add_argument("--db", default=None, help="任务数据库路径（默认取运行时配置）")
    args = parser.parse_args()
    db_path = Path(args.db).expanduser() if args.db else default_job_db_path()
    return merge_ownership(db_path, args.target, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
