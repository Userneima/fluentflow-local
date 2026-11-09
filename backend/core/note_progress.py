"""How far FluentFlow's own note writer has got, while it writes.

A note written by a text model or by Claude takes minutes, and the page used to
say only 「正在写笔记」 the whole time (user feedback, 2026-11-09). Each writer
reports here what it actually knows: which stage it is on, how many parts or
chapters there are and which are done, the stretch of the recording a part
covers, and the text written so far. The task list and the editor read it
through ``task_activity`` (``snapshot``) and the task's detail (``draft``).

Kept in memory, per task, for the process that writes the note: a restart
writes the note again from its start, so nothing here needs to survive one.
Nothing here decides anything; a writer that reports nothing leaves the page
with its elapsed time.
"""

from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

# Stages, in the order a writer can go through them. The page words them.
FRAMES = "frames"          # taking the pictures out of the recording (Claude)
READING = "reading"        # pulling the points out of each stretch of transcript
PLANNING = "planning"      # choosing the chapters
WRITING = "writing"        # writing the note, a part or the chapters
UNIFYING = "unifying"      # one pass over the joined chapters for one voice
CHECKING = "checking"      # checking the note against the transcript
REVISING = "revising"      # writing back what the check found missing
FINISHING = "finishing"    # written; being saved

# How long a finished writer's record is kept for a page that polls once more.
_KEEP_SECONDS = 15 * 60


@dataclass
class _Record:
    writer: str
    started_at: float
    stage: str = WRITING
    updated_at: float = 0.0
    # Parts a long recording is written in (Claude), or the stretches the text
    # model reads it in: 1-based ``index`` of the one being written.
    parts_total: int = 0
    parts_done: int = 0
    part_index: int = 0
    part_starts_at: str = ""
    part_ends_at: str = ""
    # Seconds the finished parts took, so the page can say about how long the
    # rest takes from this run's own pace rather than a guess.
    parts_seconds: float = 0.0
    part_started_at: float = 0.0
    # Stretches read for points (the text model's first stage).
    read_total: int = 0
    read_done: int = 0
    # Chapters, in order: title, and whether written.
    chapters: list[dict[str, Any]] = field(default_factory=list)
    # The text written so far, by key in order: one key for a single draft,
    # one per chapter or part. Shown as it is; the final note replaces it.
    texts: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    finished_at: float | None = None


_LOCK = threading.Lock()
_RECORDS: dict[str, _Record] = {}


def _now() -> float:
    return time.time()


def start(task_id: str | None, writer: str) -> None:
    """A writer starts on ``task_id``'s note; whatever an earlier run left goes."""
    if not task_id:
        return
    now = _now()
    with _LOCK:
        _RECORDS[str(task_id)] = _Record(writer=writer, started_at=now, updated_at=now)


def finish(task_id: str | None) -> None:
    """The writer is done (or gave up). The record stays a little while, read as
    finishing, so the page does not fall back to a bare line while it saves."""
    if not task_id:
        return
    with _LOCK:
        record = _RECORDS.get(str(task_id))
        if record is not None and record.finished_at is None:
            record.stage = FINISHING
            record.finished_at = record.updated_at = _now()
        _prune()


def _prune() -> None:
    cutoff = _now() - _KEEP_SECONDS
    for key in [key for key, record in _RECORDS.items() if record.finished_at and record.finished_at < cutoff]:
        _RECORDS.pop(key, None)


@contextmanager
def tracking(task_id: str | None, writer: str) -> Iterator[None]:
    start(task_id, writer)
    try:
        yield
    finally:
        finish(task_id)


def _update(task_id: str | None, change) -> None:
    if not task_id:
        return
    with _LOCK:
        record = _RECORDS.get(str(task_id))
        if record is None or record.finished_at is not None:
            return
        change(record)
        record.updated_at = _now()


def stage(task_id: str | None, name: str) -> None:
    def change(record: _Record) -> None:
        record.stage = name
    _update(task_id, change)


def reading(task_id: str | None, *, total: int | None = None, done_one: bool = False) -> None:
    def change(record: _Record) -> None:
        record.stage = READING
        if total is not None:
            record.read_total, record.read_done = int(total), 0
        if done_one:
            record.read_done = min(record.read_total or record.read_done + 1, record.read_done + 1)
    _update(task_id, change)


def chapters(task_id: str | None, titles: list[str]) -> None:
    """The chapters are chosen; writing them starts."""
    def change(record: _Record) -> None:
        record.chapters = [{"title": str(title or "").strip(), "done": False} for title in titles]
        record.order = [f"chapter:{index}" for index in range(len(titles))]
        record.texts = {}
        record.stage = WRITING
    _update(task_id, change)


def chapter_done(task_id: str | None, index: int) -> None:
    def change(record: _Record) -> None:
        if 0 <= index < len(record.chapters):
            record.chapters[index]["done"] = True
    _update(task_id, change)


def part(task_id: str | None, *, index: int, total: int, starts_at: str = "", ends_at: str = "") -> None:
    """Part ``index`` of ``total`` (1-based) starts being written."""
    def change(record: _Record) -> None:
        now = _now()
        record.stage = WRITING
        record.parts_total = int(total)
        record.part_index = int(index)
        record.part_starts_at = str(starts_at or "")
        record.part_ends_at = str(ends_at or "")
        record.part_started_at = now
    _update(task_id, change)


def part_done(task_id: str | None, *, index: int, text: str = "") -> None:
    def change(record: _Record) -> None:
        key = f"part:{index}"
        if key not in record.order:
            record.order.append(key)
        if text:
            record.texts[key] = text
        record.parts_done = max(record.parts_done, int(index))
        if record.part_started_at:
            record.parts_seconds += max(0.0, _now() - record.part_started_at)
            record.part_started_at = 0.0
    _update(task_id, change)


def text(task_id: str | None, key: str, value: str) -> None:
    """The text written so far under ``key`` (a streamed answer grows here)."""
    def change(record: _Record) -> None:
        if key not in record.order:
            record.order.append(key)
        record.texts[key] = value
    _update(task_id, change)


_SINK: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "fluentflow_note_progress_sink", default=None,
)


@contextmanager
def showing(task_id: str | None, key: str) -> Iterator[None]:
    """Text model calls in the block stream their answer here, under ``key``
    (``ai_client._chat`` reads ``sink``). Outside a task nothing streams."""
    if not task_id:
        yield
        return
    token = _SINK.set(lambda value: text(task_id, key, value))
    try:
        yield
    finally:
        _SINK.reset(token)


def sink() -> Callable[[str], None] | None:
    """Where the text model call being made streams its answer, if anywhere."""
    return _SINK.get()


def draft(task_id: str | None) -> str:
    """Everything written so far, in order."""
    if not task_id:
        return ""
    with _LOCK:
        record = _RECORDS.get(str(task_id))
        if record is None:
            return ""
        pieces = [record.texts.get(key, "").strip() for key in record.order]
    return "\n\n".join(piece for piece in pieces if piece)


def snapshot(task_id: str | None) -> dict[str, Any] | None:
    """What the page shows about the writing, without the text itself."""
    if not task_id:
        return None
    with _LOCK:
        record = _RECORDS.get(str(task_id))
        if record is None:
            return None
        now = _now()
        written = sum(len(value) for value in record.texts.values())
        return {
            "writer": record.writer,
            "stage": record.stage,
            "elapsed_seconds": round(max(0.0, (record.finished_at or now) - record.started_at)),
            "read_total": record.read_total,
            "read_done": record.read_done,
            "parts_total": record.parts_total,
            "parts_done": record.parts_done,
            "part_index": record.part_index,
            "part_starts_at": record.part_starts_at,
            "part_ends_at": record.part_ends_at,
            # Mean seconds per finished part, when a part has finished.
            "seconds_per_part": round(record.parts_seconds / record.parts_done) if record.parts_done else None,
            "part_elapsed_seconds": round(now - record.part_started_at) if record.part_started_at else None,
            "chapters": [dict(chapter) for chapter in record.chapters],
            "written_chars": written,
            "updated_at": record.updated_at,
        }


def live_task_ids() -> list[str]:
    """Tasks a writer is on now."""
    with _LOCK:
        return [key for key, record in _RECORDS.items() if record.finished_at is None]


def reset() -> None:
    """Tests only."""
    with _LOCK:
        _RECORDS.clear()


__all__ = [
    "CHECKING", "FINISHING", "FRAMES", "PLANNING", "READING", "REVISING", "UNIFYING", "WRITING",
    "chapter_done", "chapters", "draft", "finish", "live_task_ids", "part", "part_done",
    "reading", "reset", "showing", "sink", "snapshot", "stage", "start", "text", "tracking",
]
