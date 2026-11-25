"""A service started on a copy of the task database for a test run.

``scripts/prepare_test_copy.py`` copies the owner's task database and prints a
start command with ``FLUENTFLOW_TEST_COPY=1`` (decision 8.11). The copied
tasks still point at his real files: the folders beside his recordings, the
note files in them, his Feishu documents, and (by absolute path) the artifacts
in his real data directory. A service on the copy must not write to any of
them, so with this switch on:

- startup carries on and re-queues nothing (``local_main.lifespan``);
- a request that would change a copied task is refused (``refusal``,
  checked once for every request in ``local_main``), whether it names the
  task in its address, its query, a JSON body (with or without its
  Content-Type) or a form, under any field naming a task or a job: only new
  tasks made in this copy can be worked on; the one lookup of an existing
  task a form names (``local_entry_guards.require_owned_task_id``) refuses it
  too, for an upload too large to read here; a body of no stated length
  larger than that is refused outright;
- no write request is let through while a place the copy writes resolves
  outside it (``paths_outside``): a variable naming another place, or a link
  in the copy leading out of it;
- nothing a saved change sets off writes beside a copied task's note or
  calls a model for it (every ``job_store`` result listener asks ``writable``
  or ``enabled``), and its output folder is never written
  (``output_folder.sync``);
- nothing is pushed to or pulled from Feishu, and nothing is exported there,
  for a copied task or a new one: the agent's export is refused unless it is
  a PDF, and both Feishu exporters refuse on their own; a PDF is written only
  inside the copy (``export_dir``);
- the page it serves sends every request back to this service
  (``page_config``), whatever its build or its port says;
- the page may come from a build of its own (``FRONTEND_ENV``), to look at a
  change to the page that is not pushed (decision 8.29).

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
from pathlib import Path
from typing import Any

from backend.core.runtime_paths import app_data_root, default_job_db_path
from backend.core.sqlite_connection import sqlite_connection

logger = logging.getLogger(__name__)

ENV = "FLUENTFLOW_TEST_COPY"
FRONTEND_ENV = "FLUENTFLOW_TEST_FRONTEND_DIR"
PAGE_CONFIG_PATH = "/assets/config.js"
TASKS_FILE = "test_copy_tasks.json"
READ_ONLY = "这是测试副本里的旧任务，只能看。"
NO_FEISHU = "测试副本不往飞书写东西。"
OUTSIDE_COPY = "测试副本只把导出的文件写进副本自己的文件夹。"
OUTSIDE_PATHS = "测试副本有要写的位置在副本外面（{}），写请求一律不放行；删掉指出去的链接，或用 prepare_test_copy.py 重新准备副本、用它打印的命令启动。"
UNREAD_BODY = "测试副本看不出这个请求改的是哪个任务（没写长度，又超过 16 MB），没有放行。"
PDF_DIRNAME = "exports"
# Every path the service writes and takes from its own variable before the
# data directory (``runtime_paths.runtime_path``), and the folder of link
# tasks: the start command (scripts/prepare_test_copy.py) sets each inside
# the copy, since the service loads a ``.env`` above the code it runs (the
# main checkout's, from a worktree) and a real path named there would win
# over FLUENTFLOW_DATA_DIR. tests/test_test_copy_writes.py finds every such
# variable in the code.
PINNED_PATHS = {
    "FLUENTFLOW_CONFIG_PATH": "fluentflow_config.json",
    "FLUENTFLOW_JOB_DB_PATH": "fluentflow_jobs.sqlite",
    "FLUENTFLOW_EVENT_DB_PATH": "fluentflow_events.sqlite",
    "FLUENTFLOW_SOURCE_DIR": "sources",
    "FLUENTFLOW_ARTIFACT_DIR": "artifacts",
    "FLUENTFLOW_EDITED_TRANSCRIPT_DIR": "edited_transcripts",
    "FLUENTFLOW_TRANSCRIPT_EDIT_RECORDS_DIR": "transcript_edit_records",
    "FLUENTFLOW_VIDEO_SOURCE_DIR": "video_sources",
    "FLUENTFLOW_DIARIZATION_MODEL_DIR": "models/speaker",
    "FLUENTFLOW_PYANNOTE_MODEL_DIR": "models/pyannote",
    "FLUENTFLOW_MODEL_TABLE_CACHE": "recommended_models.json",
    "FLUENTFLOW_NOTE_SKILL_DIR": "note_skills/mine",
    "FLUENTFLOW_LINK_OUTPUT_DIR": "links",
}
# Read only, through the copy's ``models`` link to the real models.
READ_ONLY_PATHS = frozenset({"FLUENTFLOW_DIARIZATION_MODEL_DIR", "FLUENTFLOW_PYANNOTE_MODEL_DIR"})
# Variables of the same shape that name no place the copy writes.
NOT_WRITTEN = {
    "FLUENTFLOW_DATA_DIR": "the copy itself, which every place above is checked against",
    "FLUENTFLOW_TEST_FRONTEND_DIR": "a page build the copy only reads",
}

_lock = threading.Lock()
_copied: frozenset[str] | None = None


def enabled() -> bool:
    return (os.environ.get(ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def frontend_dir() -> Path | None:
    """The build the page is served from on a test copy, when one is named;
    never on the owner's own service."""
    named = (os.environ.get(FRONTEND_ENV) or "").strip()
    return Path(named).expanduser() if named and enabled() else None


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def export_dir(asked: str | None) -> Path:
    """Where a PDF export on a test copy goes: the folder asked for, else the
    copy's own ``exports`` folder, either only when it resolves inside the
    copy (``exports`` may be a link out of it). The default
    (``~/Documents/FluentFlow 笔记``) is the owner's real folder."""
    root = app_data_root().expanduser().resolve()
    folder = Path(asked).expanduser().resolve() if asked else (root / PDF_DIRNAME).resolve()
    if not _inside(folder, root):
        raise ValueError(OUTSIDE_COPY)
    return folder


def written_children() -> tuple[str, ...]:
    """What the copy writes, relative to it: its writable runtime paths, the
    list of copied tasks and the PDF folder."""
    return tuple(relative for name, relative in PINNED_PATHS.items() if name not in READ_ONLY_PATHS) + (
        TASKS_FILE, PDF_DIRNAME)


def paths_outside() -> list[str]:
    """The places a running copy writes that resolve outside it: a variable
    naming another place, or a link in the copy leading out of it. Each is
    where the service would write: its variable, else its place in the
    copy (the link tasks' folder: the owner's video folder)."""
    from backend.core.output_folder import LINK_ROOT_ENV, link_root

    root = app_data_root().expanduser()
    base = root.resolve()
    places: dict[str, Path] = {}
    for name, relative in PINNED_PATHS.items():
        if name in READ_ONLY_PATHS:
            continue
        named = (os.environ.get(name) or "").strip()
        places[name] = Path(named).expanduser() if named else link_root() if name == LINK_ROOT_ENV else root / relative
    places[PDF_DIRNAME] = root / PDF_DIRNAME
    resolved = {name: path.resolve() for name, path in places.items()}
    return [f"{name} → {path}" for name, path in resolved.items() if not _inside(path, base)]


# The page reads its request address from window.FLUENTFLOW_CONFIG.apiBase
# before anything else (frontend/src/lib/apiBase.js); without one it guesses
# from its port, and on 4173, 5173 or 5186 guesses 8000. A build made with
# FLUENTFLOW_API_BASE set (the MCP server's variable too) writes 8000 there.
# Either way a delete on the copy's page would land on the owner's real tasks
# (decision 8.25), so the copy answers with the build's config and then its
# own origin as the address. The build's config runs inside try, through
# Function, against a stand-in for window: a broken or cut-off file cannot
# stop the line after it, and one that makes window.FLUENTFLOW_CONFIG
# read-only cannot keep 8000 (video and audio sources and links take the
# address too). The copy's pages may only connect to, and play media from,
# the copy (``PAGE_POLICY``), so even a page that never ran this script
# sends nothing elsewhere.
_SEND_BACK_HERE = (
    "\n// FluentFlow test copy: this page's requests go back to the service that served it.\n"
    "window.FLUENTFLOW_CONFIG = Object.assign({}, built.FLUENTFLOW_CONFIG, {apiBase: window.location.origin});\n"
    "})();\n"
)
PAGE_POLICY = "connect-src 'self'; media-src 'self' blob: data:"


def page_config(*, served: bool) -> str:
    """The page's config script on a test copy. ``served``: the static files
    handed out the build's own config, so it is a file inside the build
    (not, say, a link out of it) and may be read here."""
    from backend.core.frontend_paths import local_frontend_dist_dir

    built = ""
    if served:
        try:
            built = (local_frontend_dist_dir() / "assets" / "config.js").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            built = ""
    return ("(function () {\nvar built = {};\n"
            f"try {{ new Function('window', {json.dumps(built)})(built); }} catch (_) {{ /* the build's config is broken */ }}"
            + _SEND_BACK_HERE)


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
_EXPORT_PATH = re.compile(r"/tasks/[^/]+/exports$")
# A field of the query, a body or a form names a task when its name says task
# or job (``task_id``, ``job_ids``, ``source_task_id``); a write route taking a
# task under another name fails tests/test_test_copy_writes.py.
TASK_FIELD = re.compile("task|job", re.IGNORECASE)
# A form larger than this is an upload: read here it would sit in memory
# whole. Every route taking one claims a fresh task, and the lookup of an
# existing one refuses a copied task (``require_owned_task_id``). A body of no
# stated length is read up to this and refused beyond it.
_FORM_READ_LIMIT = 16 * 1024 * 1024
_UNREAD = object()


def _named_tasks(fields: Any) -> list[str]:
    """The task ids in these (name, value) pairs, stripped as the routes
    strip them."""
    found = []
    for name, value in fields:
        if isinstance(name, str) and TASK_FIELD.search(name):
            found += value if isinstance(value, list) else [value]
    ids = (str(task_id).strip() for task_id in found
           if isinstance(task_id, (str, int)) and not isinstance(task_id, bool))
    return [task_id for task_id in ids if task_id]


def _exports_to_feishu(path: str, body: Any) -> bool:
    """The agent's export (``POST /agent/v1/tasks/{id}/exports``) goes to
    Feishu unless its body asks for a PDF."""
    target = body.get("target") if isinstance(body, dict) else None
    return bool(_EXPORT_PATH.search(path)) and str(target or "lark").strip().lower() != "pdf"


def refusal(method: str, path: str, body: Any = None, query: Any = ()) -> str | None:
    """Why this request is refused on a test copy, or None when it may go
    on: anything that changes a copied task (named in the address, the
    query's (name, value) pairs or the body), and any export to Feishu.
    Reading is always allowed."""
    if not enabled() or method.upper() in _SAFE_METHODS:
        return None
    if any(path.endswith(feishu) for feishu in _FEISHU_PATHS) or _exports_to_feishu(path, body):
        return NO_FEISHU
    fields = list(body.items()) if isinstance(body, dict) else []
    named = _TASK_IN_PATH.findall(path) + _named_tasks(fields) + _named_tasks(query)
    if any(is_copied(task_id) for task_id in named):
        return READ_ONLY
    return None


async def _read_bounded(request: Any) -> bool:
    """Read a body of no stated length, up to the limit; whether it was all
    read. Kept where Starlette keeps a body read whole (``Request.body``), so
    the route reads the same bytes."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _FORM_READ_LIMIT:
            return False
        chunks.append(chunk)
    request._body = b"".join(chunks)
    return True


async def _body(request: Any) -> Any:
    """The request's JSON body, or the task fields of its form; None when
    there is neither, or the form is an upload too large to read here;
    ``_UNREAD`` for a body of no stated length too large to read. FastAPI
    reads a body without a Content-Type as JSON, and so does this. What is
    read is kept on the request, so the route reads the same body."""
    kind = (request.headers.get("content-type") or "").lower()
    if "json" in kind:
        try:
            return json.loads(await request.body() or b"null")
        except ValueError:
            return None
    form = kind.startswith(("multipart/form-data", "application/x-www-form-urlencoded"))
    if kind and not form:
        return None
    length = (request.headers.get("content-length") or "").strip()
    if length.isdigit():
        if int(length) > _FORM_READ_LIMIT:
            return None if form else _UNREAD
        await request.body()
    elif not await _read_bounded(request):
        return _UNREAD
    if not form:
        try:
            return json.loads(await request.body() or b"null")
        except ValueError:
            return None
    try:
        async with request.form(max_part_size=_FORM_READ_LIMIT) as fields:
            return {name: [v for v in fields.getlist(name) if isinstance(v, str)]
                    for name in fields.keys() if TASK_FIELD.search(name)}
    except Exception:  # noqa: BLE001 - a form the route cannot read either
        return None



async def middleware(request: Any, call_next: Any) -> Any:
    """Refuse what ``refusal`` names, for the page and agents alike; answer
    the page's config with ``page_config`` and let the pages connect only
    here (``PAGE_POLICY``)."""
    if enabled() and request.method.upper() not in _SAFE_METHODS:
        outside = paths_outside()
        if outside:
            logger.warning("Test copy: write refused, places outside the copy: %s", "; ".join(outside))
            reason = OUTSIDE_PATHS.format("；".join(outside))
        else:
            body = await _body(request)
            reason = UNREAD_BODY if body is _UNREAD else refusal(
                request.method, request.url.path, body, request.query_params.multi_items())
        if reason:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=409, content={"detail": reason, "code": "test_copy"})
    response = await call_next(request)
    if not enabled():
        return response
    # After the service's own checks (host, origin, peer): what they refuse
    # stays refused. 404 is a build without a config of its own.
    if (request.method.upper() in {"GET", "HEAD"} and request.url.path == PAGE_CONFIG_PATH
            and response.status_code in {200, 304, 404}):
        from fastapi.responses import Response

        return Response(page_config(served=response.status_code != 404), media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})
    if (response.headers.get("content-type") or "").startswith("text/html"):
        response.headers["Content-Security-Policy"] = PAGE_POLICY
    return response


__all__ = ["ENV", "FRONTEND_ENV", "NOT_WRITTEN", "NO_FEISHU", "OUTSIDE_COPY", "OUTSIDE_PATHS", "PAGE_CONFIG_PATH",
           "PAGE_POLICY", "PDF_DIRNAME", "PINNED_PATHS", "READ_ONLY", "READ_ONLY_PATHS", "TASKS_FILE", "TASK_FIELD",
           "UNREAD_BODY", "copied_tasks", "enabled", "export_dir", "frontend_dir", "is_copied", "middleware",
           "page_config", "paths_outside", "refusal", "reset", "writable", "written_children"]
