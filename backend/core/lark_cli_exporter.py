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
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from backend.core.feishu_markdown import normalize_markdown_for_feishu
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
    figure = 0
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
    runner: Runner = subprocess.run,
) -> Dict[str, Any]:
    """Create a Feishu doc as the signed-in user and put the note's screenshots in it."""
    bin_path = _resolve_lark_cli_bin(lark_cli_bin)
    if not bin_path:
        raise LarkCliError(
            f"{LARK_CLI_NOT_FOUND}. Install @larksuite/cli globally or set FLUENTFLOW_LARK_CLI_BIN."
        )

    space = (wiki_space or os.environ.get("FLUENTFLOW_LARK_CLI_WIKI_SPACE") or "").strip() or DEFAULT_WIKI_SPACE
    export_markdown, pending, missing = _prepare_images(
        normalize_markdown_for_feishu(markdown),
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

    uploaded = 0
    errors: List[str] = list(missing)
    for item in pending:
        if not doc_id:
            errors.append(item["src"])
            continue
        path: Path = item["path"]
        try:
            _run(
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
            uploaded += 1
        except LarkCliError as exc:
            logger.warning("lark-cli image insert skipped for %s: %s", path.name, exc)
            errors.append(item["src"])

    return {
        "ok": True,
        "url": doc_url,
        "doc_token": doc_id or None,
        "doc_id": doc_id or None,
        "via": "lark_cli",
        "markdown_format": "feishu_normalized",
        "wiki_space": space,
        "image_count": len(pending) + len(missing),
        "image_upload_count": uploaded,
        "image_upload_errors": errors,
    }


__all__ = [
    "DEFAULT_WIKI_SPACE",
    "LARK_CLI_NOT_FOUND",
    "LarkCliError",
    "export_markdown_via_lark_cli",
    "lark_cli_ready",
    "parse_lark_cli_output",
]
