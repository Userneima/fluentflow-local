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


@contextmanager
def sqlite_connection(database: Any, **kwargs: Any) -> Iterator[sqlite3.Connection]:
    """``sqlite3.connect`` as a block: committed on success, rolled back on an
    error, and closed either way."""
    conn = sqlite3.connect(database, **kwargs)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


__all__ = ["sqlite_connection"]
