"""The note's local screenshots in a Feishu export through lark-cli.

Feishu can only fetch http(s) images from Markdown, so before a write each
local screenshot is swapped for its caption paragraph (``图 N：说明``), led by
a marker unique to that picture in that push (``_prepare_images``). After the
write each picture is inserted right above its marker and the marker removed
(``_insert_images``). Callers look ``_insert_images`` up on this module at
call time, so a test replaces it here.

An export cut off after its document was made but before every picture was in
leaves markers in the document; carrying it on (``resume_pictures``) puts in
the pictures still missing at their markers and takes the markers out.
"""

from __future__ import annotations

import logging
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

from backend.core import lark_cli_run, note_spacing
from backend.core.lark_cli_run import LarkCliError, Runner
from backend.core.lark_exporter import _resolve_markdown_artifact_image

logger = logging.getLogger(__name__)

NOT_UPLOADED = "（截图没有传上去）"
_RE_MD_IMAGE_LINE = re.compile(r"^\s*!\[(.*?)\]\((.*?)\)\s*$")
_CAPTION_STRIP = re.compile(r"[\\`*_\[\]$~<>#|]")
_MARKER = re.compile(r"⟦FFIMG-[0-9a-f]+-\d+⟧")
_FIGURE_NUMBER = re.compile(r"^图 \d+：")


def _caption_text(index: int, alt: str) -> str:
    label = _CAPTION_STRIP.sub("", alt or "").replace("...", "…")
    label = re.sub(r"\s+", " ", label).strip()
    if not label:
        label = "截图"
    if len(label) > 60:
        label = label[:60] + "…"
    return f"图 {index}：{label}"


def _prepare_images(
    markdown: str,
    *,
    task_id: Optional[str],
    artifact_root: Optional[Path],
    first_figure: int = 0,
    push: str = "",
) -> tuple[str, List[dict[str, Any]], List[dict[str, Any]]]:
    """Swap each local screenshot for its caption paragraph, the ones to upload
    led by a marker unique to the picture in this push.

    Returns the markdown to write, the screenshots to insert afterwards, and
    the local screenshots whose file could not be found (named in the doc as
    text, counted as failed uploads). Each keeps the note's own image line and
    its caption, so the way back can put the picture back as it was.
    """
    push = push or uuid.uuid4().hex[:8]
    out: List[str] = []
    pending: List[dict[str, Any]] = []
    missing: List[dict[str, Any]] = []
    figure = first_figure
    lines = markdown.split("\n")
    for line, in_code in zip(lines, note_spacing.in_code(lines)):
        match = None if in_code else _RE_MD_IMAGE_LINE.match(line)
        if not match:
            out.append(line)
            continue
        alt, src = match.group(1) or "", (match.group(2) or "").strip()
        scheme = urlparse(src).scheme.lower()
        if scheme in ("http", "https"):
            out.append(line)  # Feishu downloads web images itself.
            continue
        figure += 1
        caption = _caption_text(figure, alt)
        path = _resolve_markdown_artifact_image(src, task_id=task_id, artifact_root=artifact_root)
        record = {"src": src, "line": line.strip(), "caption": caption if path else f"{caption}{NOT_UPLOADED}"}
        if path:
            marker = f"⟦FFIMG-{push}-{figure}⟧"
            out.extend(["", marker + caption, ""])
            pending.append({**record, "path": path, "marker": marker})
        else:
            out.extend(["", record["caption"], ""])
            missing.append(record)
    return "\n".join(out), pending, missing


def _insert_images(
    runner: Runner, bin_path: str, doc_id: str, pending: List[dict[str, Any]],
) -> tuple[List[dict[str, Any]], List[str]]:
    """Each screenshot right above its caption; what each became in Feishu,
    and the ones that did not make it. The marker goes either way."""
    images: List[dict[str, Any]] = []
    errors: List[str] = []
    for item in pending:
        if not doc_id:
            errors.append(item["src"])
            continue
        path: Path = item["path"]
        record = {"src": item["src"], "line": item["line"], "caption": item["caption"], "name": path.name}
        try:
            inserted = lark_cli_run._run(
                runner,
                [
                    bin_path, "docs", "+media-insert",
                    "--doc", doc_id,
                    "--file", path.name,
                    "--selection-with-ellipsis", item["marker"],
                    "--before",
                    "--as", "user",
                ],
                timeout=90,
                cwd=path.parent,
            )
            images.append({**record, "file_token": inserted.get("file_token"), "block_id": inserted.get("block_id")})
        except LarkCliError as exc:
            logger.warning("lark-cli image insert skipped for %s: %s", path.name, exc)
            errors.append(item["src"])
            images.append({**record, "caption": item["caption"], "file_token": None, "block_id": None})
        try:
            lark_cli_run._run(
                runner,
                [bin_path, "docs", "+update", "--doc", doc_id, "--command", "str_replace",
                 "--doc-format", "markdown", "--pattern", item["marker"], "--content", "", "--as", "user"],
                timeout=60,
            )
        except LarkCliError as exc:
            logger.warning("lark-cli could not remove image marker %s: %s", item["marker"], exc)
    return images, errors


def _local_images_in(text: str) -> int:
    """The screenshots ``_prepare_images`` numbers in ``text`` (not the image
    lines inside code), so a chapter written on its own keeps the figure
    numbers of the whole note."""
    count = 0
    lines = text.split("\n")
    for line, in_code in zip(lines, note_spacing.in_code(lines)):
        match = None if in_code else _RE_MD_IMAGE_LINE.match(line)
        if match and urlparse((match.group(2) or "").strip()).scheme.lower() not in ("http", "https"):
            count += 1
    return count


class _Paragraphs(HTMLParser):
    """The pictures and paragraphs of a with-ids fetch, in document order:
    ``("img", attrs)`` and ``("p", text)``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, Any]] = []
        self._text: list[str] | None = None

    def handle_starttag(self, tag, attrs):  # noqa: D102
        if tag == "img":
            self.items.append(("img", dict(attrs)))
        elif tag == "p":
            self._text = []

    def handle_startendtag(self, tag, attrs):  # noqa: D102
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):  # noqa: D102
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):  # noqa: D102
        if tag == "p" and self._text is not None:
            self.items.append(("p", "".join(self._text).strip()))
            self._text = None


def resume_pictures(
    runner: Runner, bin_path: str, doc_id: str, written: str, *,
    task_id: Optional[str], artifact_root: Optional[Path],
) -> tuple[List[dict[str, Any]], List[str], List[dict[str, Any]], List[dict[str, Any]]]:
    """Finish the pictures of a document an export made and was cut off in.

    ``written`` is the note as that export wrote it (``_feishu_chunk``), so
    its captions are numbered as the document's are. Each caption is looked
    for in the document: a picture already right above it (the one this note
    names) is kept and its marker, if still there, removed; a caption still
    led by its marker gets its picture now (``_insert_images``, which takes
    the marker out). A marker whose picture the note no longer has is taken
    out and its caption left as words. Returns what each screenshot became in
    Feishu, the ones that did not make it, and the note's screenshots
    (``_prepare_images``'s to-upload and not-found lists) for the counts.

    Checked on a document made for the test in the owner's Feishu
    (2026-11-25): a picture inserted just before the cut-off sits right above
    its marker paragraph, so it is not put in twice, also when Feishu shows it
    without its file name."""
    fetched = lark_cli_run._run(
        runner, [bin_path, "docs", "+fetch", "--doc", doc_id, "--detail", "with-ids", "--as", "user"], timeout=60,
    )
    document = fetched.get("document") if isinstance(fetched.get("document"), dict) else fetched
    parser = _Paragraphs()
    parser.feed(str(document.get("content") or ""))
    _markdown, pending, missing = _prepare_images(written, task_id=task_id, artifact_root=artifact_root)
    left = list(pending)
    images: List[dict[str, Any]] = []
    errors: List[str] = []
    insert: List[dict[str, Any]] = []
    stray: List[str] = []

    def take(caption: str) -> Optional[dict[str, Any]]:
        for item in left:
            if item["caption"] == caption:
                left.remove(item)
                return item
        # The note changed since and the figures were numbered again: the
        # caption's words, when only one picture has them.
        words = _FIGURE_NUMBER.sub("", caption)
        same = [item for item in left if _FIGURE_NUMBER.sub("", item["caption"]) == words]
        if len(same) == 1:
            left.remove(same[0])
            return same[0]
        return None

    for position, (kind, value) in enumerate(parser.items):
        if kind != "p":
            continue
        marker = _MARKER.match(value)
        item = take(value[marker.end():] if marker else value)
        if item is None:
            if marker:
                stray.append(marker.group(0))
            continue
        record = {"src": item["src"], "line": item["line"], "caption": item["caption"], "name": item["path"].name}
        above = parser.items[position - 1] if position else None
        # The picture Feishu shows right above the caption is this one's when
        # it carries this file name, or no name at all: only one document was
        # checked to carry it, and a picture inserted just before the cut-off
        # must not go in twice. One named after another file is not this one.
        if above and above[0] == "img" and above[1].get("name") in (item["path"].name, "", None):
            images.append({**record, "file_token": above[1].get("src"), "block_id": above[1].get("id")})
            if marker:
                stray.append(marker.group(0))
        elif marker:
            insert.append({**item, "marker": marker.group(0)})
        else:
            # Its upload failed in that export and its marker went with it.
            images.append({**record, "file_token": None, "block_id": None})
            errors.append(item["src"])
    inserted, failed = _insert_images(runner, bin_path, doc_id, insert)
    images.extend(inserted)
    errors.extend(failed)
    for marker_text in stray:
        try:
            lark_cli_run._run(
                runner,
                [bin_path, "docs", "+update", "--doc", doc_id, "--command", "str_replace",
                 "--doc-format", "markdown", "--pattern", marker_text, "--content", "", "--as", "user"],
                timeout=60,
            )
        except LarkCliError as exc:
            logger.warning("lark-cli could not remove image marker %s: %s", marker_text, exc)
    for item in left:
        # Its caption is not in the document: the chapter was not written.
        images.append({"src": item["src"], "line": item["line"], "caption": item["caption"],
                       "name": item["path"].name, "file_token": None, "block_id": None})
        errors.append(item["src"])
    images.extend({**item, "file_token": None, "block_id": None} for item in missing)
    return images, errors, pending, missing
