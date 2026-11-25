"""The note's local screenshots in a Feishu export through lark-cli.

Feishu can only fetch http(s) images from Markdown, so before a write each
local screenshot is swapped for its caption paragraph (``图 N：说明``), led by
a marker unique to that picture in that push (``_prepare_images``). After the
write each picture is inserted right above its marker and the marker removed
(``_insert_images``). Callers look ``_insert_images`` up on this module at
call time, so a test replaces it here.

An export cut off after its document was made but before every picture was in
leaves markers in the document; carrying it on (``resume_pictures``) puts in
the pictures still missing at their markers and takes the markers out. Which
pictures went in is recorded as they go (``note_progress``), so the resume
reads it from the record instead of guessing from the document.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, List, Optional
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

    A screenshot to insert also says how many pictures this markdown puts
    right above the place its picture will go (``above``: the note's web
    pictures written just before its caption, or before the callout holding
    its caption, since lark-cli inserts above that callout), so a resume can
    tell those from the screenshot.
    """
    push = push or uuid.uuid4().hex[:8]
    out: List[str] = []
    pending: List[dict[str, Any]] = []
    missing: List[dict[str, Any]] = []
    figure = first_figure
    run = 0  # web pictures right above the next line
    callouts = 0
    above_callout = 0
    lines = markdown.split("\n")
    for line, in_code in zip(lines, note_spacing.in_code(lines)):
        match = None if in_code else _RE_MD_IMAGE_LINE.match(line)
        if not match:
            out.append(line)
            if not in_code and feishu_note._CALLOUT_OPEN.match(line):
                if not callouts:
                    above_callout = run
                callouts += 1
            elif not in_code and callouts and feishu_note._CALLOUT_CLOSE.match(line):
                callouts -= 1
            if line.strip():
                run = 0
            continue
        alt, src = match.group(1) or "", (match.group(2) or "").strip()
        scheme = urlparse(src).scheme.lower()
        if scheme in ("http", "https"):
            out.append(line)  # Feishu downloads web images itself.
            run += 1
            continue
        figure += 1
        caption = _caption_text(figure, alt)
        path = _resolve_markdown_artifact_image(src, task_id=task_id, artifact_root=artifact_root)
        record = {"src": src, "line": line.strip(), "caption": caption if path else f"{caption}{NOT_UPLOADED}"}
        if path:
            marker = f"⟦FFIMG-{push}-{figure}⟧"
            out.extend(["", marker + caption, ""])
            pending.append({**record, "path": path, "marker": marker, "above": above_callout if callouts else run})
        else:
            out.extend(["", record["caption"], ""])
            missing.append(record)
        run = 0
    return "\n".join(out), pending, missing


Progress = Callable[[dict[str, Any], Optional[dict[str, Any]]], None]


def _insert_images(
    runner: Runner, bin_path: str, doc_id: str, pending: List[dict[str, Any]], progress: Optional[Progress] = None,
) -> tuple[List[dict[str, Any]], List[str]]:
    """Each screenshot right above its caption; what each became in Feishu,
    and the ones that did not make it. The marker goes either way.

    ``progress`` (``note_progress``) hears of each screenshot before its
    insert (None) and after it (what it became, ``{}`` when it failed), both
    before its marker goes."""
    images: List[dict[str, Any]] = []
    errors: List[str] = []
    for item in pending:
        if not doc_id:
            errors.append(item["src"])
            continue
        path: Path = item["path"]
        record = {"src": item["src"], "line": item["line"], "caption": item["caption"], "name": path.name}
        if progress is not None:
            progress(item, None)
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
        if progress is not None:
            progress(item, {key: images[-1][key] for key in ("file_token", "block_id") if images[-1][key]})
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
    push: Optional[str], pending: List[dict[str, Any]], links: List[feishu_note.HeadingLink], *,
    tracked: bool = True,
) -> dict[str, Any]:
    """What the export that makes a document wrote into it, kept beside the
    task until that export is finished (``feishu_created.json``): each
    marker's picture by its figure number, and each chapter link's
    placeholder. A resume reads the markers and placeholders left in the
    document against this, not against the note as it is by then: the note
    may have been edited and its figures numbered again. ``push`` None (a
    record from a version that kept only the document) takes a marker of any
    push.

    ``tracked`` (an export that records its pictures as they go in): the
    picture going in when it stopped (``inserting``, its figure number) and
    what each picture that went in became (``inserted``, by figure number),
    kept by ``note_progress``. A record without them is from a version that
    did not keep them, or rebuilt for one (``tracked`` False)."""
    first: dict[str, Any] = {
        "push": push,
        "pictures": [
            {"figure": int(_MARKER.match(item["marker"]).group(2)), "src": item["src"], "line": item["line"],
             "caption": item["caption"], "name": item["path"].name, "above": item.get("above", 0)}
            for item in pending
        ],
        "links": [{"placeholder": link.placeholder, "label": link.label, "heading": link.heading} for link in links],
    }
    if tracked:
        first.update(inserting=None, inserted={})
    return first


def tracked(first: dict[str, Any]) -> bool:
    """``first`` records its pictures as they go in (``first_push``)."""
    return isinstance(first.get("inserted"), dict)


def note_progress(first: dict[str, Any], save: Callable[[], None]) -> Progress:
    """The ``_insert_images`` progress that keeps ``first``'s record of its
    pictures (``save`` writes it): the figure about to go in, then what it
    became. Only the figure going in can have a picture of its own above its
    marker that the record does not name."""
    def note(item: dict[str, Any], result: Optional[dict[str, Any]]) -> None:
        figure = int(_MARKER.match(item["marker"]).group(2))
        if result is None:
            first["inserting"] = figure
        else:
            first["inserting"] = None
            if result:
                first["inserted"][str(figure)] = {"file_token": result.get("file_token"),
                                                  "block_id": result.get("block_id")}
        save()
    return note


def recorded_pictures(first: dict[str, Any], images: List[dict[str, Any]]) -> List[dict[str, Any]]:
    """``images`` (what a task knows its document's pictures became) with
    the pictures ``first`` records as gone in, each once. A resume hands
    them to ``_update`` with the others, so a chapter it rewrites drops
    them the way it drops any picture it deletes."""
    pictures = {str(picture["figure"]): picture for picture in first.get("pictures") or []}
    known = {(image.get("block_id"), image.get("file_token")) for image in images}
    added = [
        {**{key: pictures[figure][key] for key in ("src", "line", "caption", "name")},
         "file_token": became.get("file_token"), "block_id": became.get("block_id")}
        for figure, became in (first.get("inserted") or {}).items()
        if figure in pictures and (became.get("block_id"), became.get("file_token")) not in known
    ]
    return list(images) + added


def forget_rewritten(first: dict[str, Any], images: List[dict[str, Any]]) -> None:
    """Drop from ``first``'s record the pictures no longer in ``images``
    (their chapter was rewritten, the picture deleted with it)."""
    kept = {(image.get("block_id"), image.get("file_token")) for image in images}
    first["inserted"] = {
        figure: became for figure, became in (first.get("inserted") or {}).items()
        if (became.get("block_id"), became.get("file_token")) in kept
    }


_VOID = {"img", "br", "hr", "input", "col", "area", "embed", "source", "track", "wbr"}


class _Blocks(HTMLParser):
    """Every block of a with-ids fetch at any depth, in document order: an
    element with an id, or any element at the top level but a list (a
    ``<ul>`` has no id of its own: its items are the blocks). Each gathers
    the words and pictures inside it, of the blocks nested in it too, and
    knows how deep it sits and which top-level block holds it (``top``, that
    block's place among the top-level ones). Every block counts, so "the
    block right above" a place is the one Feishu shows there, not the last
    picture or paragraph before it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self._open_tags: list[Optional[int]] = []  # each open element's block, None for one that is not
        self._tops = 0

    def _within(self) -> list[int]:
        return [index for index in self._open_tags if index is not None]

    def _open(self, tag: str, attrs: list[tuple[str, Optional[str]]], closes: bool) -> None:
        attributes = dict(attrs)
        within = self._within()
        index = None
        if tag != "title" and (attributes.get("id") or (not within and tag not in ("ul", "ol"))):
            top = self.blocks[within[0]]["top"] if within else self._tops
            self._tops += not within
            self.blocks.append({"tag": tag, "id": attributes.get("id"), "depth": len(within), "top": top,
                                "text": [], "imgs": []})
            index = len(self.blocks) - 1
        if tag == "img":
            for holder in within + ([index] if index is not None else []):
                self.blocks[holder]["imgs"].append(attributes)
        if not closes and tag not in _VOID:
            self._open_tags.append(index)

    def handle_starttag(self, tag, attrs):  # noqa: D102
        self._open(tag, attrs, closes=False)

    def handle_startendtag(self, tag, attrs):  # noqa: D102
        self._open(tag, attrs, closes=True)

    def handle_endtag(self, tag):  # noqa: D102
        if tag not in _VOID and self._open_tags:
            self._open_tags.pop()

    def handle_data(self, data):  # noqa: D102
        for holder in self._within():
            self.blocks[holder]["text"].append(data)


def _blocks(content: str) -> list[dict[str, Any]]:
    parser = _Blocks()
    parser.feed(content)
    blocks = []
    for block in parser.blocks:
        text = "".join(block["text"]).strip()
        picture = block["imgs"][0] if len(block["imgs"]) == 1 and not text else None
        blocks.append({"tag": block["tag"], "id": block["id"], "depth": block["depth"], "top": block["top"],
                       "text": text, "picture": picture})
    return blocks


def resume_pictures(
    runner: Runner, bin_path: str, doc_id: str, written: str, first: dict[str, Any], known: List[dict[str, Any]], *,
    task_id: Optional[str], artifact_root: Optional[Path], save: Optional[Callable[[], None]] = None,
) -> tuple[List[dict[str, Any]], List[str], List[dict[str, Any]], List[dict[str, Any]], str]:
    """Finish the pictures of a document an export made and was cut off in.

    ``first`` is what that export wrote (``first_push``); ``known`` what the
    task knows its pictures became: those of the chapters this resume
    rewrote (``_update``) and, for a record that tracks its pictures, the
    ones it records as gone in (``recorded_pictures``, passed through
    ``_update``). They are in already, and no picture of theirs is taken for
    another. ``written`` is the note as it is now (``_feishu_chunk``), for
    the counts. ``save`` writes ``first`` back to its record: this resume's
    pictures are recorded as they go in, the same way.

    Every paragraph is read, those inside a callout too. A paragraph led by
    a marker is the caption of the picture that marker was made for. A
    marker no picture was made for, or of a picture the record says is in,
    is taken out and its caption left as words. Otherwise the picture goes
    in at the marker now (``_insert_images``, which takes the marker out) or,
    when its file is gone since, the marker is taken out and its caption
    left as words; unless its picture is in already: lark-cli puts a picture
    right above the top-level block holding its marker (above a callout,
    not inside it), so the picture nearest above that place is this one's
    when it carries this file name, or no name at all, and is no picture
    already known. With a tracking record that is only possible for the
    picture that was going in when the export stopped (``inserting``), and
    only when more pictures are there than the note put there itself
    (``above``): a picture the user pasted there, or the note's own, is
    never taken for a screenshot.

    A caption whose marker is already gone is read from the record when it
    tracks its pictures: a picture it does not record as in is not in.
    With an older record, a caption paragraph at the top level is that
    export's picture only when it reads exactly as that export numbered it
    and a picture not known yet is right above it. Every picture of the note
    that ends up in none of these is listed as not in Feishu.

    Returns what each screenshot became in Feishu, the ones that are not in,
    the note's screenshots (to-upload and not-found, a picture already in
    Feishu counted as uploaded even if its file is gone since) and the
    document as fetched (for its placeholders).

    Checked on a document made for the test in the owner's Feishu
    (2026-11-25): a picture inserted just before the cut-off sits right above
    its marker paragraph. Where lark-cli puts a picture for a marker inside a
    callout is from its ``--help`` (1.0.70), not checked in Feishu."""
    fetched = lark_cli_run._run(
        runner, [bin_path, "docs", "+fetch", "--doc", doc_id, "--detail", "with-ids", "--as", "user"], timeout=60,
    )
    document = fetched.get("document") if isinstance(fetched.get("document"), dict) else fetched
    content = str(document.get("content") or "")
    blocks = _blocks(content)
    tops = [block for block in blocks if block["depth"] == 0]
    _markdown, pending, missing = _prepare_images(written, task_id=task_id, artifact_root=artifact_root)
    push = first.get("push")
    figures = {int(picture["figure"]): picture for picture in first.get("pictures") or []}
    progress = note_progress(first, save or (lambda: None)) if tracked(first) else None
    recorded = set(first.get("inserted") or {})
    inserting = first.get("inserting")
    taken = {image.get(key) for image in known for key in ("block_id", "file_token") if image.get(key)}
    done: set[int] = set()
    found: List[dict[str, Any]] = []
    insert: List[dict[str, Any]] = []
    stray: List[str] = []

    def free(block: dict[str, Any]) -> bool:
        picture = block["picture"]
        return not ({block["id"], picture.get("id"), picture.get("src"), picture.get("token")} - {None}) & taken

    def pictures_above(block: dict[str, Any]) -> List[dict[str, Any]]:
        """The pictures right above where lark-cli puts one for ``block``, nearest first."""
        stack = []
        for above in reversed(tops[:block["top"]]):
            if above["picture"] is None:
                break
            stack.append(above)
        return stack

    for block in blocks:
        text = block["text"]
        marker = _MARKER.match(text) if block["tag"] == "p" else None
        if marker:
            figure = int(marker.group(2))
            picture = figures.get(figure) if push in (None, marker.group(1)) and figure not in done else None
            if picture is None or str(figure) in recorded:
                stray.append(marker.group(0))
                continue
        else:
            if progress is not None or block["depth"] or block["tag"] != "p" or not _FIGURE_NUMBER.match(text):
                continue
            figure = next((n for n, p in figures.items() if n not in done and p["caption"] == text), -1)
            if figure < 0:
                continue
            picture = figures[figure]
        record = {key: picture[key] for key in ("src", "line", "caption", "name")}
        stack = pictures_above(block)
        above = stack[0] if stack and free(stack[0]) else None
        # Feishu was seen to name the picture after its file on one document
        # only; one named after another file is not this one.
        if above is not None and above["picture"].get("name") not in (picture["name"], "", None):
            above = None
        if above is not None and progress is not None and (
            figure != inserting or sum(free(b) for b in stack) <= int(picture.get("above") or 0)
        ):
            above = None
        if above is not None:
            done.add(figure)
            became = {"file_token": above["picture"].get("src") or above["picture"].get("token"),
                      "block_id": above["picture"].get("id") or above["id"]}
            taken.update(value for value in became.values() if value)
            found.append({**record, **became})
            if marker:
                if progress is not None:
                    progress({"marker": marker.group(0)}, became)
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
    inserted, _failed = _insert_images(runner, bin_path, doc_id, insert, progress)
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
