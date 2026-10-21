"""The note as the Feishu copy needs it, and what that copy holds afterwards.

Requirement (owner, 2026-10-21): a note kept in Feishu as well reads there as
it does in Obsidian, and can come back to the md. Checked against a real note
(the Luo Fuli interview) with lark-cli 1.0.70: headings, lists, bold, quotes
and tables come through lark-cli's markdown import and export as they are.
What does not, and what this module does about it:

- An Obsidian callout (``> [!question] 标题``) arrives as a plain quote with
  「[!question]」 showing. It becomes a Feishu callout (高亮块) whose emoji says
  which kind it was, so the way back can tell. A Feishu callout cannot hold a
  table or a code block: from the first such line on, the rest of the callout
  is written after it, outside.
- A link to a heading of the note (``[[#五、标题|第五章]]``) is dead text in
  Feishu. It is written as a link to a placeholder address unique to that link;
  once the document exists and its headings have block ids, each placeholder is
  replaced by the document's own link to that heading (``<doc url>#<block id>``).
- The part markers a long note carries (``<!-- 第 1/3 部分 -->``) are dropped;
  Feishu would drop them anyway, and nothing reads them back.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# kind -> (emoji, background, border). The emoji is what the way back reads.
CALLOUT_STYLES: dict[str, tuple[str, str, str]] = {
    "question": ("❓", "light-yellow", "yellow"),
    "tip": ("💡", "light-green", "green"),
    "note": ("📝", "light-blue", "blue"),
    "info": ("📝", "light-blue", "blue"),
    "warning": ("❗", "light-red", "red"),
    "summary": ("📌", "light-purple", "purple"),
    "todo": ("✅", "light-green", "green"),
    "quote": ("💬", "gray", "gray"),
}
DEFAULT_CALLOUT_STYLE = ("💡", "gray", "gray")

PLACEHOLDER_PREFIX = "https://fluentflow.invalid/link/"

_FENCE = re.compile(r"^\s*```")
_CALLOUT_START = re.compile(r"^(\s*)>\s*\[!(\w+)\][+-]?\s*(.*)$")
_HEADING_LINK = re.compile(r"\[\[#([^\]|]+)(?:\|([^\]]+))?\]\]")
_PART_MARKER = re.compile(r"^\s*<!--.*-->\s*$")
_LABEL_STRIP = re.compile(r"[\\`*_\[\]$~<>|]")


@dataclass
class HeadingLink:
    placeholder: str
    label: str
    heading: str


@dataclass
class FeishuMarkdown:
    markdown: str
    links: list[HeadingLink] = field(default_factory=list)


def _quoted_body(line: str, indent: str) -> str | None:
    """The text of a callout line under ``indent``, or None when the callout ended."""
    if not line.startswith(indent):
        return None
    rest = line[len(indent):]
    if not rest.startswith(">"):
        return None
    rest = rest[1:]
    return rest[1:] if rest.startswith(" ") else rest


def _breaks_out(body: str) -> bool:
    stripped = body.lstrip()
    return stripped.startswith("|") or stripped.startswith("```")


def _callout(lines: list[str], start: int) -> tuple[list[str], int]:
    """The callout starting at ``lines[start]`` as Feishu markup, and the index after it."""
    match = _CALLOUT_START.match(lines[start])
    assert match is not None
    indent, kind, title = match.group(1), match.group(2).lower(), match.group(3).strip()
    emoji, background, border = CALLOUT_STYLES.get(kind, DEFAULT_CALLOUT_STYLE)
    inside: list[str] = [title] if title else []
    outside: list[str] = []
    index = start + 1
    while index < len(lines):
        body = _quoted_body(lines[index], indent)
        if body is None:
            break
        if outside or _breaks_out(body):
            outside.append(body)
        else:
            inside.append(body)
        index += 1
    while inside and not inside[-1].strip():
        inside.pop()
    out = [f'{indent}<callout emoji="{emoji}" background-color="{background}" border-color="{border}">']
    out.extend(f"{indent}{line}" if line.strip() else "" for line in inside)
    out.append(f"{indent}</callout>")
    if outside:
        out.append("")
        out.extend(f"{indent}{line}" if line.strip() else "" for line in outside)
    return out, index


def to_feishu(markdown: str) -> FeishuMarkdown:
    """``markdown`` (the note as the md holds it) as written to Feishu."""
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    links: list[HeadingLink] = []

    def heading_link(match: re.Match[str]) -> str:
        heading = match.group(1).strip()
        label = _LABEL_STRIP.sub("", (match.group(2) or heading)).strip() or heading
        placeholder = f"{PLACEHOLDER_PREFIX}{len(links) + 1}"
        links.append(HeadingLink(placeholder=placeholder, label=label, heading=heading))
        return f"[{label}]({placeholder})"

    fenced = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            fenced = not fenced
        if fenced or _FENCE.match(line):
            out.append(line)
            index += 1
            continue
        if _PART_MARKER.match(line):
            index += 1
            # The blank line that followed the marker goes with it.
            if index < len(lines) and not lines[index].strip() and (not out or not out[-1].strip()):
                index += 1
            continue
        if _CALLOUT_START.match(line):
            block, index = _callout(lines, index)
            out.extend(_HEADING_LINK.sub(heading_link, b) for b in block)
            continue
        out.append(_HEADING_LINK.sub(heading_link, line))
        index += 1
    return FeishuMarkdown(markdown="\n".join(out), links=links)


# ── reading the document back ───────────────────────────────────────────────

_HEADING_TAG = re.compile(r"<h([1-9])\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</h\1>", re.S)
_TAG = re.compile(r"<[^>]+>")
_HEADING_NOISE = re.compile(r"[\s*_`\\]+")


def heading_key(text: str) -> str:
    """A heading's words, for matching the md's heading against Feishu's."""
    return _HEADING_NOISE.sub("", html.unescape(_TAG.sub("", text)))


def headings(document_xml: str) -> dict[str, str]:
    """Heading words -> block id, from a ``docs +fetch --detail with-ids`` XML
    document. The first of two same-worded headings wins, as in Obsidian."""
    found: dict[str, str] = {}
    for _level, block_id, inner in _HEADING_TAG.findall(document_xml or ""):
        found.setdefault(heading_key(inner), block_id)
    return found


__all__ = [
    "CALLOUT_STYLES",
    "FeishuMarkdown",
    "HeadingLink",
    "PLACEHOLDER_PREFIX",
    "heading_key",
    "headings",
    "to_feishu",
]
