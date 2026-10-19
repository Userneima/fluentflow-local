"""A task's note as a PDF file, for an agent to hand to the user.

The editor already prints a note to PDF through the browser's print dialog;
an agent has no dialog to click, so this writes the file itself. The note's
screenshots are served from this app's artifact URLs, which a file on disk
cannot load, so each is pointed at the frame file on this machine first.

The page is rendered by a Chromium-based browser already on the machine
(Chrome or Edge; every Windows has Edge) in headless mode. Without one, the
printable HTML is still written and the answer says so, so the agent can open
it in whatever browser there is.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import markdown

from backend.core import visual_note_job as vn

_IMAGE_REF = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n]+')

_CSS = """@page{size:A4;margin:18mm 18mm 20mm}
body{font:11pt/1.75 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;color:#1a1a1a}
h1{font-size:17pt;margin:0 0 10pt}h2{font-size:14pt;margin:18pt 0 6pt}h3{font-size:12pt;margin:14pt 0 4pt}
img{max-width:100%;max-height:120mm;display:block;margin:6pt 0}
blockquote{margin:6pt 0;padding:2pt 10pt;border-left:3px solid #ccc;color:#444}
table{border-collapse:collapse;margin:6pt 0}td,th{border:1px solid #ccc;padding:3pt 6pt;vertical-align:top}
code{font-size:10pt;background:#f3f3f3;padding:0 2pt}hr{border:0;border-top:1px solid #ddd}"""


class NotePdfError(RuntimeError):
    pass


def default_output_dir() -> Path:
    return Path.home() / "Documents" / "FluentFlow 笔记"


def _browser() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    if sys.platform.startswith("win"):
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates += [
                    os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s")
# Obsidian's callout, 「> [!question] 标题」: the reader's own questions and the
# answers added later. Python-Markdown would print 「[!question]」 as text.
_CALLOUT = re.compile(r"^(\s*>\s*)\[!(\w+)\][+-]?\s*(.*)$")
_CALLOUT_LABELS = {"question": "问题", "note": "补充", "info": "补充", "tip": "提示",
                   "quote": "引用", "warning": "注意", "summary": "小结"}


def _markdown_for_print(text: str) -> str:
    """The note as Python-Markdown needs it to keep lists as lists.

    Notes are written for renderers that start a list right under a sentence and
    nest with two spaces; Python-Markdown needs a blank line before the list and
    four spaces per level, otherwise the items run on as one paragraph. Nothing
    changes inside code blocks.
    """
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        callout = _CALLOUT.match(line)
        if callout:
            prefix, kind, heading = callout.groups()
            label = _CALLOUT_LABELS.get(kind.lower(), kind)
            heading = "" if heading.strip() == label else heading
            # Two trailing spaces: the body under the heading starts a new line.
            line = f"{prefix}**{label}**" + (f"　{heading}" if heading else "") + "  "
        item = _LIST_ITEM.match(line)
        if item:
            indent = item.group(1).replace("\t", "    ")
            line = " " * (len(indent) * 2) + line.lstrip()
            previous = out[-1] if out else ""
            if previous.strip() and not _LIST_ITEM.match(previous) and not previous.startswith(" "):
                out.append("")
        out.append(line)
    return "\n".join(out)


def printable_html(task_id: str, note_markdown: str, title: str) -> str:
    """The note as a standalone page whose screenshots load from this machine.

    Every image reference that resolves to one of the task's screenshots is
    pointed at the file. Matching only the frame note's ``note_NNNN.jpg`` names
    left a text note's screenshots (named after the source or the timestamp) as
    server URLs that a file page cannot load, so they printed as blanks."""
    from backend.core.output_folder import frame_file

    def local(match: re.Match[str]) -> str:
        src = match.group(2)
        path = frame_file(task_id, src)
        if path is None:
            fallback = re.search(r"(note_\d+\.jpg)", src)
            candidate = Path(str(vn._frame_artifact_path(task_id, fallback.group(1)))) if fallback else None
            path = candidate if candidate is not None and candidate.is_file() else None
        return f"{match.group(1)}{path.as_uri()}{match.group(3)}" if path is not None else match.group(0)

    body = markdown.markdown(
        _markdown_for_print(_IMAGE_REF.sub(local, note_markdown)), extensions=["tables", "fenced_code", "sane_lists"]
    )
    return (
        f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )


def export_pdf(
    task_id: str, *, client_id: str | None = None, output_dir: str | None = None, filename: str | None = None,
) -> dict[str, Any]:
    """``filename`` (without extension) names the file; otherwise the note's title does."""
    job = vn.get_job(task_id, client_id=client_id)
    if not job:
        raise NotePdfError("任务不存在")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    note = str(result.get("summary_markdown") or "").strip()
    if not note:
        raise NotePdfError("这个任务还没有笔记")
    # The note's own title names the file: for a link the task title is often the
    # name of a subtitle file or a video id.
    heading = next((line.lstrip("#").strip() for line in note.splitlines() if line.startswith("# ")), "")
    title = heading or str(result.get("display_title") or result.get("filename") or task_id).strip()
    directory = Path(output_dir).expanduser() if output_dir else default_output_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = _UNSAFE_NAME.sub("_", filename or title)[:120 if filename else 80].strip() or task_id
    html_path = directory / f"{stem}.html"
    html_path.write_text(printable_html(task_id, note, title), encoding="utf-8")

    browser = _browser()
    if not browser:
        return {
            "task_id": task_id, "pdf_path": None, "html_path": str(html_path),
            "reason": "这台电脑上没找到 Chrome 或 Edge，生成不了 PDF。已写好可打印的网页，用浏览器打开后打印成 PDF 即可。",
        }
    pdf_path = directory / f"{stem}.pdf"
    with tempfile.TemporaryDirectory(prefix="fluentflow-pdf-") as profile:
        run = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={profile}", "--allow-file-access-from-files",
             f"--print-to-pdf={pdf_path}", html_path.as_uri()],
            capture_output=True, text=True, timeout=180,
        )
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise NotePdfError(f"浏览器没能生成 PDF（退出码 {run.returncode}）。可打印的网页在 {html_path}")
    html_path.unlink(missing_ok=True)
    return {"task_id": task_id, "pdf_path": str(pdf_path), "html_path": None}


__all__ = ["NotePdfError", "default_output_dir", "export_pdf", "printable_html"]
