"""Export Markdown to Feishu via local ``lark-cli`` (user identity).

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
   --file <name> --selection-with-ellipsis "<caption>" --before --as user`` from
   the screenshot's folder (lark-cli only accepts relative file paths), so the
   picture lands right above its caption.

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
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from backend.core import feishu_note
from backend.core.lark_exporter import _resolve_markdown_artifact_image

logger = logging.getLogger(__name__)

DEFAULT_WIKI_SPACE = "my_library"

# Marker the diagnostics layer keys on; keep it stable.
LARK_CLI_NOT_FOUND = "lark-cli not found"

_RE_MD_IMAGE_LINE = re.compile(r"^\s*!\[(.*?)\]\((.*?)\)\s*$")
_CAPTION_STRIP = re.compile(r"[\\`*_\[\]$~<>#|]")

Runner = Callable[..., subprocess.CompletedProcess]


class LarkCliError(RuntimeError):
    """A lark-cli command failed; ``str()`` carries what the user should read."""


def _resolve_lark_cli_bin(explicit: Optional[str] = None) -> Optional[str]:
    if explicit and explicit.strip():
        p = explicit.strip()
        if os.path.isfile(p):
            return p
        return shutil.which(p)
    env_bin = (os.environ.get("FLUENTFLOW_LARK_CLI_BIN") or "").strip()
    if env_bin:
        if os.path.isfile(env_bin):
            return env_bin
        return shutil.which(env_bin)
    return shutil.which("lark-cli")


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    # Keep update / skill notices out of the JSON we parse.
    env.setdefault("LARKSUITE_CLI_NO_UPDATE_NOTIFIER", "1")
    env.setdefault("LARKSUITE_CLI_NO_SKILLS_NOTIFIER", "1")
    return env


def _first_json_object(text: str) -> Optional[dict[str, Any]]:
    """The first JSON object in ``text``, skipping progress lines before it."""
    raw = text or ""
    decoder = json.JSONDecoder()
    start = raw.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(raw[start:])
        except ValueError:
            start = raw.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = raw.find("{", start + 1)
    return None


def _tail(text: str, limit: int = 400) -> str:
    cleaned = (text or "").strip()
    return cleaned if len(cleaned) <= limit else "…" + cleaned[-limit:]


def parse_lark_cli_output(proc: subprocess.CompletedProcess) -> dict[str, Any]:
    """Return the success envelope's ``data``, or raise :class:`LarkCliError`.

    lark-cli writes the success envelope to stdout and the error envelope to
    stderr, and may print progress lines before either. Success is decided by
    the envelope's own ``ok`` (or, without one, the exit code) — never by
    whether this parser coped with the text around it.
    """
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    payload = _first_json_object(stdout)
    if payload is None or (payload.get("ok") is not True and proc.returncode != 0):
        payload = _first_json_object(stderr) or payload
    if isinstance(payload, dict) and payload.get("ok") is True:
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise LarkCliError(_describe_cli_error(payload.get("error")))
    raw_tail = _tail(stderr or stdout)
    if proc.returncode == 0:
        raise LarkCliError(
            "lark-cli 执行完成，但返回内容里没有可读的结果"
            f"（输出末尾：{raw_tail or '空'}）"
        )
    raise LarkCliError(f"lark-cli 失败（退出码 {proc.returncode}）：{raw_tail or '没有输出'}")


def _describe_cli_error(error: Any) -> str:
    """One line that keeps lark-cli's own words plus the fields diagnostics need."""
    if not isinstance(error, dict):
        return f"lark-cli 失败：{error}"
    message = str(error.get("message") or "").strip()
    tags: List[str] = []
    for key in ("type", "subtype", "code"):
        value = error.get(key)
        if value not in (None, ""):
            tags.append(f"{key}={value}")
    scopes = error.get("missing_scopes")
    if isinstance(scopes, list) and scopes:
        tags.append("missing_scopes=" + ",".join(str(s) for s in scopes))
    hint = str(error.get("hint") or "").strip()
    text = f"lark-cli 失败 [{' '.join(tags)}]：{message or '没有说明'}"
    if hint:
        text += f"（hint: {hint}）"
    return text


def _run(
    runner: Runner,
    cmd: List[str],
    *,
    timeout: int,
    input_text: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> dict[str, Any]:
    try:
        proc = runner(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_cli_env(),
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise LarkCliError(f"lark-cli 超过 {timeout} 秒没有返回（{' '.join(cmd[1:3])}）") from exc
    except FileNotFoundError as exc:
        raise LarkCliError(f"{LARK_CLI_NOT_FOUND}: {exc}") from exc
    return parse_lark_cli_output(proc)


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
) -> tuple[str, List[dict[str, Any]], List[str]]:
    """Swap each local screenshot for a unique caption paragraph.

    Returns the markdown to create, the screenshots to insert afterwards, and
    the local screenshots whose file could not be found (named in the doc as
    text, counted as failed uploads).
    """
    out: List[str] = []
    pending: List[dict[str, Any]] = []
    missing: List[str] = []
    in_code = False
    figure = first_figure
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
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
        out.extend(["", caption if path else f"{caption}（截图没有传上去）", ""])
        if path:
            pending.append({"caption": caption, "path": path, "src": src})
        else:
            missing.append(src or caption)
    return "\n".join(out), pending, missing


def lark_cli_ready(lark_cli_bin: Optional[str] = None, *, runner: Runner = subprocess.run) -> bool:
    """True when lark-cli is installed and has a usable user login."""
    bin_path = _resolve_lark_cli_bin(lark_cli_bin)
    if not bin_path:
        return False
    try:
        proc = runner(
            [bin_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_cli_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    # ``auth status`` prints its report without the ok/data envelope.
    status = _first_json_object(proc.stdout or "") or {}
    if isinstance(status.get("data"), dict):
        status = status["data"]
    identities = status.get("identities") if isinstance(status.get("identities"), dict) else {}
    user = identities.get("user") if isinstance(identities.get("user"), dict) else {}
    return bool(user.get("available"))


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
) -> Dict[str, Any]:
    """Put the note in Feishu as the signed-in user, with its screenshots.

    A note with no document yet gets a new one. A note whose document an
    earlier export recorded (``previous``, the ``sync`` it returned) is updated
    in that document, chapter by chapter (``_update``); ``keep_feishu`` gets the
    document as markdown before anything in it is replaced."""
    bin_path = _resolve_lark_cli_bin(lark_cli_bin)
    if not bin_path:
        raise LarkCliError(
            f"{LARK_CLI_NOT_FOUND}. Install @larksuite/cli globally or set FLUENTFLOW_LARK_CLI_BIN."
        )
    if previous and previous.get("doc_id") and isinstance(previous.get("sections"), list):
        return _update(
            markdown, previous, bin_path=bin_path, timeout=timeout, task_id=task_id,
            artifact_root=artifact_root, keep_feishu=keep_feishu, runner=runner,
        )

    space = (wiki_space or os.environ.get("FLUENTFLOW_LARK_CLI_WIKI_SPACE") or "").strip() or DEFAULT_WIKI_SPACE
    prepared = feishu_note.to_feishu(markdown)
    export_markdown, pending, missing = _prepare_images(
        prepared.markdown,
        task_id=task_id,
        artifact_root=artifact_root,
    )

    logger.info(
        "lark-cli export: parent=%s title_len=%d md_len=%d images=%d missing=%d",
        space, len(title), len(export_markdown), len(pending), len(missing),
    )
    data = _run(
        runner,
        [
            bin_path, "docs", "+create",
            "--doc-format", "markdown",
            "--title", title,
            "--content", "-",
            "--parent-position", space,
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

    images, errors = _insert_images(runner, bin_path, doc_id, pending)
    sync = {
        "doc_id": doc_id or None,
        "url": doc_url,
        "revision_id": document.get("revision_id"),
        "note_digest": note_digest(markdown),
        "images": images,
        "headings": {},
        "sections": [],
        "link_errors": [],
    }
    if doc_id:
        _finish(runner, bin_path, doc_id, doc_url, prepared.links, feishu_note.sections(markdown), sync)
    return _response(sync, pending, missing, images, errors, wiki_space=space)


def note_digest(markdown: str) -> str:
    return hashlib.sha256((markdown or "").encode("utf-8")).hexdigest()


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
        "image_upload_count": len(images),
        "image_upload_errors": list(missing) + errors,
        "sync": sync,
    }


def _insert_images(
    runner: Runner, bin_path: str, doc_id: str, pending: List[dict[str, Any]],
) -> tuple[List[dict[str, Any]], List[str]]:
    """Each screenshot right above its caption; what each became in Feishu,
    and the ones that did not make it."""
    images: List[dict[str, Any]] = []
    errors: List[str] = []
    for item in pending:
        if not doc_id:
            errors.append(item["src"])
            continue
        path: Path = item["path"]
        try:
            inserted = _run(
                runner,
                [
                    bin_path, "docs", "+media-insert",
                    "--doc", doc_id,
                    "--file", path.name,
                    "--selection-with-ellipsis", item["caption"],
                    "--before",
                    "--as", "user",
                ],
                timeout=90,
                cwd=path.parent,
            )
            images.append({
                "src": item["src"],
                "name": path.name,
                "file_token": inserted.get("file_token"),
                "block_id": inserted.get("block_id"),
            })
        except LarkCliError as exc:
            logger.warning("lark-cli image insert skipped for %s: %s", path.name, exc)
            errors.append(item["src"])
    return images, errors


def _local_images_in(text: str) -> int:
    count = 0
    for line in text.split("\n"):
        match = _RE_MD_IMAGE_LINE.match(line)
        if match and urlparse((match.group(2) or "").strip()).scheme.lower() not in ("http", "https"):
            count += 1
    return count


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
    fetched = _run(
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
        as_markdown = _run(
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
    missing: List[str] = []
    deleted: set[str] = set()
    rewritten = 0
    for op in ops:
        if isinstance(op, feishu_note.Delete):
            for start in range(0, len(op.block_ids), 50):
                chunk = op.block_ids[start:start + 50]
                _run(
                    runner,
                    [bin_path, "docs", "+update", "--doc", doc_id, "--command", "block_delete",
                     "--block-id", ",".join(chunk), "--as", "user"],
                    timeout=timeout,
                )
                deleted.update(chunk)
            continue
        source = "\n\n".join(parts[j].body if op.body_only else parts[j].text for j in op.parts)
        prepared = feishu_note.to_feishu(source, first_link=len(links))
        links.extend(prepared.links)
        content, wanted, gone = _prepare_images(
            prepared.markdown, task_id=task_id, artifact_root=artifact_root, first_figure=figures[op.parts[0]],
        )
        pending.extend(wanted)
        missing.extend(gone)
        _run(
            runner,
            [bin_path, "docs", "+update", "--doc", doc_id, "--command", "block_insert_after",
             "--block-id", op.anchor, "--doc-format", "markdown", "--content", "-", "--as", "user"],
            timeout=timeout,
            input_text=content,
        )
        rewritten += len(op.parts)

    images, errors = _insert_images(runner, bin_path, doc_id, pending)
    sync["images"] = [
        image for image in previous.get("images") or [] if image.get("block_id") not in deleted
    ] + images
    _finish(runner, bin_path, doc_id, doc_url, links, parts, sync)
    return {**_response(sync, pending, missing, images, errors), "updated": True, "sections_rewritten": rewritten}


def _finish(
    runner: Runner,
    bin_path: str,
    doc_id: str,
    doc_url: str,
    links: List[feishu_note.HeadingLink],
    parts: List[feishu_note.Section],
    sync: dict[str, Any],
) -> None:
    """After the writes: note each chapter's heading block, point each link to
    a heading at that heading, and note the revision, all on ``sync``. A link
    that cannot be fixed keeps its words and is listed; the document stands
    either way."""
    try:
        outline = _run(
            runner,
            [bin_path, "docs", "+fetch", "--doc", doc_id, "--scope", "outline",
             "--detail", "with-ids", "--as", "user"],
            timeout=60,
        )
    except LarkCliError as exc:
        logger.warning("lark-cli outline fetch failed for %s: %s", doc_id, exc)
        sync["link_errors"] = [link.label for link in links]
        # Without the headings' ids no chapter can be matched next time: the
        # next export updates nothing in place and writes a new document.
        sync["sections"] = None
        return
    fetched = outline.get("document") if isinstance(outline.get("document"), dict) else outline
    content = str(fetched.get("content") or "")
    sync["headings"] = feishu_note.headings(content)
    sync["sections"] = feishu_note.section_records(parts, feishu_note.heading_list(content))
    if fetched.get("revision_id") is not None:
        sync["revision_id"] = fetched.get("revision_id")
    base = doc_url.split("#", 1)[0]
    for link in links:
        block_id = sync["headings"].get(feishu_note.heading_key(link.heading))
        target = f"[{link.label}]({base}#{block_id})" if block_id else link.label
        try:
            updated = _run(
                runner,
                [bin_path, "docs", "+update", "--doc", doc_id, "--command", "str_replace",
                 "--doc-format", "markdown", "--pattern", f"[{link.label}]({link.placeholder})",
                 "--content", target, "--as", "user"],
                timeout=60,
            )
        except LarkCliError as exc:
            logger.warning("lark-cli could not link %s in %s: %s", link.label, doc_id, exc)
            sync["link_errors"].append(link.label)
            continue
        if not block_id or updated.get("result") not in (None, "success"):
            sync["link_errors"].append(link.label)
        revision = (updated.get("document") or {}).get("revision_id") if isinstance(updated.get("document"), dict) else None
        if revision is not None:
            sync["revision_id"] = revision


__all__ = [
    "DEFAULT_WIKI_SPACE",
    "LARK_CLI_NOT_FOUND",
    "LarkCliError",
    "export_markdown_via_lark_cli",
    "lark_cli_ready",
    "note_digest",
    "parse_lark_cli_output",
]
