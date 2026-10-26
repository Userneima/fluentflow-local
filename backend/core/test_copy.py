"""A service started on a copy of the task database for a test run.

``scripts/prepare_test_copy.py`` copies the owner's task database and prints a
start command with ``FLUENTFLOW_TEST_COPY=1`` (decision 8.11). The copied
tasks still point at his real files: the folders beside his recordings, the
note files in them, his Feishu documents, and (by absolute path) the artifacts
in his real data directory. A service on the copy must not write to any of
them, so with this switch on:

- startup carries on and re-queues nothing (``local_main.lifespan``);
- a request that would change a copied task is refused (``refusal``,
  checked once for every request in ``local_main``): only new tasks made in
  this copy can be worked on;
- the output folder of a copied task is never written (``output_folder.sync``);
- nothing is pushed to or pulled from Feishu, and nothing is exported there.

Which tasks were copied is the list the script writes beside the copy
(``TASKS_FILE``); without it, every task in the database when the service
first asks.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from typing import Any

from backend.core.runtime_paths import app_data_root, default_job_db_path
from backend.core.sqlite_connection import sqlite_connection

logger = logging.getLogger(__name__)

ENV = "FLUENTFLOW_TEST_COPY"
TASKS_FILE = "test_copy_tasks.json"
READ_ONLY = "这是测试副本里的旧任务，只能看。"
NO_FEISHU = "测试副本不往飞书写东西。"

_lock = threading.Lock()
_copied: frozenset[str] | None = None


def enabled() -> bool:
    return (os.environ.get(ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_copied() -> frozenset[str]:
    listed = app_data_root() / TASKS_FILE
    try:
        found = json.loads(listed.read_text(encoding="utf-8"))
        return frozenset(str(task_id) for task_id in found)
    except (OSError, ValueError, TypeError):
        logger.info("no list of copied tasks at %s; every task in the database now counts as copied", listed)
    try:
        with sqlite_connection(f"file:{default_job_db_path()}?mode=ro", uri=True) as db:
            return frozenset(row[0] for row in db.execute("select task_id from jobs"))
    except sqlite3.Error:
        return frozenset()


def copied_tasks() -> frozenset[str]:
    global _copied
    with _lock:
        if _copied is None:
            _copied = _read_copied()
        return _copied


def is_copied(task_id: Any) -> bool:
    """Whether this task came with the copy (and so points at real files)."""
    return enabled() and bool(task_id) and str(task_id) in copied_tasks()


def writable(task_id: Any) -> bool:
    """Whether work may write the files of this task: always, except on a
    test copy, where only the tasks made in it may (a note with no task
    beside it is the owner's own file)."""
    return not enabled() or (bool(task_id) and not is_copied(task_id))


def reset() -> None:
    """Read the list again (tests)."""
    global _copied
    with _lock:
        _copied = None


_TASK_IN_PATH = re.compile(r"/(?:jobs|tasks)/([0-9A-Za-z_-]+)")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_FEISHU_PATHS = ("/export-lark",)


def _named_tasks(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    found = [body.get(name) for name in ("task_id", "job_id")]
    for name in ("task_ids", "job_ids"):
        if isinstance(body.get(name), list):
            found += body[name]
    return [str(task_id) for task_id in found if task_id]


def refusal(method: str, path: str, body: Any = None) -> str | None:
    """Why this request is refused on a test copy, or None when it may go
    on: anything that changes a copied task (named in the address or the
    body), and any export to Feishu. Reading is always allowed."""
    if not enabled() or method.upper() in _SAFE_METHODS:
        return None
    if any(path.endswith(feishu) for feishu in _FEISHU_PATHS):
        return NO_FEISHU
    named = _TASK_IN_PATH.findall(path) + _named_tasks(body)
    if any(is_copied(task_id) for task_id in named):
        return READ_ONLY
    return None


async def middleware(request: Any, call_next: Any) -> Any:
    """Refuse what ``refusal`` names, for the page and agents alike."""
    if enabled() and request.method.upper() not in _SAFE_METHODS:
        body: Any = None
        if "json" in (request.headers.get("content-type") or ""):
            try:
                body = json.loads(await request.body() or b"null")
            except ValueError:
                body = None
        reason = refusal(request.method, request.url.path, body)
        if reason:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=409, content={"detail": reason, "code": "test_copy"})
    return await call_next(request)


__all__ = ["ENV", "NO_FEISHU", "READ_ONLY", "TASKS_FILE", "copied_tasks", "enabled", "is_copied", "middleware",
           "refusal", "reset", "writable"]
