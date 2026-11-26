"""Writing the note's chapters into a Feishu document through lark-cli.

``_feishu_chunk`` turns chapters into what is written to Feishu (both for a
new document and for a rewrite); ``_update`` rewrites only the chapters that
changed since the last push, as ``feishu_note.plan_update`` says, and raises
:class:`UnfinishedUpdate` when a rewrite stops after it began writing. Its
links are numbered after the placeholders the document still holds, so a
resume's own link never shares a placeholder with the first push's.
``_response`` is the reply both a new document and an update return.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.core import feishu_note, lark_cli_images, lark_cli_run
from backend.core.lark_cli_finish import _finish
from backend.core.lark_cli_images import _local_images_in, _prepare_images
from backend.core.lark_cli_records import note_digest, save_pushed_note
from backend.core.lark_cli_run import Runner


def _feishu_chunk(
    parts: List[feishu_note.Section], indices: List[int], links: List[feishu_note.HeadingLink], *, body_only: bool = False,
    tight: bool = False, first_link: int = 0,
) -> str:
    """The chapters ``indices`` as written to Feishu, links numbered on from
    ``first_link`` and ``links`` (extended). A chapter followed by a ``##``
    chapter other than the first one ends with the divider before it, so
    rewriting either chapter later keeps the divider where it was."""
    first_h2 = next((i for i, part in enumerate(parts) if part.level == 2), None)
    pieces: List[str] = []
    for index in indices:
        following = parts[index + 1] if index + 1 < len(parts) else None
        divider = following is not None and following.level == 2 and index + 1 != first_h2
        gap = tight and following is not None and following.level >= 3
        text = parts[index].body if body_only else parts[index].text
        prepared = feishu_note.to_feishu(text, first_link=first_link + len(links), divider_after=divider, tight=tight,
                                         gap_after=gap)
        links.extend(prepared.links)
        if prepared.markdown.strip():
            pieces.append(prepared.markdown.strip("\n"))
    return "\n\n".join(pieces)


def _response(
    sync: dict[str, Any], pending: list, missing: list, images: list, errors: list, **extra: Any,
) -> Dict[str, Any]:
    return {
        "ok": True,
        "url": sync["url"],
        "doc_token": sync["doc_id"],
        "doc_id": sync["doc_id"],
        "via": "lark_cli",
        "markdown_format": "feishu_note",
        **extra,
        "image_count": len(pending) + len(missing),
        "image_upload_count": sum(1 for image in images if image.get("file_token") or image.get("block_id")),
        "image_upload_errors": [item["src"] for item in missing] + errors,
        "sync": sync,
    }


def _write_ops(
    ops: list,
    parts: List[feishu_note.Section],
    figures: List[int],
    links: List[feishu_note.HeadingLink],
    pending: List[dict[str, Any]],
    missing: List[dict[str, Any]],
    deleted: set[str],
    *,
    bin_path: str,
    doc_id: str,
    timeout: int,
    task_id: Optional[str],
    artifact_root: Optional[Path],
    runner: Runner,
    tight: bool = False,
    first_link: int = 0,
) -> int:
    """Run ``plan_update``'s deletes and inserts; returns the chapters written.
    Its links are numbered after ``first_link``, the last placeholder already
    in the document."""
    rewritten = 0
    for op in ops:
        if isinstance(op, feishu_note.Delete):
            for start in range(0, len(op.block_ids), 50):
                chunk = op.block_ids[start:start + 50]
                lark_cli_run._run(
                    runner,
                    [bin_path, "docs", "+update", "--doc", doc_id, "--command", "block_delete",
                     "--block-id", ",".join(chunk), "--as", "user"],
                    timeout=timeout,
                )
                deleted.update(chunk)
            continue
        written = _feishu_chunk(parts, op.parts, links, body_only=op.body_only, tight=tight, first_link=first_link)
        if not written.strip():
            # Nothing the page shows (a part marker only): the delete was enough.
            continue
        content, wanted, gone = _prepare_images(
            written, task_id=task_id, artifact_root=artifact_root, first_figure=figures[op.parts[0]],
        )
        pending.extend(wanted)
        missing.extend(gone)
        lark_cli_run._run(
            runner,
            [bin_path, "docs", "+update", "--doc", doc_id, "--command", "block_insert_after",
             "--block-id", op.anchor, "--doc-format", "markdown", "--content", "-", "--as", "user"],
            timeout=timeout,
            input_text=content,
        )
        rewritten += len(op.parts)
    return rewritten


def _occurrence_ids(keys: List[Any]) -> List[tuple[str, int]]:
    seen: dict[str, int] = {}
    found: List[tuple[str, int]] = []
    for key in keys:
        count = seen.get(str(key), 0)
        seen[str(key)] = count + 1
        found.append((str(key), count))
    return found


class UnfinishedUpdate(RuntimeError):
    """A chapter update that failed after it began writing: the document may
    hold a chapter half rewritten (its old paragraphs deleted, the new ones
    not written). The note is what the document should be; the next push
    finishes the job (feishu_sync), instead of reading the half-written
    chapter as an edit made in Feishu. ``chapters`` are the chapters it was
    changing, as (heading words, which occurrence of them)."""

    def __init__(self, message: str, chapters: List[tuple[str, int]]) -> None:
        super().__init__(message)
        self.chapters = chapters


def _update(
    markdown: str,
    previous: dict[str, Any],
    *,
    bin_path: str,
    timeout: int,
    task_id: Optional[str],
    artifact_root: Optional[Path],
    keep_feishu: Optional[Callable[[str], None]],
    runner: Runner,
    state: str = "",
    tight: bool = False,
) -> Dict[str, Any]:
    """Rewrite only the chapters that changed since ``previous`` was pushed.

    The document as it is now is read with its block ids; ``plan_update``
    compares the chapters pushed last time with the note's chapters now and
    says which blocks to delete and what to write after which block. Before the
    first write the document is handed to ``keep_feishu`` as markdown, so the
    version being replaced can be found again; if it cannot be read, nothing
    is written."""
    doc_id = str(previous["doc_id"])
    doc_url = str(previous.get("url") or "")
    fetched = lark_cli_run._run(
        runner,
        [bin_path, "docs", "+fetch", "--doc", doc_id, "--detail", "with-ids", "--as", "user"],
        timeout=timeout,
    )
    document = fetched.get("document") if isinstance(fetched.get("document"), dict) else fetched
    parts = feishu_note.sections(markdown)
    ops = feishu_note.plan_update(
        previous["sections"], parts, feishu_note.top_blocks(str(document.get("content") or "")), doc_id,
    )
    sync = {**previous, "note_digest": note_digest(markdown), "link_errors": []}
    if not ops:
        return {**_response(sync, [], [], [], []), "updated": True, "sections_rewritten": 0}

    if keep_feishu is not None:
        as_markdown = lark_cli_run._run(
            runner,
            [bin_path, "docs", "+fetch", "--doc", doc_id, "--doc-format", "markdown", "--as", "user"],
            timeout=timeout,
        )
        shown = as_markdown.get("document") if isinstance(as_markdown.get("document"), dict) else as_markdown
        keep_feishu(str(shown.get("content") or ""))

    figures: list[int] = []
    running = 0
    for part in parts:
        figures.append(running)
        running += _local_images_in(part.text)

    links: List[feishu_note.HeadingLink] = []
    pending: List[dict[str, Any]] = []
    missing: List[dict[str, Any]] = []
    deleted: set[str] = set()
    rewritten = 0
    # The placeholders the document still holds are the ones it was written
    # with before this push (a resume's first push's, or one a failed point
    # left): this push numbers its own after them, so no two places in the
    # document read the same and neither is pointed for the other.
    first_link = feishu_note.last_placeholder(str(document.get("content") or ""))
    try:
        rewritten = _write_ops(ops, parts, figures, links, pending, missing, deleted,
                               bin_path=bin_path, doc_id=doc_id, timeout=timeout, task_id=task_id,
                               artifact_root=artifact_root, runner=runner, tight=tight, first_link=first_link)
    except Exception as exc:
        # Every chapter the push was changing: rewritten, added, removed or
        # moved (a move is a delete and an insert with nothing else changed).
        # A chapter is its heading's words and which occurrence of them it
        # is, so of two chapters with the same heading only the one changed
        # is listed.
        old_ids = _occurrence_ids([record.get("key") for record in previous["sections"]])
        new_ids = _occurrence_ids([part.key for part in parts])
        before = {(cid, record.get("digest")) for cid, record in zip(old_ids, previous["sections"])}
        after = {(cid, part.digest) for cid, part in zip(new_ids, parts)}
        doomed = {block for op in ops if isinstance(op, feishu_note.Delete) for block in op.block_ids}
        rewriting = sorted(
            {cid for cid, _ in before ^ after}
            | {new_ids[j] for op in ops if not isinstance(op, feishu_note.Delete) for j in op.parts}
            | {cid for cid, record in zip(old_ids, previous["sections"]) if record.get("heading_id") in doomed}
        )
        raise UnfinishedUpdate(str(exc), rewriting) from exc

    images, errors = lark_cli_images._insert_images(runner, bin_path, doc_id, pending)
    # A picture whose chapter was rewritten is in the new pictures, if it is
    # still in the note; one never uploaded is listed again by its chapter.
    rewritten_lines = {item["line"] for item in pending + missing}
    sync["images"] = [
        image for image in previous.get("images") or []
        if (image.get("block_id") not in deleted if image.get("block_id") else image.get("line") not in rewritten_lines)
    ] + images + [{**item, "file_token": None, "block_id": None} for item in missing]
    _finish(runner, bin_path, doc_id, doc_url, links, parts, sync)
    save_pushed_note(artifact_root, task_id, markdown, state)
    return {**_response(sync, pending, missing, images, errors), "updated": True, "sections_rewritten": rewritten}
