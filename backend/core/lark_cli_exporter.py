"""Export Markdown to Feishu via local ``lark-cli`` (user identity).

This module is the entry point (``export_markdown_via_lark_cli``); its parts
sit beside it: running lark-cli and reading its output ``lark_cli_run``,
screenshots ``lark_cli_images``, what a task keeps about its document
``lark_cli_records``, writing and rewriting chapters ``lark_cli_update``, what
follows the writes (heading links, list numbers, the snapshot)
``lark_cli_finish``. They call ``lark_cli_run._run``,
``lark_cli_run._resolve_lark_cli_bin`` and ``lark_cli_images._insert_images``
at call time, so tests replace those there, never on this module
(``tests/test_lark_cli_export.py`` checks).

Requires ``lark-cli`` on PATH or ``FLUENTFLOW_LARK_CLI_BIN``. The document is
created in the user's own 「我的文档库」 (``--parent-position my_library``),
overridable via ``FLUENTFLOW_LARK_CLI_WIKI_SPACE``.

The command sequence (checked against lark-cli 1.0.70 and its embedded
``lark-doc`` skill):

1. ``docs +create --doc-format markdown --title T --content - --parent-position
   my_library --as user`` with the note on stdin. Every local screenshot in the
   note is first replaced by its own caption paragraph (``图 N：说明``), because
   Feishu can only fetch http(s) images from Markdown.
2. For each local screenshot, run ``docs +media-insert --doc <document_id>
   --file <name> --selection-with-ellipsis "<marker>" --before --as user`` from
   the screenshot's folder (lark-cli only accepts relative file paths), so the
   picture lands right above its caption, then remove the marker with a
   ``str_replace``. The marker (``⟦FFIMG-<push>-<n>⟧`` in front of the
   caption) is unique to that picture in that push: captions are numbered over
   the whole note, so a chapter rewritten later can carry the same caption as
   one left alone, and locating by caption put the picture in the wrong place.
   Before each insert the task's record of the document notes which picture
   is going in, and after it what the picture became, so an export cut off
   here is carried on from the record (``lark_cli_images.resume_pictures``).

A screenshot that fails to upload does not fail the export; the counts come
back as ``image_count`` / ``image_upload_count`` / ``image_upload_errors``.

The note is written as ``feishu_note.to_feishu`` prepares it (callouts as
Feishu callouts, tables kept as tables: lark-cli's markdown import renders
them, checked 2026-10-21). After the pictures:

3. ``docs +fetch --scope outline --detail with-ids`` for the headings' block
   ids, then one ``docs +update --command str_replace --doc-format markdown``
   per link to a heading of the note, swapping its placeholder for the
   document's own link to that heading.

What the document now holds comes back as ``sync``: the document, its
revision after these writes, the note's digest, and which Feishu file each
screenshot became. It is what keeping the two copies in step starts from.

The work is split by part, each in its own module beside this one:
``lark_cli_run`` runs lark-cli and reads its output, ``lark_cli_images``
handles the screenshots, ``lark_cli_records`` keeps what a task knows about its
document, ``lark_cli_update`` writes chapters (and rewrites the changed ones),
``lark_cli_finish`` does what follows the writes. This module keeps the entry
point and the names other code imports from it.

A name tests replace is replaced where it lives, and every caller looks it up
there at call time: ``_run`` and ``_resolve_lark_cli_bin`` on ``lark_cli_run``,
``_insert_images`` on ``lark_cli_images``, ``export_markdown_via_lark_cli``
here. The ``_run`` and ``_resolve_lark_cli_bin`` below only pass through to
``lark_cli_run`` for the modules that call them as ``lark_cli_exporter.X``;
replacing them here would miss every call made inside these modules
(``tests/test_lark_cli_export.py`` checks no test does).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.core import feishu_note, lark_cli_images, lark_cli_run
from backend.core.lark_cli_finish import _finish, modified_times
from backend.core.lark_cli_images import _prepare_images
from backend.core.lark_cli_records import (
    CREATED,
    PUSHED_NOTE,
    _created_unrecorded,
    _forget_created,
    _remember_created,
    note_digest,
    pushed_note_path,
    reread_sections,
    save_pushed_note,
    sections_unread,
)
from backend.core.lark_cli_run import (
    LARK_CLI_NOT_FOUND,
    LarkCliError,
    Runner,
    _tail,
    lark_cli_ready,
    parse_lark_cli_output,
)
from backend.core.lark_cli_update import UnfinishedUpdate, _feishu_chunk, _response, _update

logger = logging.getLogger(__name__)

DEFAULT_WIKI_SPACE = "my_library"


def _resolve_lark_cli_bin(explicit: Optional[str] = None) -> Optional[str]:
    """``lark_cli_run._resolve_lark_cli_bin``, looked up when called."""
    return lark_cli_run._resolve_lark_cli_bin(explicit)


def _run(runner: Runner, cmd: List[str], **kwargs: Any) -> dict[str, Any]:
    """``lark_cli_run._run``, looked up when called."""
    return lark_cli_run._run(runner, cmd, **kwargs)


def _first_push_of_older_record(
    resumed: dict[str, Any], artifact_root: Optional[Path], task_id: Optional[str], state: str, *, tight: bool,
) -> dict[str, Any]:
    """What an export cut off under a version that kept only the document
    wrote into it: the note as pushed, saved before that record, read before
    ``_update`` replaces it, and kept with the record so a resume cut off in
    turn reads the same. Its markers are taken whatever their push."""
    path = pushed_note_path(artifact_root, task_id, state)
    pushed = path.read_text(encoding="utf-8") if path is not None and path.is_file() else ""
    parts = feishu_note.sections(pushed)
    links: List[feishu_note.HeadingLink] = []
    written = _feishu_chunk(parts, list(range(len(parts))), links, tight=tight)
    _markdown, pending, _missing = _prepare_images(written, task_id=task_id, artifact_root=artifact_root)
    first = lark_cli_images.first_push(None, pending, links, tracked=False)
    _remember_created(artifact_root, task_id, {
        "doc_id": resumed["doc_id"], "url": resumed.get("url"), "revision_id": resumed.get("revision_id"),
        "first_push": first,
    }, state)
    return first


def export_markdown_via_lark_cli(
    title: str,
    markdown: str,
    *,
    wiki_space: Optional[str] = None,
    lark_cli_bin: Optional[str] = None,
    timeout: int = 120,
    task_id: Optional[str] = None,
    artifact_root: Optional[Path] = None,
    previous: Optional[dict[str, Any]] = None,
    keep_feishu: Optional[Callable[[str], None]] = None,
    runner: Runner = subprocess.run,
    parent_token: Optional[str] = None,
    state: str = "",
    tight: bool = False,
) -> Dict[str, Any]:
    """Put the note in Feishu as the signed-in user, with its screenshots.

    ``parent_token`` creates the document under that wiki node instead of at
    the root of ``wiki_space``. ``state`` prefixes the files this task keeps
    about its document, so a second document of the same task (its review
    note) keeps its own. ``tight`` lays it out as the review note reads in
    Obsidian (``feishu_note._layout``).

    A note with no document yet gets a new one. A note whose document an
    earlier export recorded (``previous``, the ``sync`` it returned) is updated
    in that document, chapter by chapter (``_update``); ``keep_feishu`` gets the
    document as markdown before anything in it is replaced."""
    bin_path = lark_cli_run._resolve_lark_cli_bin(lark_cli_bin)
    if not bin_path:
        raise LarkCliError(
            f"{LARK_CLI_NOT_FOUND}. Install @larksuite/cli globally or set FLUENTFLOW_LARK_CLI_BIN."
        )
    resumed = None
    first: Optional[dict[str, Any]] = None
    if not (previous and previous.get("doc_id")):
        # A document this task created in an export cut off before it was
        # recorded is written into again, not made a second time.
        resumed = _created_unrecorded(artifact_root, task_id, state)
        if resumed is not None:
            first = resumed.pop("first_push", None)
            if first is None:
                first = _first_push_of_older_record(resumed, artifact_root, task_id, state, tight=tight)
            # The pictures the record says went in are known pictures of
            # the document: a chapter ``_update`` rewrites drops them.
            resumed["images"] = lark_cli_images.recorded_pictures(first, resumed["images"])
            previous = resumed
    if previous and previous.get("doc_id") and sections_unread(previous):
        previous = reread_sections(previous, task_id=task_id, artifact_root=artifact_root,
                                   bin_path=bin_path, runner=runner, state=state)
    if previous and previous.get("doc_id") and isinstance(previous.get("sections"), list):
        updated = _update(
            markdown, previous, bin_path=bin_path, timeout=timeout, task_id=task_id,
            artifact_root=artifact_root, keep_feishu=keep_feishu, runner=runner, state=state, tight=tight,
        )
        if resumed is not None:
            # The export that made the document may have been cut off before
            # every screenshot was in: the ones still missing go in at the
            # markers it left, and the task learns what each became.
            doc_id = str(previous["doc_id"])
            parts = feishu_note.sections(markdown)
            written = _feishu_chunk(parts, list(range(len(parts))), [], tight=tight)
            sync = updated["sync"]
            if lark_cli_images.tracked(first):
                lark_cli_images.forget_rewritten(first, sync.get("images") or [])
            # What the chapters just rewritten became, for a resume cut off
            # in turn: their captions carry no marker any more.
            record = {
                "doc_id": doc_id, "url": previous.get("url"), "revision_id": previous.get("revision_id"),
                "first_push": first, "images": sync.get("images") or [],
            }
            _remember_created(artifact_root, task_id, record, state)
            images, errors, pending, missing, content = lark_cli_images.resume_pictures(
                runner, bin_path, doc_id, written, first, list(sync.get("images") or []),
                task_id=task_id, artifact_root=artifact_root,
                save=lambda: _remember_created(artifact_root, task_id, record, state),
            )
            sync["images"] = images
            # It may also have been cut off before its chapter links were
            # pointed at their chapters. The placeholders it wrote that are
            # still there (in chapters this resume did not rewrite) are
            # pointed now; ``_update`` pointed its own before this read, so
            # a placeholder found here is never one of its.
            links = [
                feishu_note.HeadingLink(**link) for link in first.get("links") or []
                if re.search(re.escape(link["placeholder"]) + r"(?!\d)", content)
            ]
            _finish(runner, bin_path, doc_id, str(previous.get("url") or ""), links, parts, sync)
            _forget_created(artifact_root, task_id, state)
            updated = {**updated, **_response(sync, pending, missing, images, errors)}
        return updated

    space = (wiki_space or os.environ.get("FLUENTFLOW_LARK_CLI_WIKI_SPACE") or "").strip() or DEFAULT_WIKI_SPACE
    parts = feishu_note.sections(markdown)
    links: List[feishu_note.HeadingLink] = []
    written = _feishu_chunk(parts, list(range(len(parts))), links, tight=tight)
    push = uuid.uuid4().hex[:8]
    export_markdown, pending, missing = _prepare_images(
        written,
        task_id=task_id,
        artifact_root=artifact_root,
        push=push,
    )

    logger.info(
        "lark-cli export: parent=%s title_len=%d md_len=%d images=%d missing=%d",
        space, len(title), len(export_markdown), len(pending), len(missing),
    )
    data = lark_cli_run._run(
        runner,
        [
            bin_path, "docs", "+create",
            "--doc-format", "markdown",
            "--title", title,
            "--content", "-",
            *(["--parent-token", parent_token] if parent_token else ["--parent-position", space]),
            "--as", "user",
        ],
        timeout=timeout,
        input_text=export_markdown,
    )
    document = data.get("document") if isinstance(data.get("document"), dict) else data
    doc_id = str(document.get("document_id") or document.get("doc_id") or document.get("doc_token") or "").strip()
    doc_url = str(document.get("url") or document.get("doc_url") or data.get("url") or "").strip()
    if not doc_url:
        raise LarkCliError(
            "lark-cli 说文档已创建，但没有返回文档链接；请到飞书「我的文档库」里找标题为"
            f"「{title}」的文档。返回内容：{_tail(json.dumps(data, ensure_ascii=False))}"
        )

    progress = None
    if doc_id:
        # From here the document exists: an export cut off before it is
        # recorded on the task finds it again (``_created_unrecorded``).
        save_pushed_note(artifact_root, task_id, markdown, state)
        first = lark_cli_images.first_push(push, pending, links)
        record = {"doc_id": doc_id, "url": doc_url, "revision_id": document.get("revision_id"), "first_push": first}
        _remember_created(artifact_root, task_id, record, state)
        progress = lark_cli_images.note_progress(
            first, lambda: _remember_created(artifact_root, task_id, record, state),
        )
    images, errors = lark_cli_images._insert_images(runner, bin_path, doc_id, pending, progress)
    sync = {
        "doc_id": doc_id or None,
        "url": doc_url,
        "revision_id": document.get("revision_id"),
        "note_digest": note_digest(markdown),
        "images": images + [{**item, "file_token": None, "block_id": None} for item in missing],
        "headings": {},
        "sections": [],
        "link_errors": [],
    }
    if doc_id:
        _finish(runner, bin_path, doc_id, doc_url, links, parts, sync)
        save_pushed_note(artifact_root, task_id, markdown, state)
        _forget_created(artifact_root, task_id, state)
    return _response(sync, pending, missing, images, errors, wiki_space=space)


__all__ = [
    "CREATED",
    "DEFAULT_WIKI_SPACE",
    "LARK_CLI_NOT_FOUND",
    "LarkCliError",
    "PUSHED_NOTE",
    "UnfinishedUpdate",
    "export_markdown_via_lark_cli",
    "lark_cli_ready",
    "modified_times",
    "note_digest",
    "pushed_note_path",
    "parse_lark_cli_output",
    "reread_sections",
    "save_pushed_note",
    "sections_unread",
]
