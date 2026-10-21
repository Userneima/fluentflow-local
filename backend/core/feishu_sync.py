"""A note exported to Feishu stays in step with it, both ways, chapter by chapter.

Requirement (owner, 2026-10-21). A note exported with the user's own identity
(lark-cli) has its document recorded on the task (``lark_response.sync``).
From then on:

- Exporting it again updates that document instead of creating another.
- When the note changes (edited in FluentFlow, or in Obsidian and taken back
  in), the document is updated once the changes have stopped for
  ``QUIET_SECONDS``. Only the chapters that changed are rewritten
  (``lark_cli_exporter._update``).
- Every ``CHECK_SECONDS`` Feishu is asked, in one request for all such notes,
  when each document last changed. A document changed after the last push,
  and left alone for ``QUIET_SECONDS``, is read back: only the chapters that
  no longer look as they did after the push are written back into the note,
  in the note's own way (``feishu_note.from_feishu``); the other chapters keep
  the note's text as it was pushed.
- When both sides changed, the later change is the note; the other version is
  kept beside the md. Before either side is replaced, the version replaced is
  kept among the note's versions (``note_versions``).
- A document deleted in Feishu, or no longer open to the user, stops the
  syncing of that note with the reason on the task; nothing is recreated.

Notes exported before this, or through a Feishu app (OpenAPI), have no record
and are left alone.
"""

from __future__ import annotations

import difflib
import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core import feishu_note, job_store, note_versions

logger = logging.getLogger(__name__)

QUIET_SECONDS = 60.0
PUSH_QUIET_SECONDS = QUIET_SECONDS
CHECK_SECONDS = 120.0

# What lark-cli says when the document is gone or closed to the user.
_GONE_SIGNS = ("has been deleted", "doc not found", "3380003", "permission denied", "no permission", "forbidden")


def recorded_sync(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Feishu document a note is kept in step with, or None."""
    if not isinstance(result, dict):
        return None
    response = result.get("lark_response")
    if not isinstance(response, dict) or response.get("via") != "lark_cli":
        return None
    sync = response.get("sync")
    if not isinstance(sync, dict) or not sync.get("doc_id") or not isinstance(sync.get("sections"), list):
        return None
    if sync.get("stopped"):
        return None
    return sync


def _note_md(task_id: str, result: dict[str, Any]) -> Path:
    """Where the note's versions are kept: beside its md in the output folder,
    or, for a note with no folder, in the task's own storage."""
    from backend.core.output_folder import RESULT_KEY
    from backend.core.storage_paths import _artifact_storage_dir

    folder = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
    if folder.get("md_path"):
        return Path(str(folder["md_path"]))
    return _artifact_storage_dir() / task_id / "笔记.md"


def keeper(task_id: str, result: dict[str, Any] | None) -> Callable[[str], None]:
    """What saves the Feishu document's markdown before an update replaces it."""
    md = _note_md(task_id, result if isinstance(result, dict) else {})

    def keep(text: str) -> None:
        note_versions.keep(md, text, note_versions.FEISHU)

    return keep


def _keep_beside(task_id: str, result: dict[str, Any], text: str, label: str) -> None:
    """The version that lost, as a copy beside the md (as an Obsidian edit
    that lost is kept)."""
    md = _note_md(task_id, result)
    try:
        md.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        md.with_name(f"{md.stem}_{label}-{stamp}.md").write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("could not keep the %s copy of %s: %s", label, task_id, exc)


def _gone(error: Exception) -> bool:
    text = str(error).lower()
    return any(sign in text for sign in _GONE_SIGNS)


def _stop(task_id: str, error: Exception) -> None:
    job = job_store.get_job(task_id)
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else None
    if not result or not isinstance(result.get("lark_response"), dict):
        return
    reason = "飞书那份已被删除或打不开了，这篇笔记不再和它同步。要重新同步，再导出一次会新建一篇。"
    response = result["lark_response"]
    sync = dict(response.get("sync") or {})
    sync["stopped"] = reason
    # Exporting again creates a new document rather than updating a gone one.
    sync["sections"] = None
    logger.warning("Feishu copy of %s is gone (%s); syncing stopped", task_id, error)
    _record(task_id, lark_response={**response, "sync": sync}, lark_sync_error=reason)


def push(task_id: str, *, previous: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Bring the task's Feishu document up to date with its note. Returns the
    export response, or None when there was nothing to do. Never raises: a
    failure is recorded on the task as ``lark_sync_error``. ``previous``
    replaces the recorded sync for this push (a pull that lost to the note
    asks for the chapters edited in Feishu to be rewritten)."""
    from backend.core.lark_cli_exporter import export_markdown_via_lark_cli, note_digest
    from backend.core.note_title import resolve_lark_doc_title
    from backend.core.storage_paths import _artifact_storage_dir

    job = job_store.get_job(task_id)
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else None
    sync = previous or recorded_sync(result)
    if result is None or sync is None:
        return None
    note = str(result.get("summary_markdown") or "")
    if not note.strip() or (previous is None and note_digest(note) == sync.get("note_digest")):
        return None
    title = str(result.get("lark_doc_title") or "") or resolve_lark_doc_title(note, filename_stem="", form_title=None)
    try:
        response = export_markdown_via_lark_cli(
            title, note, task_id=task_id, artifact_root=_artifact_storage_dir(),
            previous=sync, keep_feishu=keeper(task_id, result),
        )
    except Exception as exc:  # noqa: BLE001 - the note itself is fine
        if _gone(exc):
            _stop(task_id, exc)
            return None
        logger.warning("Feishu copy of %s not updated: %s", task_id, exc)
        _record(task_id, lark_sync_error=f"飞书那份没更新（{exc}）。笔记本身没受影响，下次改动时会再试。")
        return None
    response_before = result.get("lark_response") if isinstance(result.get("lark_response"), dict) else {}
    _record(task_id, lark_response={**response_before, **response}, lark_sync_error=None,
            lark_synced_at=datetime.now(timezone.utc).isoformat())
    return response


def _record(task_id: str, **fields: Any) -> None:
    job = job_store.get_job(task_id)
    if not isinstance(job, dict):
        return
    latest = job.get("result") if isinstance(job.get("result"), dict) else {}
    job_store.save_result_changes(task_id, latest, {**latest, **fields})


# ── edits made in Feishu ────────────────────────────────────────────────────

# When each note last changed here (seconds), for telling which side changed
# later. Kept in memory; after a restart the note's own edit time stands in.
_NOTE_CHANGED_AT: dict[str, float] = {}


def _local_changed_at(task_id: str, result: dict[str, Any]) -> float:
    known = _NOTE_CHANGED_AT.get(task_id, 0.0)
    try:
        edited = datetime.fromisoformat(str(result.get("summary_edited_at"))).timestamp()
    except (TypeError, ValueError):
        edited = 0.0
    return max(known, edited)


def _cli() -> tuple[Any, str]:
    from backend.core import lark_cli_exporter as cli

    bin_path = cli._resolve_lark_cli_bin()
    if not bin_path:
        raise cli.LarkCliError(f"{cli.LARK_CLI_NOT_FOUND}.")
    return cli, bin_path


def _image_fetcher(task_id: str, runner: Any) -> Callable[[str], str | None]:
    """Fetch a picture added in Feishu into the task's screenshots; its src as
    the note refers to screenshots."""
    from backend.core.storage_paths import _artifact_storage_dir

    def fetch(token: str) -> str | None:
        cli, bin_path = _cli()
        frames = _artifact_storage_dir() / task_id / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        try:
            data = cli._run(
                runner,
                [bin_path, "docs", "+media-download", "--token", token, "--output", f"feishu_{token}",
                 "--overwrite", "--as", "user"],
                timeout=120, cwd=frames,
            )
        except cli.LarkCliError as exc:
            logger.warning("picture %s from Feishu not fetched for %s: %s", token, task_id, exc)
            return None
        name = Path(str(data.get("saved_path") or f"feishu_{token}")).name
        return f"/jobs/{task_id}/artifacts/frame?file={name}"

    return fetch


def _with_note(task_id: str, result: dict[str, Any], note: str) -> dict[str, Any]:
    from backend.core.result_artifacts import _attach_result_artifacts

    after = dict(result)
    if not after.get("summary_edited") and not after.get("summary_original_markdown"):
        after["summary_original_markdown"] = str(result.get("summary_markdown") or "")
    after.update({
        "summary_markdown": note,
        "summary_skipped": False,
        "summary_status": "completed",
        "summary_error": None,
        "summary_edited": True,
        "summary_edited_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "summary_edited_in": "feishu",
    })
    return _attach_result_artifacts(task_id, after, parts=("summary",))


def pull(task_id: str, *, modified_at: int | None = None, runner: Any = subprocess.run) -> str | None:
    """Bring edits made in the Feishu document back into the note. Returns
    what happened (``nothing``, ``pulled``, ``kept_note``), or None when the
    note is not kept in Feishu. Never raises."""
    try:
        return _pull(task_id, modified_at=modified_at, runner=runner)
    except Exception as exc:  # noqa: BLE001 - the note itself is fine
        if _gone(exc):
            _stop(task_id, exc)
            return None
        logger.warning("Feishu edits of %s not brought back: %s", task_id, exc)
        _record(task_id, lark_sync_error=f"飞书里的修改没收回来（{exc}）。笔记本身没受影响，过一会会再试。")
        return None


def _pull(task_id: str, *, modified_at: int | None, runner: Any) -> str | None:
    from backend.core.storage_paths import _artifact_storage_dir

    job = job_store.get_job(task_id)
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else None
    sync = recorded_sync(result)
    if result is None or sync is None:
        return None
    cli, bin_path = _cli()
    doc_id = str(sync["doc_id"])
    shown = cli._run(runner, [bin_path, "docs", "+fetch", "--doc", doc_id, "--doc-format", "markdown", "--as", "user"],
                     timeout=120)
    document = shown.get("document") if isinstance(shown.get("document"), dict) else shown
    content = str(document.get("content") or "")
    if modified_at is None:
        modified_at = cli.modified_times(runner, bin_path, [doc_id]).get(doc_id)
    current = feishu_note.feishu_section_records(content)
    old = sync.get("feishu_sections")
    response = result["lark_response"]

    def settle(**changes: Any) -> None:
        settled = {**sync, "feishu_sections": current, **changes}
        if modified_at is not None:
            settled["feishu_modified_at"] = modified_at
        _record(task_id, lark_response={**response, "sync": settled})

    if not isinstance(old, list):
        # Nothing to compare with (pushed before chapters were recorded):
        # from now on there is.
        settle()
        return "nothing"
    matcher = difflib.SequenceMatcher(a=[r.get("key") for r in old], b=[r.get("key") for r in current], autojunk=False)
    opcodes = matcher.get_opcodes()
    edited = [
        (tag, i1, i2, j1, j2) for tag, i1, i2, j1, j2 in opcodes
        if tag != "equal" or any(old[i]["digest"] != current[j]["digest"] for i, j in zip(range(i1, i2), range(j1, j2)))
    ]
    if not edited:
        settle()
        return "nothing"

    pushed_path = cli.pushed_note_path(_artifact_storage_dir(), task_id)
    pushed = pushed_path.read_text(encoding="utf-8") if pushed_path and pushed_path.is_file() else ""
    base = feishu_note.sections(pushed)
    feishu_parts = feishu_note.sections(feishu_note.without_title(content))
    records = sync.get("sections") or []
    lined_up = [p.key for p in base] == [r.get("key") for r in old] == [r.get("key") for r in records]
    headings_by_id = {
        str(record.get("heading_id")): part.heading
        for part, record in zip(base, records) if lined_up and record.get("heading_id")
    }
    way = feishu_note.Way(doc_id=doc_id, headings_by_id=headings_by_id, images=list(sync.get("images") or []),
                          fetch_image=_image_fetcher(task_id, runner))

    def back(part: feishu_note.Section) -> str:
        return feishu_note.from_feishu(part.text, way)

    merged: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                same = old[i]["digest"] == current[j]["digest"]
                merged.append(base[i].text if same and lined_up else back(feishu_parts[j]))
        else:
            merged.extend(back(feishu_parts[j]) for j in range(j1, j2))
    from_feishu = "\n\n".join(text.strip("\n") for text in merged if text.strip()) + "\n"

    outline = cli._run(runner, [bin_path, "docs", "+fetch", "--doc", doc_id, "--scope", "outline",
                                "--detail", "with-ids", "--as", "user"], timeout=60)
    outline_doc = outline.get("document") if isinstance(outline.get("document"), dict) else outline
    heading_ids = feishu_note.heading_list(str(outline_doc.get("content") or ""))
    note = str(result.get("summary_markdown") or "")
    note_changed = cli.note_digest(note) != sync.get("note_digest")

    if note_changed and _local_changed_at(task_id, result) >= float(modified_at or 0):
        # Both changed and the note's change is the later one: the note stays,
        # the Feishu version is kept beside the md, and the push rewrites the
        # chapters edited in Feishu as well as the note's own.
        _keep_beside(task_id, result, from_feishu, "飞书修改")
        feishu_records = feishu_note.section_records(feishu_parts, heading_ids)
        for tag, i1, i2, j1, j2 in opcodes:
            for j in range(j1, j2):
                i = i1 + (j - j1) if tag == "equal" else None
                unchanged = i is not None and old[i]["digest"] == current[j]["digest"] and lined_up
                feishu_records[j]["digest"] = records[i]["digest"] if unchanged else None
        settle()
        forced = {**recorded_sync(job_store.get_job(task_id)["result"]), "sections": feishu_records}
        push(task_id, previous=forced)
        return "kept_note"

    # Feishu's change is the note now. What it replaces is kept first.
    md = _note_md(task_id, result)
    note_versions.keep(md, note, note_versions.TASK)
    if note_changed:
        _keep_beside(task_id, result, note, "本地修改")
    after = _with_note(task_id, result, from_feishu)
    cli.save_pushed_note(_artifact_storage_dir(), task_id, from_feishu)
    settled = {
        **sync,
        "feishu_sections": current,
        "note_digest": cli.note_digest(from_feishu),
        "sections": feishu_note.section_records(feishu_note.sections(from_feishu), heading_ids),
        "headings": feishu_note.headings(str(outline_doc.get("content") or "")),
    }
    if modified_at is not None:
        settled["feishu_modified_at"] = modified_at
    after["lark_response"] = {**response, "sync": settled}
    after["lark_sync_error"] = None
    after["lark_pulled_at"] = datetime.now(timezone.utc).isoformat()
    job_store.save_result_changes(task_id, result, after)
    return "pulled"


def check_feishu(now: float | None = None, runner: Any = subprocess.run) -> list[str]:
    """Ask Feishu, in one request, when each kept document last changed; queue
    a pull for each changed since the last push and left alone for
    ``QUIET_SECONDS``. Returns the tasks queued."""
    rows = [row for row in job_store.list_feishu_synced() if not row[3]]
    if not rows:
        return []
    cli, bin_path = _cli()
    times = cli.modified_times(runner, bin_path, sorted({doc_id for _, doc_id, _, _ in rows}))
    clock = time.time() if now is None else now
    queued: list[str] = []
    for task_id, doc_id, recorded, _stopped in rows:
        modified = times.get(doc_id)
        if modified is None:
            _WORKER.submit(_check_gone, task_id, doc_id, runner)
            continue
        if modified > recorded and clock - modified >= QUIET_SECONDS:
            _WORKER.submit(pull, task_id, modified_at=modified, runner=runner)
            queued.append(task_id)
    return queued


def _check_gone(task_id: str, doc_id: str, runner: Any) -> None:
    """A document Feishu did not report: stop syncing it if it is really gone."""
    try:
        cli, bin_path = _cli()
        cli._run(runner, [bin_path, "docs", "+fetch", "--doc", doc_id, "--scope", "outline", "--as", "user"], timeout=60)
    except Exception as exc:  # noqa: BLE001
        if _gone(exc):
            _stop(task_id, exc)


_watch_started = False


def start_watch() -> None:
    """Every ``CHECK_SECONDS``, bring back edits made in Feishu. Started once,
    at service start; the first look comes right away, so edits made while
    FluentFlow was closed come back."""
    global _watch_started
    if _watch_started:
        return
    _watch_started = True

    def loop() -> None:
        while True:
            try:
                check_feishu()
            except Exception as exc:  # noqa: BLE001 - the next look tries again
                logger.warning("looking for Feishu edits failed: %s", exc)
            time.sleep(CHECK_SECONDS)

    threading.Thread(target=loop, name="feishu-edits", daemon=True).start()


# ── when the note changes ───────────────────────────────────────────────────

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feishu-sync")
_TIMERS: dict[str, threading.Timer] = {}
_LOCK = threading.Lock()


def schedule_push_when_quiet(task_id: str, delay: float | None = None) -> None:
    """Push once the note has not changed for ``QUIET_SECONDS``; each new
    change starts the wait again. Pushes and pulls run one at a time."""
    def fire() -> None:
        with _LOCK:
            if _TIMERS.get(task_id) is timer:
                _TIMERS.pop(task_id, None)
        _WORKER.submit(push, task_id)

    timer = threading.Timer(QUIET_SECONDS if delay is None else delay, fire)
    timer.daemon = True
    with _LOCK:
        previous = _TIMERS.get(task_id)
        if previous is not None:
            previous.cancel()
        _TIMERS[task_id] = timer
    timer.start()


def _on_result_saved(task_id: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    if recorded_sync(after) is None:
        return
    note = str((after or {}).get("summary_markdown") or "")
    if note == str((before or {}).get("summary_markdown") or ""):
        return
    if (after or {}).get("lark_pulled_at") == (before or {}).get("lark_pulled_at"):
        # Changed here, not brought back from Feishu.
        _NOTE_CHANGED_AT[task_id] = time.time()
    schedule_push_when_quiet(task_id)


job_store.add_result_listener(_on_result_saved)


__all__ = [
    "CHECK_SECONDS",
    "PUSH_QUIET_SECONDS",
    "QUIET_SECONDS",
    "check_feishu",
    "keeper",
    "pull",
    "push",
    "recorded_sync",
    "schedule_push_when_quiet",
    "start_watch",
]
