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
   --file <name> --selection-with-ellipsis "<marker>" --before --as user`` from
   the screenshot's folder (lark-cli only accepts relative file paths), so the
   picture lands right above its caption, then remove the marker with a
   ``str_replace``. The marker (``⟦FFIMG-<push>-<n>⟧`` in front of the
   caption) is unique to that picture in that push: captions are numbered over
   the whole note, so a chapter rewritten later can carry the same caption as
   one left alone, and locating by caption put the picture in the wrong place.

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
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from backend.core import feishu_note
from backend.core.lark_exporter import _resolve_markdown_artifact_image

logger = logging.getLogger(__name__)

DEFAULT_WIKI_SPACE = "my_library"

# Marker the diagnostics layer keys on; keep it stable.
LARK_CLI_NOT_FOUND = "lark-cli not found"

NOT_UPLOADED = "（截图没有传上去）"
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
        record = {"src": src, "line": line.strip(), "caption": caption if path else f"{caption}{NOT_UPLOADED}"}
        if path:
            marker = f"⟦FFIMG-{push}-{figure}⟧"
            out.extend(["", marker + caption, ""])
            pending.append({**record, "path": path, "marker": marker})
        else:
            out.extend(["", record["caption"], ""])
            missing.append(record)
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
    if previous and previous.get("doc_id") and sections_unread(previous):
        previous = reread_sections(previous, task_id=task_id, artifact_root=artifact_root,
                                   bin_path=bin_path, runner=runner)
    if previous and previous.get("doc_id") and isinstance(previous.get("sections"), list):
        return _update(
            markdown, previous, bin_path=bin_path, timeout=timeout, task_id=task_id,
            artifact_root=artifact_root, keep_feishu=keep_feishu, runner=runner,
        )

    space = (wiki_space or os.environ.get("FLUENTFLOW_LARK_CLI_WIKI_SPACE") or "").strip() or DEFAULT_WIKI_SPACE
    parts = feishu_note.sections(markdown)
    links: List[feishu_note.HeadingLink] = []
    written = _feishu_chunk(parts, list(range(len(parts))), links)
    export_markdown, pending, missing = _prepare_images(
        written,
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
        "images": images + [{**item, "file_token": None, "block_id": None} for item in missing],
        "headings": {},
        "sections": [],
        "link_errors": [],
    }
    if doc_id:
        _finish(runner, bin_path, doc_id, doc_url, links, parts, sync)
        save_pushed_note(artifact_root, task_id, markdown)
    return _response(sync, pending, missing, images, errors, wiki_space=space)


PUSHED_NOTE = "feishu_pushed.md"


def pushed_note_path(artifact_root: Optional[Path], task_id: Optional[str]) -> Optional[Path]:
    """The note as last pushed to Feishu: what edits made in Feishu are laid
    over when they come back, so the chapters not edited there keep the note's
    own text."""
    if artifact_root is None or not task_id:
        return None
    return Path(artifact_root) / task_id / PUSHED_NOTE


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
) -> dict[str, Any]:
    """``sync`` with its chapters read again: the note as last pushed, matched
    to the document's headings now. Raises when either cannot be read, so the
    caller writes nothing rather than a second document."""
    path = pushed_note_path(artifact_root, task_id)
    if path is None or not path.is_file():
        raise LarkCliError("飞书那份的章节对不上了：找不到上次推送的笔记。")
    pushed = path.read_text(encoding="utf-8")
    outline = _run(
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


def save_pushed_note(artifact_root: Optional[Path], task_id: Optional[str], markdown: str) -> None:
    path = pushed_note_path(artifact_root, task_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown or "", encoding="utf-8")
    except OSError as exc:
        logger.warning("could not keep the pushed note of %s: %s", task_id, exc)


def _feishu_chunk(
    parts: List[feishu_note.Section], indices: List[int], links: List[feishu_note.HeadingLink], *, body_only: bool = False,
) -> str:
    """The chapters ``indices`` as written to Feishu, links numbered on from
    ``links`` (extended). A chapter followed by a ``##`` chapter other than
    the first one ends with the divider before it, so rewriting either chapter
    later keeps the divider where it was."""
    first_h2 = next((i for i, part in enumerate(parts) if part.level == 2), None)
    pieces: List[str] = []
    for index in indices:
        following = parts[index + 1] if index + 1 < len(parts) else None
        divider = following is not None and following.level == 2 and index + 1 != first_h2
        text = parts[index].body if body_only else parts[index].text
        prepared = feishu_note.to_feishu(text, first_link=len(links), divider_after=divider)
        links.extend(prepared.links)
        if prepared.markdown.strip():
            pieces.append(prepared.markdown.strip("\n"))
    return "\n\n".join(pieces)


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
        "image_upload_count": sum(1 for image in images if image.get("file_token") or image.get("block_id")),
        "image_upload_errors": [item["src"] for item in missing] + errors,
        "sync": sync,
    }


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
            inserted = _run(
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
            _run(
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
    missing: List[dict[str, Any]] = []
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
        written = _feishu_chunk(parts, op.parts, links, body_only=op.body_only)
        if not written.strip():
            # Nothing the page shows (a part marker only): the delete was enough.
            continue
        content, wanted, gone = _prepare_images(
            written, task_id=task_id, artifact_root=artifact_root, first_figure=figures[op.parts[0]],
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
    # A picture whose chapter was rewritten is in the new pictures, if it is
    # still in the note; one never uploaded is listed again by its chapter.
    rewritten_lines = {item["line"] for item in pending + missing}
    sync["images"] = [
        image for image in previous.get("images") or []
        if (image.get("block_id") not in deleted if image.get("block_id") else image.get("line") not in rewritten_lines)
    ] + images + [{**item, "file_token": None, "block_id": None} for item in missing]
    _finish(runner, bin_path, doc_id, doc_url, links, parts, sync)
    save_pushed_note(artifact_root, task_id, markdown)
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
        # The document was written; only its headings' ids are not known. The
        # next export reads them again (``reread_sections``) before writing,
        # rather than the note dropping out of sync (owner, 2026-10-24: a
        # failed read here once left a note unsynced with no error shown).
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
        # A link in a callout's title is bold with the rest of the title, and
        # Feishu shows it as [**label**](...).
        for label in (link.label, f"**{link.label}**"):
            target = f"[{label}]({base}#{block_id})" if block_id else label
            try:
                updated = _run(
                    runner,
                    [bin_path, "docs", "+update", "--doc", doc_id, "--command", "str_replace",
                     "--doc-format", "markdown", "--pattern", f"[{label}]({link.placeholder})",
                     "--content", target, "--as", "user"],
                    timeout=60,
                )
            except LarkCliError as exc:
                logger.warning("lark-cli could not link %s in %s: %s", link.label, doc_id, exc)
                updated = {"result": "failed"}
            if updated.get("result") in (None, "success"):
                break
        if not block_id or updated.get("result") not in (None, "success"):
            sync["link_errors"].append(link.label)
        revision = (updated.get("document") or {}).get("revision_id") if isinstance(updated.get("document"), dict) else None
        if revision is not None:
            sync["revision_id"] = revision
    _snapshot(runner, bin_path, doc_id, sync)


def _snapshot(runner: Runner, bin_path: str, doc_id: str, sync: dict[str, Any]) -> None:
    """What the document looks like now, chapter by chapter, and when Feishu
    last saw it change: an edit made in Feishu later is anything newer than
    this time, in any chapter that no longer looks like this."""
    try:
        shown = _run(
            runner,
            [bin_path, "docs", "+fetch", "--doc", doc_id, "--doc-format", "markdown", "--as", "user"],
            timeout=60,
        )
        document = shown.get("document") if isinstance(shown.get("document"), dict) else shown
        sync["feishu_sections"] = feishu_note.feishu_section_records(str(document.get("content") or ""))
        # ``feishu_sections`` moves on when an edit made in Feishu is pulled
        # in; this stays as the push left it, for telling whether the document
        # was edited in Feishu since (``feishu_sync.keeper``).
        sync["pushed_sections"] = sync["feishu_sections"]
        if document.get("revision_id") is not None:
            sync["revision_id"] = document.get("revision_id")
    except LarkCliError as exc:
        logger.warning("lark-cli could not read %s back after the push: %s", doc_id, exc)
        sync["feishu_sections"] = None
        sync["pushed_sections"] = None
    modified = modified_times(runner, bin_path, [doc_id]).get(doc_id)
    if modified is not None:
        sync["feishu_modified_at"] = modified


def modified_times(runner: Runner, bin_path: str, doc_ids: List[str]) -> Dict[str, int]:
    """When Feishu last saw each document change (seconds), for the ones it
    reports; a document it does not report is left out."""
    times: Dict[str, int] = {}
    for start in range(0, len(doc_ids), 50):
        chunk = doc_ids[start:start + 50]
        body = {"request_docs": [{"doc_token": doc_id, "doc_type": "docx"} for doc_id in chunk]}
        try:
            data = _run(
                runner,
                [bin_path, "drive", "metas", "batch_query", "--data", json.dumps(body), "--as", "user"],
                timeout=60,
            )
        except LarkCliError as exc:
            logger.warning("lark-cli could not read when documents changed: %s", exc)
            continue
        for meta in data.get("metas") or []:
            try:
                times[str(meta.get("doc_token"))] = int(meta.get("latest_modify_time"))
            except (TypeError, ValueError):
                continue
    return times


__all__ = [
    "DEFAULT_WIKI_SPACE",
    "LARK_CLI_NOT_FOUND",
    "LarkCliError",
    "export_markdown_via_lark_cli",
    "lark_cli_ready",
    "modified_times",
    "note_digest",
    "pushed_note_path",
    "parse_lark_cli_output",
]
