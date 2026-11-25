"""What a task keeps beside itself about its Feishu document.

The document an export created but was cut off before recording
(``feishu_created.json``), the note as last pushed (``feishu_pushed.md``),
the note's digest, and reading a pushed document's chapters again when they
could not be read right after the push (``reread_sections``). ``state``
prefixes the file names, so a second document of the same task (its review
note) keeps its own.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from backend.core import feishu_note, lark_cli_run
from backend.core.lark_cli_run import LarkCliError, Runner

logger = logging.getLogger(__name__)

CREATED = "feishu_created.json"
PUSHED_NOTE = "feishu_pushed.md"


def note_digest(markdown: str) -> str:
    return hashlib.sha256((markdown or "").encode("utf-8")).hexdigest()


def _created_path(artifact_root: Optional[Path], task_id: Optional[str], state: str = "") -> Optional[Path]:
    if artifact_root is None or not task_id:
        return None
    return Path(artifact_root) / task_id / f"{state}{CREATED}"


def _remember_created(
    artifact_root: Optional[Path], task_id: Optional[str], document: dict[str, Any], state: str = "",
) -> None:
    path = _created_path(artifact_root, task_id, state)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not note the Feishu document of %s: %s", task_id, exc)


def _forget_created(artifact_root: Optional[Path], task_id: Optional[str], state: str = "") -> None:
    path = _created_path(artifact_root, task_id, state)
    if path is not None:
        path.unlink(missing_ok=True)


def _created_unrecorded(
    artifact_root: Optional[Path], task_id: Optional[str], state: str = "",
) -> Optional[dict[str, Any]]:
    """The document an export of this task created and never finished, as a
    ``sync`` whose chapters are read again from it, or None."""
    path = _created_path(artifact_root, task_id, state)
    if path is None or not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or not document.get("doc_id"):
        return None
    return {"doc_id": str(document["doc_id"]), "url": str(document.get("url") or ""),
            "revision_id": document.get("revision_id"), "images": [], "headings": {},
            "sections": None, "link_errors": []}


def pushed_note_path(artifact_root: Optional[Path], task_id: Optional[str], state: str = "") -> Optional[Path]:
    """The note as last pushed to Feishu: what edits made in Feishu are laid
    over when they come back, so the chapters not edited there keep the note's
    own text."""
    if artifact_root is None or not task_id:
        return None
    return Path(artifact_root) / task_id / f"{state}{PUSHED_NOTE}"


def sections_unread(sync: Optional[dict[str, Any]]) -> bool:
    """A document that was pushed but whose chapters could not be read back
    afterwards (``_finish``). A stopped sync also has no chapters, on purpose:
    exporting it again makes a new document."""
    return (
        isinstance(sync, dict) and bool(sync.get("doc_id")) and not sync.get("stopped")
        and "sections" in sync and sync["sections"] is None
    )


def reread_sections(
    sync: dict[str, Any],
    *,
    task_id: Optional[str],
    artifact_root: Optional[Path],
    bin_path: str,
    runner: Runner,
    state: str = "",
) -> dict[str, Any]:
    """``sync`` with its chapters read again: the note as last pushed, matched
    to the document's headings now. Raises when either cannot be read, so the
    caller writes nothing rather than a second document."""
    path = pushed_note_path(artifact_root, task_id, state)
    if path is None or not path.is_file():
        raise LarkCliError("飞书那份的章节对不上了：找不到上次推送的笔记。")
    pushed = path.read_text(encoding="utf-8")
    outline = lark_cli_run._run(
        runner,
        [bin_path, "docs", "+fetch", "--doc", str(sync["doc_id"]), "--scope", "outline",
         "--detail", "with-ids", "--as", "user"],
        timeout=60,
    )
    fetched = outline.get("document") if isinstance(outline.get("document"), dict) else outline
    content = str(fetched.get("content") or "")
    parts = feishu_note.sections(pushed)
    return {
        **sync,
        "headings": feishu_note.headings(content),
        "sections": feishu_note.section_records(parts, feishu_note.heading_list(content)),
    }


def save_pushed_note(artifact_root: Optional[Path], task_id: Optional[str], markdown: str, state: str = "") -> None:
    path = pushed_note_path(artifact_root, task_id, state)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown or "", encoding="utf-8")
    except OSError as exc:
        logger.warning("could not keep the pushed note of %s: %s", task_id, exc)
