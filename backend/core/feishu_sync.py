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
- When both sides changed, each chapter changed on one side only takes that
  side's version; a chapter changed on both goes to the later change, and the
  other version of the whole note is kept beside the md. A push first brings
  in edits made in Feishu since the last one. Before either side is replaced, the version replaced is
  kept among the note's versions (``note_versions``); the Feishu side only
  when it was edited in Feishu since the last push.
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
    return note_versions.md_for(task_id, result)


def _as_pushed(result: dict[str, Any] | None, text: str) -> bool:
    """Whether the Feishu document still reads as it did after the last push,
    chapter by chapter in the tidied form recorded then: Feishu rewrites some
    of what is pushed (escapes, callouts, pictures), so the words are not
    compared as they stand. An edit made in Feishu and pulled in since still
    counts as an edit: the push about to run writes over it."""
    response = result.get("lark_response") if isinstance(result, dict) else None
    sync = response.get("sync") if isinstance(response, dict) else None
    if not isinstance(sync, dict):
        return False
    # Synced before 2026-10-23: only the chapters as last seen were recorded.
    recorded = sync.get("pushed_sections") if "pushed_sections" in sync else sync.get("feishu_sections")
    if not isinstance(recorded, list):
        return False
    now = feishu_note.feishu_section_records(text)
    return [(r.get("key"), r.get("digest")) for r in recorded] == [(r["key"], r["digest"]) for r in now]


def keeper(task_id: str, result: dict[str, Any] | None, by: str | None = None) -> Callable[[str], None]:
    """What saves the Feishu document's markdown before an update replaces it.
    ``by`` is what replaces it; by default, whatever wrote the note pushed.

    A document nobody edited in Feishu since the last push is not saved when
    the note has an output folder: it is the note as it was pushed, which the
    folder keeps as the md's version when the md is next rewritten, and a
    second copy in Feishu's format is one more near-duplicate (owner,
    2026-10-23). A note with no folder has no md whose versions are kept, so
    its Feishu copy is still saved."""
    md = _note_md(task_id, result if isinstance(result, dict) else {})
    by = by or note_versions.changed_by(result)
    folder = (result or {}).get("output_folder") if isinstance(result, dict) else None
    has_folder = isinstance(folder, dict) and bool(folder.get("md_path"))

    def keep(text: str) -> None:
        if has_folder and _as_pushed(result, text):
            return
        note_versions.keep(md, text, note_versions.FEISHU, by)

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


def push(task_id: str, *, previous: dict[str, Any] | None = None, runner: Any = subprocess.run) -> Any:
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
    if previous is None:
        # Edits made in Feishu since the last push come in first; writing over
        # the document now would record them as pushed and never bring them back.
        cli, bin_path = _cli()
        modified = cli.modified_times(runner, bin_path, [str(sync["doc_id"])]).get(str(sync["doc_id"]))
        if modified is not None and modified > int(sync.get("feishu_modified_at") or 0):
            return pull(task_id, modified_at=modified, runner=runner)
    title = str(result.get("lark_doc_title") or "") or resolve_lark_doc_title(note, filename_stem="", form_title=None)
    try:
        response = export_markdown_via_lark_cli(
            title, note, task_id=task_id, artifact_root=_artifact_storage_dir(),
            previous=sync, keep_feishu=keeper(task_id, result), runner=runner,
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
    note_versions.declare(after, note_versions.FEISHU_PULL)
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

    # The note as Feishu now has it: the pushed note with the chapters edited
    # in Feishu laid over it. A chapter is known by its heading words and
    # which occurrence of them it is.
    old_ids = _occurrences([r.get("key") for r in old])
    current_ids = _occurrences([r.get("key") for r in current])
    feishu_side: list[tuple[tuple[str, int], str]] = []
    feishu_changed: set[tuple[str, int]] = set()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                same = old[i]["digest"] == current[j]["digest"]
                feishu_side.append((current_ids[j], base[i].text if same and lined_up else back(feishu_parts[j])))
                if not same:
                    feishu_changed.add(current_ids[j])
        else:
            feishu_changed.update(old_ids[i] for i in range(i1, i2))
            for j in range(j1, j2):
                feishu_side.append((current_ids[j], back(feishu_parts[j])))
                feishu_changed.add(current_ids[j])
    from_feishu = _joined(text for _, text in feishu_side)

    # The note's own changes since the push, chapter by chapter.
    note = str(result.get("summary_markdown") or "")
    note_changed = cli.note_digest(note) != sync.get("note_digest")
    local_parts = feishu_note.sections(note)
    local_ids = _occurrences([part.key for part in local_parts])
    local_text = dict(zip(local_ids, (part.text for part in local_parts)))
    base_text = dict(zip(_occurrences([part.key for part in base]), (part.text for part in base))) if lined_up else {}
    if not note_changed:
        local_changed: set[tuple[str, int]] = set()
    elif lined_up:
        local_changed = {cid for cid in local_ids if base_text.get(cid) != local_text[cid]}
        local_changed |= {cid for cid in base_text if cid not in local_text}
    else:
        local_changed = set(local_ids)
    feishu_later = float(modified_at or 0) > _local_changed_at(task_id, result)
    conflicts = local_changed & feishu_changed

    # Each side's chapters where only it changed them; a chapter changed on
    # both sides goes to the later change.
    merged: list[tuple[tuple[str, int], str]] = []
    from_note: set[tuple[str, int]] = set()
    for cid, text in feishu_side:
        if cid in local_changed and not (cid in conflicts and feishu_later):
            if cid in local_text:
                merged.append((cid, local_text[cid]))
                from_note.add(cid)
            continue
        merged.append((cid, text))
    placed = {cid for cid, _ in merged}
    for position, cid in enumerate(local_ids):
        if cid in placed:
            continue
        if cid in base_text and (cid not in local_changed or feishu_later):
            continue  # removed in Feishu, and the note did not change it later
        before = next((local_ids[k] for k in range(position - 1, -1, -1) if local_ids[k] in placed), None)
        at = 0 if before is None else [c for c, _ in merged].index(before) + 1
        merged.insert(at, (cid, local_text[cid]))
        placed.add(cid)
        from_note.add(cid)
    merged_note = _joined(text for _, text in merged)

    if conflicts:
        if feishu_later:
            _keep_beside(task_id, result, note, "本地修改")
        else:
            _keep_beside(task_id, result, from_feishu, "飞书修改")

    outline = cli._run(runner, [bin_path, "docs", "+fetch", "--doc", doc_id, "--scope", "outline",
                                "--detail", "with-ids", "--as", "user"], timeout=60)
    outline_doc = outline.get("document") if isinstance(outline.get("document"), dict) else outline
    heading_ids = feishu_note.heading_list(str(outline_doc.get("content") or ""))
    merged_digests = dict(zip(
        _occurrences([part.key for part in feishu_note.sections(merged_note)]),
        (part.digest for part in feishu_note.sections(merged_note)),
    ))
    # What the document holds, chapter by chapter, as the next push sees it: a
    # chapter that now has the note's text, or that the note no longer has, is
    # out of date and gets rewritten or removed.
    feishu_records = feishu_note.section_records(feishu_parts, heading_ids)
    for cid, record in zip(current_ids, feishu_records):
        record["digest"] = None if cid in from_note else merged_digests.get(cid)

    pulled = merged_note != note
    if pulled:
        note_versions.keep(_note_md(task_id, result), note, note_versions.TASK, note_versions.FEISHU_PULL)
    settled = {
        **sync,
        "feishu_sections": current,
        "note_digest": cli.note_digest(merged_note),
        "sections": feishu_records,
        "headings": feishu_note.headings(str(outline_doc.get("content") or "")),
    }
    if modified_at is not None:
        settled["feishu_modified_at"] = modified_at
    cli.save_pushed_note(_artifact_storage_dir(), task_id, from_feishu)
    after = _with_note(task_id, result, merged_note) if pulled else dict(result)
    after["lark_response"] = {**response, "sync": settled}
    after["lark_sync_error"] = None
    if pulled:
        after["lark_pulled_at"] = datetime.now(timezone.utc).isoformat()
    job_store.save_result_changes(task_id, result, after)
    if from_note or any(record["digest"] is None for record in feishu_records):
        push(task_id, previous=settled, runner=runner)
    return "pulled" if pulled else "kept_note"


def _occurrences(keys: list[Any]) -> list[tuple[str, int]]:
    """Each heading's words with which occurrence of them it is, so two
    chapters with the same heading stay apart."""
    seen: dict[str, int] = {}
    found: list[tuple[str, int]] = []
    for key in keys:
        count = seen.get(str(key), 0)
        seen[str(key)] = count + 1
        found.append((str(key), count))
    return found


def _joined(texts: Any) -> str:
    return "\n\n".join(text.strip("\n") for text in texts if text.strip()) + "\n"


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
