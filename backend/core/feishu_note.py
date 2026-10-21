"""The note as the Feishu copy needs it, and what that copy holds afterwards.

Requirement (owner, 2026-10-21): a note kept in Feishu as well reads there as
it does in Obsidian, and can come back to the md. Checked against a real note
(the Luo Fuli interview) with lark-cli 1.0.70: headings, lists, bold, quotes
and tables come through lark-cli's markdown import and export as they are.
What does not, and what this module does about it:

- An Obsidian callout (``> [!question] 标题``) arrives as a plain quote with
  「[!question]」 showing. It becomes a Feishu callout (高亮块) whose emoji says
  which kind it was, so the way back can tell. Its title is a heading inside
  the callout (``####``), set apart from the answer under it the way the owner
  formatted one by hand in Feishu (2026-10-21). A Feishu callout cannot hold a
  table or a code block: from the first such line on, the rest of the callout
  is written after it, outside.
- A link to a heading of the note (``[[#五、标题|第五章]]``) is dead text in
  Feishu. It is written as a link to a placeholder address unique to that link;
  once the document exists and its headings have block ids, each placeholder is
  replaced by the document's own link to that heading (``<doc url>#<block id>``).
- The part markers a long note carries (``<!-- 第 1/3 部分 -->``) are dropped;
  Feishu would drop them anyway, and nothing reads them back.
- A single ``~`` is escaped (Feishu makes ``~x~`` strikethrough) and so is a
  ``<`` that does not open a tag Feishu knows (it drops ``<tag>`` with its text).
- The page is laid out the way the owner's Feishu notes are (feishu-doc-ops,
  formatting-style): a long prose paragraph starts with two full-width spaces;
  an empty paragraph separates body blocks (none under a heading, none
  between list items); a divider ends each chapter before the next ``##``; a
  table gets columns fitted to its text, a header row, and centred cells.
  All of it is undone on the way back, so the md never carries it.

The way back (``from_feishu``) is at the end of this module.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

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
_LONE_TILDE = re.compile(r"(?<![~\\])~(?!~)")
_UNKNOWN_TAG = re.compile(r"(?<!\\)<(?!/?(?:br|b|em|del|u|code|span|a|img|sup|sub|callout)\b)(?=/?[A-Za-z])")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")


def _escape(line: str) -> str:
    """Escape what Feishu's markdown import would otherwise eat, outside inline code."""
    pieces = _INLINE_CODE.split(line)
    out: list[str] = []
    index = 0
    while index < len(pieces):
        out.append(_UNKNOWN_TAG.sub(r"\\<", _LONE_TILDE.sub(r"\\~", pieces[index])))
        if index + 2 < len(pieces):
            out.append(pieces[index + 1] + pieces[index + 2] + pieces[index + 1])
        index += 3
    return "".join(out)


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
    inside: list[str] = [f"#### {title}"] if title else []
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
    out.extend(f"{indent}{_escape(line)}" if line.strip() else "" for line in inside)
    out.append(f"{indent}</callout>")
    if outside:
        out.append("")
        out.extend(f"{indent}{_escape(line)}" if line.strip() else "" for line in outside)
    return out, index


def to_feishu(markdown: str, *, first_link: int = 0, divider_after: bool = False) -> FeishuMarkdown:
    """``markdown`` (the note as the md holds it) as written to Feishu. Link
    placeholders are numbered from ``first_link`` + 1, so pieces of one note
    written in the same push never share one. ``divider_after`` ends the piece
    with the divider that comes before the next chapter."""
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    links: list[HeadingLink] = []

    def heading_link(match: re.Match[str]) -> str:
        heading = match.group(1).strip()
        label = _LABEL_STRIP.sub("", (match.group(2) or heading)).strip() or heading
        placeholder = f"{PLACEHOLDER_PREFIX}{first_link + len(links) + 1}"
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
        out.append(_HEADING_LINK.sub(heading_link, _escape(line)))
        index += 1
    laid = _layout(out)
    if divider_after:
        laid.extend(["", "---"])
    return FeishuMarkdown(markdown="\n".join(laid), links=links)


# ── the page as the owner's Feishu notes look ───────────────────────────────

_LIST_START = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
INDENT = "\u3000\u3000"
EMPTY_PARAGRAPH = "<p></p>"
# Shorter than this is a label or a line of metadata, not a paragraph to indent.
_PROSE_MIN_CHARS = 20


def _block_kind(block: list[str]) -> str:
    first = block[0]
    if _FENCE.match(first):
        return "code"
    if first.lstrip().startswith("<callout"):
        return "callout"
    if first.startswith("#"):
        return "heading"
    if _HR.match(first):
        return "hr"
    if _LIST_START.match(first):
        return "list"
    if first.lstrip().startswith("|") and len(block) > 1 and _TABLE_SEPARATOR.match(block[1]):
        return "table"
    if first.lstrip().startswith((">", "<", "!", "|")) or first.startswith(" "):
        return "other"
    return "prose"


def _blocks(lines: list[str]) -> list[list[str]]:
    """Lines grouped into blocks: blank lines separate them, except inside a code
    block or a callout, which stay whole; a callout indented under a list item
    belongs to that item's block."""
    blocks: list[list[str]] = []
    current: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        enclosing = _FENCE.match(line) or _CALLOUT_OPEN.match(line)
        if enclosing:
            closing = _FENCE if _FENCE.match(line) else _CALLOUT_CLOSE
            group = [line]
            index += 1
            while index < len(lines):
                group.append(lines[index])
                index += 1
                if closing.match(group[-1]):
                    break
            if line.startswith(" ") and current:
                current.extend(group)
            else:
                if current:
                    blocks.append(current)
                    current = []
                blocks.append(group)
            continue
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
        index += 1
    if current:
        blocks.append(current)
    return blocks


def _cells(row: str) -> list[str]:
    text = row.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", text)]


def _units(text: str) -> int:
    plain = re.sub(r"\*\*|`|\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in plain)


def _cell_xml(text: str) -> str:
    text = text.replace("\\|", "|").replace("\\~", "~")
    text = html.escape(text, quote=False).replace("\\&lt;", "&lt;")
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return f'<p align="center">{text}</p>' if text else "<p></p>"


def _table(block: list[str]) -> list[str]:
    """A markdown table as a Feishu table: columns as wide as their text (a
    table whose cells hold sentences fills the body width), a header row, every
    cell centred."""
    header = _cells(block[0])
    rows = [_cells(line) for line in block[2:]]
    columns = len(header)
    rows = [(row + [""] * columns)[:columns] for row in rows]
    widest = [max([_units(header[c])] + [_units(row[c]) for row in rows]) for c in range(columns)]
    pixels = [units * 7 + 28 for units in widest]
    if max(widest, default=0) > 40:
        fixed = [min(p, 160) if w <= 20 else None for p, w in zip(pixels, widest)]
        room = max(740 - sum(p for p in fixed if p), 120)
        loose = [p for p, f in zip(pixels, fixed) if f is None]
        total = sum(loose) or 1
        widths = [f if f else max(120, round(room * p / total)) for p, f in zip(pixels, fixed)]
    else:
        widths = [min(max(p, 60), 275) for p in pixels]
    cols = "".join(f'<col width="{w}"/>' for w in widths)
    head = "".join(f'<th vertical-align="middle">{_cell_xml(cell)}</th>' for cell in header)
    body = "".join(
        "<tr>" + "".join(f'<td vertical-align="middle">{_cell_xml(cell)}</td>' for cell in row) + "</tr>"
        for row in rows
    )
    return [f"<table><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"]


def _layout(lines: list[str]) -> list[str]:
    out: list[str] = []
    previous = ""
    for block in _blocks(lines):
        kind = _block_kind(block)
        if kind == "prose" and len("".join(block).strip()) >= _PROSE_MIN_CHARS and not block[0].startswith("\u3000"):
            block = [INDENT + block[0]] + block[1:]
        elif kind == "table":
            block = _table(block)
        if out:
            spaced = (previous not in ("heading", "hr") and kind not in ("heading", "hr")
                      and not (previous == "list" and kind == "list"))
            out.extend(["", EMPTY_PARAGRAPH, ""] if spaced else [""])
        out.extend(block)
        previous = kind
    return out


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


def heading_list(document_xml: str) -> list[tuple[str, str]]:
    """(heading words, block id) for each heading, in document order."""
    return [(heading_key(inner), block_id) for _level, block_id, inner in _HEADING_TAG.findall(document_xml or "")]


# ── updating the document chapter by chapter ────────────────────────────────
#
# Owner, 2026-10-21: a note already in Feishu is updated by chapter. Only the
# chapters that changed since the last push are rewritten; the others stay as
# they are, with their screenshots and any comments made on them in Feishu.
# A chapter whose heading stayed the same keeps its heading block, so links to
# it from elsewhere in the document keep working.

_HEADING_LINE = re.compile(r"^(#{1,9})\s+(.+?)\s*$")


@dataclass
class Section:
    """A heading and everything under it up to the next heading (any level).
    The part before the first heading has level 0 and no heading."""
    level: int
    heading: str
    body: str

    @property
    def key(self) -> str:
        return heading_key(self.heading) if self.level else ""

    @property
    def digest(self) -> str:
        return hashlib.sha256(f"{self.heading}\n{self.body}".encode("utf-8")).hexdigest()

    @property
    def text(self) -> str:
        if not self.level:
            return self.body
        line = f"{'#' * self.level} {self.heading}"
        return f"{line}\n\n{self.body}" if self.body.strip() else line


def sections(markdown: str) -> list[Section]:
    found: list[Section] = []
    level, heading, body = 0, "", []
    fenced = False
    boxed = False
    for line in (markdown or "").replace("\r\n", "\n").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        # A heading inside a Feishu callout is the callout's title, not a chapter.
        if not fenced and _CALLOUT_OPEN.match(line):
            boxed = True
        elif boxed and _CALLOUT_CLOSE.match(line):
            boxed = False
        match = None if fenced or boxed else _HEADING_LINE.match(line)
        if match:
            if level or "\n".join(body).strip():
                found.append(Section(level, heading, "\n".join(body).strip("\n")))
            level, heading, body = len(match.group(1)), match.group(2).strip(), []
            continue
        body.append(line)
    if level or "\n".join(body).strip():
        found.append(Section(level, heading, "\n".join(body).strip("\n")))
    return found


def section_records(parts: list[Section], heading_ids: list[tuple[str, str]]) -> list[dict]:
    """What is kept about each chapter after a push: its heading, digest and
    the block id of its heading in the document (matched in order by words)."""
    records: list[dict] = []
    cursor = 0
    for part in parts:
        heading_id = None
        if part.level:
            for index in range(cursor, len(heading_ids)):
                if heading_ids[index][0] == part.key:
                    heading_id = heading_ids[index][1]
                    cursor = index + 1
                    break
        records.append({"key": part.key, "level": part.level, "digest": part.digest, "heading_id": heading_id})
    return records


class _TopBlocks(HTMLParser):
    """The document's top-level blocks, in order, from a with-ids fetch. A list
    (``<ul>``) has no id of its own: its items are the blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._stack: list[bool] = []

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], closes: bool) -> None:
        inside = any(self._stack)
        block_id = dict(attrs).get("id")
        record = bool(block_id) and not inside and tag != "title"
        if record:
            self.blocks.append((tag, str(block_id)))
        if not closes:
            self._stack.append(record)

    def handle_starttag(self, tag, attrs):  # noqa: D102
        self._start(tag, attrs, closes=False)

    def handle_startendtag(self, tag, attrs):  # noqa: D102
        self._start(tag, attrs, closes=True)

    def handle_endtag(self, tag):  # noqa: D102
        if self._stack:
            self._stack.pop()


def top_blocks(document_xml: str) -> list[tuple[str, str]]:
    parser = _TopBlocks()
    parser.feed(document_xml or "")
    return parser.blocks


_HEADING_TAGS = {f"h{n}" for n in range(1, 10)}


@dataclass
class Delete:
    block_ids: list[str]


@dataclass
class Insert:
    """Write ``parts`` (new chapters, or only a chapter's body) after ``anchor``,
    a block id or the document id for the very start."""
    anchor: str
    parts: list[int]
    body_only: bool = False


def plan_update(old: list[dict], new: list[Section], blocks: list[tuple[str, str]], doc_id: str) -> list:
    """The deletes and inserts that turn the document (``blocks``, last pushed
    as ``old``) into ``new``, chapter by chapter, in the order to run them:
    from the end of the document backwards, so every anchor still exists when
    it is used."""
    bodies: dict[str | None, list[str]] = {None: []}
    current: str | None = None
    for tag, block_id in blocks:
        if tag in _HEADING_TAGS:
            current = block_id
            bodies[current] = []
        else:
            bodies[current].append(block_id)

    def heading_of(index: int) -> str | None:
        heading_id = old[index].get("heading_id")
        return heading_id if heading_id in bodies else None

    def body_of(index: int) -> list[str]:
        if not old[index].get("level"):
            return bodies[None]
        heading_id = heading_of(index)
        return bodies[heading_id] if heading_id else []

    def blocks_of(index: int) -> list[str]:
        heading_id = heading_of(index)
        return ([heading_id] if heading_id else []) + body_of(index)

    def anchor_before(index: int) -> str:
        for earlier in range(index - 1, -1, -1):
            found = blocks_of(earlier)
            if found:
                return found[-1]
        return doc_id

    ops: list = []
    matcher = difflib.SequenceMatcher(
        a=[(r.get("key"), r.get("level")) for r in old], b=[(p.key, p.level) for p in new], autojunk=False,
    )
    for tag, i1, i2, j1, j2 in reversed(matcher.get_opcodes()):
        if tag == "equal":
            for i, j in reversed(list(zip(range(i1, i2), range(j1, j2)))):
                if old[i].get("digest") == new[j].digest:
                    continue
                body = body_of(i)
                if body:
                    ops.append(Delete(body))
                anchor = heading_of(i) if old[i].get("level") else doc_id
                if anchor is None:
                    # Its heading is gone from the document: write it whole.
                    ops.append(Insert(anchor_before(i), [j]))
                elif new[j].body.strip():
                    ops.append(Insert(anchor, [j], body_only=True))
            continue
        gone = [block for i in range(i1, i2) for block in blocks_of(i)]
        if gone:
            ops.append(Delete(gone))
        if j2 > j1:
            ops.append(Insert(anchor_before(i1), list(range(j1, j2))))
    return ops


# ── the way back: the Feishu document as the md writes it ───────────────────
#
# Only chapters edited in Feishu come back this way; the others keep the note's
# own text. Checked against lark-cli's markdown export (2026-10-21).

_TITLE_LINE = re.compile(r"^\s*<title>.*</title>\s*$")
_CALLOUT_OPEN = re.compile(r'^(\s*)<callout\b[^>]*?\bemoji="([^"]*)"[^>]*>\s*$')
_CALLOUT_CLOSE = re.compile(r"^\s*</callout>\s*$")
_FEISHU_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\((\S+?)\)\s*$")
_CAPTION = re.compile(r"^图 \d+：(.*?)(（截图没有传上去）)?$")
_UNESCAPE = re.compile(r"\\([\\`*_\[\]$~<>#+\-{}!.()])")
_LIST_LINE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s")
_KIND_BY_EMOJI = {}
for _kind, (_emoji, _bg, _border) in CALLOUT_STYLES.items():
    _KIND_BY_EMOJI.setdefault(_emoji, _kind)


def without_title(feishu_markdown: str) -> str:
    lines = (feishu_markdown or "").replace("\r\n", "\n").split("\n")
    while lines and (not lines[0].strip() or _TITLE_LINE.match(lines[0])):
        lines.pop(0)
    return "\n".join(lines)


def _tidy(text: str) -> str:
    """Trailing spaces off, runs of blank lines down to one."""
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip("\n")


def feishu_section_records(feishu_markdown: str) -> list[dict]:
    """Each chapter of the Feishu document as lark-cli shows it: what a later
    look compares against to tell which chapters were edited in Feishu."""
    return [
        {"key": part.key, "level": part.level, "digest": hashlib.sha256(_tidy(part.text).encode("utf-8")).hexdigest()}
        for part in sections(without_title(feishu_markdown))
    ]


@dataclass
class Way:
    """What the way back needs to know about the document: its id, its
    headings by block id, and the pictures each push put in."""
    doc_id: str
    headings_by_id: dict[str, str]
    images: list[dict]
    fetch_image: object = None  # callable(file_token) -> note image src, or None


def _unescape(line: str) -> str:
    pieces = _INLINE_CODE.split(line)
    out: list[str] = []
    index = 0
    while index < len(pieces):
        out.append(_UNESCAPE.sub(r"\1", pieces[index]))
        if index + 2 < len(pieces):
            out.append(pieces[index + 1] + pieces[index + 2] + pieces[index + 1])
        index += 3
    return "".join(out)


def _next_heading_level(lines: list[str], position: int) -> int:
    for line in lines[position + 1:]:
        if line.strip():
            match = _HEADING_LINE.match(line)
            return len(match.group(1)) if match else 0
    return 0


def from_feishu(feishu_markdown: str, way: Way) -> str:
    """A part of the Feishu document (lark-cli's markdown export) written the
    way the note writes it: callouts back to Obsidian callouts (the emoji says
    which kind), links to a heading of the document back to ``[[#标题|文字]]``,
    pictures FluentFlow put in back to the note's own image line, pictures
    added in Feishu fetched (``way.fetch_image``), Feishu's escapes undone."""
    by_token = {str(image.get("file_token")): image for image in way.images if image.get("file_token")}
    by_caption = {str(image.get("caption")): image for image in way.images if image.get("caption")}
    link = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]*" + re.escape(way.doc_id) + r"[^)\s]*#([A-Za-z0-9_-]+))\)") \
        if way.doc_id else None

    def relink(text: str) -> str:
        if link is None:
            return text

        def swap(match: re.Match[str]) -> str:
            heading = way.headings_by_id.get(match.group(3))
            if not heading:
                return match.group(0)
            label = match.group(1)
            return f"[[#{heading}]]" if heading_key(label) == heading_key(heading) else f"[[#{heading}|{label}]]"

        return link.sub(swap, text)

    lines = without_title(feishu_markdown).split("\n")
    out: list[str] = []
    index = 0
    fenced = False
    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            fenced = not fenced
            out.append(line)
            index += 1
            continue
        if fenced:
            out.append(line)
            index += 1
            continue
        opened = _CALLOUT_OPEN.match(line)
        if opened:
            indent, emoji = opened.group(1), opened.group(2)
            inner: list[str] = []
            index += 1
            while index < len(lines) and not _CALLOUT_CLOSE.match(lines[index]):
                inner.append(lines[index][len(indent):] if lines[index].startswith(indent) else lines[index].lstrip())
                index += 1
            index += 1
            inner = [relink(_unescape(text.rstrip())) for text in inner]
            while inner and not inner[0].strip():
                inner.pop(0)
            kind = _KIND_BY_EMOJI.get(emoji, "note")
            title = inner.pop(0) if inner else ""
            heading = re.match(r"^\s*#{1,6}\s+(.*)$", title)
            if heading:
                title = heading.group(1)
            elif title.strip().startswith("**"):
                # The bold an earlier push put on the title, links in it included.
                title = title.replace("**", "")
            out.append(f"{indent}> [!{kind}] {title}".rstrip())
            out.extend(f"{indent}> {text}" if text.strip() else f"{indent}>" for text in inner)
            continue
        picture = _FEISHU_IMAGE.match(line)
        if picture:
            token = picture.group(2).rstrip("/").rsplit("/", 1)[-1]
            known = by_token.get(token)
            # The caption FluentFlow wrote under it goes with the picture.
            after = index + 1
            while after < len(lines) and not lines[after].strip():
                after += 1
            caption = _CAPTION.match(_unescape(lines[after].strip())) if after < len(lines) else None
            if caption:
                index = after
            if known is not None:
                out.append(str(known.get("line")))
            else:
                src = way.fetch_image(token) if callable(way.fetch_image) else None
                alt = caption.group(1) if caption else picture.group(1)
                out.append(f"![{alt}]({src or picture.group(2)})")
            index += 1
            continue
        stripped = _unescape(line.strip())
        if stripped in by_caption and not by_caption[stripped].get("file_token"):
            # A screenshot that never made it to Feishu: its caption stands in for it.
            out.append(str(by_caption[stripped].get("line")))
            index += 1
            continue
        if line.startswith("\u3000"):
            line = line.lstrip("\u3000")
        out.append(relink(_unescape(line.rstrip())))
        index += 1
    # The divider that ends a chapter is the page's, not the note's.
    while out and (not out[-1].strip() or _HR.match(out[-1])):
        out.pop()
    out = [
        line for position, line in enumerate(out)
        if not (_HR.match(line) and _next_heading_level(out, position) == 2)
    ]
    # Feishu puts a blank line between a list item and the list nested in it.
    kept: list[str] = []
    for position, line in enumerate(out):
        if not line.strip() and kept and position + 1 < len(out):
            before, after = _LIST_LINE.match(kept[-1]), _LIST_LINE.match(out[position + 1])
            if before and after and len(after.group(1)) > len(before.group(1)):
                continue
        kept.append(line)
    return _tidy("\n".join(kept))


__all__ = [
    "CALLOUT_STYLES",
    "FeishuMarkdown",
    "Delete",
    "HeadingLink",
    "Insert",
    "Section",
    "Way",
    "PLACEHOLDER_PREFIX",
    "heading_key",
    "heading_list",
    "feishu_section_records",
    "from_feishu",
    "headings",
    "plan_update",
    "section_records",
    "sections",
    "to_feishu",
    "top_blocks",
    "without_title",
]
