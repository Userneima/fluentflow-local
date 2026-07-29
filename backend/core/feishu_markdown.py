"""Feishu/Lark export Markdown normalization.

The app keeps ``summary_markdown`` as ordinary Markdown. Before exporting to
Feishu, convert fragile Markdown features into shapes that Feishu's Markdown
import and the local flat docx block writer both handle predictably.
"""

from __future__ import annotations

import re
from typing import List


_TABLE_ALIGN_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$")


def _split_table_row(line: str) -> List[str]:
    text = (line or "").strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _looks_like_table_row(line: str) -> bool:
    text = (line or "").strip()
    return text.startswith("|") and text.endswith("|") and len(_split_table_row(text)) >= 2


def _looks_like_table(lines: List[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    head = (lines[index] or "").strip()
    align = (lines[index + 1] or "").strip()
    return "|" in head and "|" in align and _TABLE_ALIGN_RE.match(align) is not None


def _compact_cell(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if len(text) >= 4 and text.startswith("**") and text.endswith("**"):
        return text[2:-2].strip()
    return text


def _label(label: str) -> str:
    return _compact_cell(label).rstrip("：:")


def _table_to_list(header: List[str], rows: List[List[str]]) -> List[str]:
    column_count = max([len(header), *[len(row) for row in rows]], default=0)
    if column_count <= 0:
        return []
    labels = [_label(header[idx]) if idx < len(header) else f"列 {idx + 1}" for idx in range(column_count)]
    converted: List[str] = []
    for row in rows:
        cells = [_compact_cell(row[idx]) if idx < len(row) else "" for idx in range(column_count)]
        if not any(cells):
            continue
        first_label = labels[0] or "项目"
        first_value = cells[0] or "-"
        converted.append(f"- **{first_label}**：{first_value}")
        for idx in range(1, column_count):
            value = cells[idx]
            if not value:
                continue
            label = labels[idx] or f"列 {idx + 1}"
            converted.append(f"  - **{label}**：{value}")
    return converted


def normalize_markdown_for_feishu(markdown: str) -> str:
    """Return a Feishu-friendly Markdown copy for export only.

    Feishu Markdown import is not a lossless CommonMark renderer. Pipe tables
    are the most visible mismatch: unsupported routes can display the raw table
    source as ordinary text. Convert tables into scan-friendly labeled lists so
    notes remain readable across OpenAPI and local lark-cli export routes.
    """
    if not markdown:
        return ""

    lines = markdown.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    index = 0
    while index < len(lines):
        if _looks_like_table(lines, index):
            header = _split_table_row(lines[index])
            index += 2
            rows: List[List[str]] = []
            while index < len(lines) and _looks_like_table_row(lines[index]):
                rows.append(_split_table_row(lines[index]))
                index += 1
            converted = _table_to_list(header, rows)
            if converted:
                if out and out[-1].strip():
                    out.append("")
                out.extend(converted)
                if index < len(lines) and lines[index].strip():
                    out.append("")
            continue
        out.append(lines[index])
        index += 1

    return "\n".join(out).strip() + ("\n" if markdown.endswith("\n") else "")


__all__ = ["normalize_markdown_for_feishu"]
