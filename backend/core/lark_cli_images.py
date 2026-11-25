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
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

from backend.core import feishu_note, lark_cli_run, note_spacing
from backend.core.lark_cli_run import LarkCliError, Runner
from backend.core.lark_exporter import _resolve_markdown_artifact_image

logger = logging.getLogger(__name__)

NOT_UPLOADED = "（截图没有传上去）"
_RE_MD_IMAGE_LINE = re.compile(r"^\s*!\[(.*?)\]\((.*?)\)\s*$")
_CAPTION_STRIP = re.compile(r"[\\`*_\[\]$~<>#|]")
_MARKER = re.compile(r"⟦FFIMG-([0-9a-f]+)-(\d+)⟧")
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


def first_push(
    push: Optional[str], pending: List[dict[str, Any]], links: List[feishu_note.HeadingLink],
) -> dict[str, Any]:
    """What the export that makes a document wrote into it, kept beside the
    task until that export is finished (``feishu_created.json``): each
    marker's picture by its figure number, and each chapter link's
    placeholder. A resume reads the markers and placeholders left in the
    document against this, not against the note as it is by then: the note
    may have been edited and its figures numbered again. ``push`` None (a
    record from a version that kept only the document) takes a marker of any
    push."""
    return {
        "push": push,
        "pictures": [
            {"figure": int(_MARKER.match(item["marker"]).group(2)), "src": item["src"], "line": item["line"],
             "caption": item["caption"], "name": item["path"].name}
            for item in pending
        ],
        "links": [{"placeholder": link.placeholder, "label": link.label, "heading": link.heading} for link in links],
    }


_VOID = {"img", "br", "hr", "input", "col", "area", "embed", "source", "track", "wbr"}


class _Blocks(HTMLParser):
    """Every top-level block of a with-ids fetch, in document order, with its
    words and, for a block that is a picture (an ``<img>``, or a block holding
    only one), that picture's attributes. Every block counts, so "the block
    right above" a caption is the one Feishu shows there, not the last picture
    or paragraph before it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self._depth = 0

    def _open(self, tag: str, attrs: list[tuple[str, Optional[str]]], closes: bool) -> None:
        if self._depth == 0:
            self.blocks.append({"tag": tag, "id": dict(attrs).get("id"), "text": [], "imgs": []})
        if tag == "img" and self.blocks:
            self.blocks[-1]["imgs"].append(dict(attrs))
        if not closes and tag not in _VOID:
            self._depth += 1

    def handle_starttag(self, tag, attrs):  # noqa: D102
        self._open(tag, attrs, closes=False)

    def handle_startendtag(self, tag, attrs):  # noqa: D102
        self._open(tag, attrs, closes=True)

    def handle_endtag(self, tag):  # noqa: D102
        if tag not in _VOID and self._depth:
            self._depth -= 1

    def handle_data(self, data):  # noqa: D102
        if self._depth and self.blocks:
            self.blocks[-1]["text"].append(data)


def _blocks(content: str) -> list[dict[str, Any]]:
    parser = _Blocks()
    parser.feed(content)
    blocks = []
    for block in parser.blocks:
        if block["tag"] == "title":
            continue
        text = "".join(block["text"]).strip()
        picture = block["imgs"][0] if len(block["imgs"]) == 1 and not text else None
        blocks.append({"tag": block["tag"], "id": block["id"], "text": text, "picture": picture})
    return blocks


def resume_pictures(
    runner: Runner, bin_path: str, doc_id: str, written: str, first: dict[str, Any], known: List[dict[str, Any]], *,
    task_id: Optional[str], artifact_root: Optional[Path],
) -> tuple[List[dict[str, Any]], List[str], List[dict[str, Any]], List[dict[str, Any]], str]:
    """Finish the pictures of a document an export made and was cut off in.

    ``first`` is what that export wrote (``first_push``); ``known`` what the
    chapters this resume rewrote became (``_update``'s pictures: they are
    already in). ``written`` is the note as it is now (``_feishu_chunk``), for
    the counts.

    A paragraph led by a marker is the caption of the picture that marker
    was made for: a picture right above it (this one's, or one Feishu shows
    without a name) means it is in and only the marker goes; otherwise the
    picture goes in at the marker now (``_insert_images``, which takes the
    marker out). A marker no picture was made for is taken out and its caption
    left as words. A caption paragraph whose marker is already gone is that
    export's picture only when it reads exactly as that export numbered it and
    a picture not rewritten in this resume is right above it. Every picture of
    the note that ends up in none of these is listed as not in Feishu.

    Returns what each screenshot became in Feishu, the ones that are not in,
    the note's screenshots (to-upload and not-found, a picture already in
    Feishu counted as uploaded even if its file is gone since) and the
    document as fetched (for its placeholders).

    Checked on a document made for the test in the owner's Feishu
    (2026-11-25): a picture inserted just before the cut-off sits right above
    its marker paragraph, so it is not put in twice, also when Feishu shows it
    without its file name."""
    fetched = lark_cli_run._run(
        runner, [bin_path, "docs", "+fetch", "--doc", doc_id, "--detail", "with-ids", "--as", "user"], timeout=60,
    )
    document = fetched.get("document") if isinstance(fetched.get("document"), dict) else fetched
    content = str(document.get("content") or "")
    blocks = _blocks(content)
    _markdown, pending, missing = _prepare_images(written, task_id=task_id, artifact_root=artifact_root)
    push = first.get("push")
    figures = {int(picture["figure"]): picture for picture in first.get("pictures") or []}
    rewritten = {image.get("block_id") for image in known if image.get("block_id")}
    done: set[int] = set()
    found: List[dict[str, Any]] = []
    insert: List[dict[str, Any]] = []
    stray: List[str] = []

    def picture_above(position: int) -> Optional[dict[str, Any]]:
        above = blocks[position - 1] if position else None
        picture = above["picture"] if above else None
        if picture is None or above["id"] in rewritten or picture.get("id") in rewritten:
            return None
        return {**picture, "block_id": picture.get("id") or above["id"]}

    for position, block in enumerate(blocks):
        text = block["text"]
        marker = _MARKER.match(text) if block["tag"] == "p" else None
        if marker:
            figure = int(marker.group(2))
            picture = figures.get(figure) if push in (None, marker.group(1)) and figure not in done else None
            if picture is None:
                stray.append(marker.group(0))
                continue
        else:
            figure = next((n for n, p in figures.items() if n not in done and p["caption"] == text), -1)
            if block["tag"] != "p" or not _FIGURE_NUMBER.match(text) or figure < 0:
                continue
            picture = figures[figure]
        record = {key: picture[key] for key in ("src", "line", "caption", "name")}
        above = picture_above(position)
        # The picture Feishu shows right above the caption is this one's when
        # it carries this file name, or no name at all: only one document was
        # checked to carry it, and a picture inserted just before the cut-off
        # must not go in twice. One named after another file is not this one.
        if above is not None and above.get("name") in (picture["name"], "", None):
            done.add(figure)
            found.append({**record, "file_token": above.get("src") or above.get("token"),
                          "block_id": above["block_id"]})
            if marker:
                stray.append(marker.group(0))
        elif marker:
            done.add(figure)
            path = _resolve_markdown_artifact_image(picture["src"], task_id=task_id, artifact_root=artifact_root)
            if path:
                insert.append({**record, "path": path, "marker": marker.group(0)})
            else:
                # Its file is gone since: the caption stays as words.
                found.append({**record, "file_token": None, "block_id": None})
                stray.append(marker.group(0))
    inserted, _failed = _insert_images(runner, bin_path, doc_id, insert)
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
    images = list(known) + found + inserted
    # Every screenshot of the note is accounted for once, by its image line.
    accounted = Counter(image["line"] for image in images)
    in_feishu = Counter(image["line"] for image in images if image.get("file_token") or image.get("block_id"))
    for item in pending + missing:
        if accounted[item["line"]]:
            accounted[item["line"]] -= 1
        else:
            images.append({"src": item["src"], "line": item["line"], "caption": item["caption"],
                           "name": item["path"].name if item.get("path") else None,
                           "file_token": None, "block_id": None})
    not_found = []
    for item in missing:
        if in_feishu[item["line"]]:
            # In Feishu already; only its local file is gone since.
            in_feishu[item["line"]] -= 1
            pending.append(item)
        else:
            not_found.append(item)
    unreported = Counter(item["line"] for item in not_found)
    errors = []
    for image in images:
        if image.get("file_token") or image.get("block_id"):
            continue
        if unreported[image["line"]]:
            unreported[image["line"]] -= 1  # listed as not found
            continue
        errors.append(image["src"])
    return images, errors, pending, not_found, content
