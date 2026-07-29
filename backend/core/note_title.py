"""Derive a human-readable document title from generated Markdown (for Lark export)."""

from __future__ import annotations

import re
from typing import Optional

_H1 = re.compile(r"^\s*#\s+(?!#)(.+?)\s*$")
_H2 = re.compile(r"^\s*##\s+(?!#)(.+?)\s*$")

# 内置课程/会议纪要模板中的小节标题，不宜作为整篇文档标题
_TEMPLATE_SECTION_MARKERS = (
    "一句话概览",
    "核心概念",
    "深度逻辑",
    "敲黑板",
    "延伸思考",
    "Next Step",
    "会议摘要",
    "行动项",
)


def _strip_inline_md(s: str) -> str:
    t = s.strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    return t.strip()


def _is_template_section_heading(line: str) -> bool:
    s = line.strip()
    if not s.startswith("#"):
        return False
    body = re.sub(r"^#+\s*", "", s)
    return any(m in body for m in _TEMPLATE_SECTION_MARKERS)


def extract_note_title_from_markdown(markdown: str) -> Optional[str]:
    """Return a title inferred from AI-generated Markdown, or None if unknown."""
    if not markdown or not markdown.strip():
        return None
    lines = markdown.replace("\r\n", "\n").split("\n")

    for line in lines:
        m = _H1.match(line)
        if m:
            t = _strip_inline_md(m.group(1))
            return t or None

    for line in lines:
        s = line.strip()
        if "一句话概览" not in s:
            continue
        for sep in ("：", ":"):
            if sep in s:
                rest = s.split(sep, 1)[1].strip()
                if len(rest) > 1:
                    rest = re.sub(r"^[\s\d\.\)\*]+", "", rest)
                    t = _strip_inline_md(rest)
                    return t or None

    for line in lines:
        m = _H2.match(line)
        if not m:
            continue
        if _is_template_section_heading(line):
            continue
        t = _strip_inline_md(m.group(1))
        if t and len(t) <= 200:
            return t

    return None


def resolve_lark_doc_title(
    markdown: str,
    *,
    filename_stem: str,
    form_title: Optional[str] = None,
) -> str:
    """Prefer AI-derived title from Markdown; then optional form title; then filename stem."""
    got = extract_note_title_from_markdown(markdown)
    if got:
        return got
    ft = (form_title or "").strip()
    if ft:
        return ft
    fs = (filename_stem or "").strip()
    if fs:
        return fs
    return "FluentFlow Export"


__all__ = ["extract_note_title_from_markdown", "resolve_lark_doc_title"]
