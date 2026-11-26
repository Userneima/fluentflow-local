"""One SQLite connection for the length of a ``with`` block, closed at its end.

``with sqlite3.connect(path) as conn`` commits or rolls back at the end of the
block but does not close the connection; it stays open until the garbage
collector finds it. On macOS and Linux that goes unnoticed. On Windows an open
connection holds the database file, so deleting, replacing or renaming it fails
with "another program is using this file" until then.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

# How long a connection waits for another one's write before it fails. SQLite's
# own default is five seconds; see ``sqlite_connection``.
LOCK_WAIT_SECONDS = 30.0


@contextmanager
def sqlite_connection(database: Any, **kwargs: Any) -> Iterator[sqlite3.Connection]:
    """``sqlite3.connect`` as a block: committed on success, rolled back on an
    error, and closed either way.

    One commit holds the file's write lock while it waits for the disk, which on
    the GitHub Windows machine was measured at tens of milliseconds and up to
    400 (decision 8.27), and task steps are written from worker threads while
    the page reads (decision 8.30). SQLite's own five-second wait turned such a
    lock into "database is locked" on that machine, so a connection waits
    ``LOCK_WAIT_SECONDS`` instead; a caller may pass its own ``timeout``."""
    conn = sqlite3.connect(database, **{"timeout": LOCK_WAIT_SECONDS, **kwargs})
    try:
        with conn:
            yield conn
    finally:
        conn.close()


__all__ = ["LOCK_WAIT_SECONDS", "sqlite_connection"]
