"""The note's local screenshots in a Feishu export through lark-cli.

Feishu can only fetch http(s) images from Markdown, so before a write each
local screenshot is swapped for its caption paragraph (``图 N：说明``), led by
a marker unique to that picture in that push (``_prepare_images``). After the
write each picture is inserted right above its marker and the marker removed
(``_insert_images``). Callers look ``_insert_images`` up on this module at
call time, so a test replaces it here.
"""

from __future__ import annotations

import logging
import re
import uuid
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
    count = 0
    for line in text.split("\n"):
        match = _RE_MD_IMAGE_LINE.match(line)
        if match and urlparse((match.group(2) or "").strip()).scheme.lower() not in ("http", "https"):
            count += 1
    return count
