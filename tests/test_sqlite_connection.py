"""Every database connection is closed when its block ends.

``with sqlite3.connect(...)`` commits but leaves the connection open until the
garbage collector finds it, and on Windows an open connection holds the file:
deleting or replacing the database then fails (found 2026-10-16).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.core.sqlite_connection import sqlite_connection

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def test_the_file_can_be_removed_right_after_the_block(tmp_path):
    db = tmp_path / "jobs.sqlite"
    with sqlite_connection(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
    db.unlink()
    assert not db.exists()


def test_a_failed_block_is_rolled_back_and_still_closed(tmp_path):
    db = tmp_path / "jobs.sqlite"
    with sqlite_connection(db) as conn:
        conn.execute("CREATE TABLE t (x)")
    try:
        with sqlite_connection(db) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with sqlite_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone() == (0,)
    db.unlink()


def test_no_backend_code_opens_a_connection_it_does_not_close():
    pattern = re.compile(r"with\s+sqlite3\.connect\(")
    offenders = [
        f"{path.relative_to(BACKEND)}:{number}"
        for path in BACKEND.rglob("*.py")
        if path.name != "sqlite_connection.py"  # its docstring names the pattern
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"use sqlite_connection instead: {offenders}"
