"""Read the text off a frame, locally, so the index can say what is on it.

The index the note-writing agent chooses from used to describe a picture by two
numbers: edge contrast, standing in for "how much is written here", and how far
the picture was from the last one kept. Both are proxies, and one of them has
already been caught misjudging — a slide deck scored 19.5 against a cutoff of 20
and was treated as a recording with nothing on screen.

Measured on real course frames, the text itself separates the same material with
no overlap at all: a shared-document lecture reads 971–1641 characters a frame, a
talking-head meeting reads 0–2. That is not a better proxy, it is the thing the
proxy was approximating, and on this machine it costs nothing — Apple's Vision
framework is part of the OS, needs no model download, and runs at ~520ms on a
dense screenshot and ~18ms on a webcam shot where there is nothing to find.

Three rules, because this is the kind of helper that quietly becomes a censor:

- **It labels, it never filters.** Nothing here decides a frame is not worth
  offering. A diagram, an interface demonstration, a physical object held up to
  the camera all read as zero characters, and deleting them would delete exactly
  the pictures a lecture is made of. Zero characters is reported as zero
  characters.
- **Absence degrades, it never fails.** Vision is macOS-only and the local
  edition is meant to reach Windows. Without it the index falls back to the
  numbers it used before. A note must never depend on OCR being installed.
- **It is offered as a rough read, not as fact.** This misread 如何监督 as
  如們血苔 in testing. The index says so, so the model treats it as a hint about
  what to open rather than as a quotation it can pass on.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

# Chinese first: the corpus is Chinese lectures with English code and interface
# text mixed in, and Vision weights the order it is given.
RECOGNITION_LANGUAGES = ("zh-Hans", "zh-Hant", "en-US")

# A ceiling on the whole index, not on any one row, and a spend limit rather than
# a judgement about which text matters.
#
# Per-row clipping was tried first at 160 characters and was wrong in a way worth
# recording. Measured on the real recordings, a frame's new text averages 460-760
# characters, so 160 was cutting most rows in half — and it was combined with
# ranking the longest lines first, which meant the *short* lines were the ones
# systematically thrown away. Short lines are port numbers, versions, filenames:
# the things the Stage 5 notes actually cited a picture for. A cheap rule aimed at
# interface chrome was quietly deleting the most valuable text in the corpus.
#
# So rows are whole now, and only the total is bounded. Measured against the same
# recordings, whole new text runs 37-61k tokens per lecture — 20 to 33 frames'
# worth of the open budget, which is a real price and the reason a ceiling exists
# at all. When it binds, the longest rows are trimmed first and the prompt says so,
# because a silently shortened index reads like a recording with less on screen.
INDEX_TOTAL_CHARS = 60_000

# How close two lines must be before the second counts as the first one again.
#
# Exact matching leaves a lot behind, because the recogniser garbles the same
# sidebar differently on every frame — 如何监督 came back as 如們m音, 外但血音 and
# 如何血音 across three frames of one lecture. Fuzzy matching removes another
# 19–39% of the index on the real recordings.
#
# The number is not a preference. Measured on real pairs from the corpus:
#
#   must stay separate (sibling headings on one slide's contents page)
#     1.1 不问"怎么做"，问"业界通常怎么做" / 1.2 …问"什么情况下该做"   0.71
#     1.5 用"别人能做到"破解… / 1.6 问完之后，提出自己的判断…          0.20
#     3.1 中断不需要理由… / 3.3 每次修完都问"以后怎么规避"             0.23
#   should merge (one line, misread twice)
#     如何监督你没做过的事… / 如們m音你没做过的事…                      0.88
#     ［12:53］「你看下最新的日志…」 / 「12:531「你着下最新的日志…」      0.64
#
# The two ranges overlap, so no threshold gets both right, and the choice is
# which way to be wrong. 0.85 sits above the worst real sibling with margin and
# catches the milder garbling; the badly garbled 0.64 pair stays in the index as
# redundant text. That is the cheap failure. The expensive one — 0.75, which does
# reach that pair — merges 1.1 with 1.2 and deletes a heading the note needed.
DUPLICATE_LINE_SIMILARITY = 0.85

# Comparisons are bucketed by length so this stays linear-ish rather than
# comparing every line against every line seen: two strings whose lengths differ
# by more than a bucket cannot reach the threshold anyway.
_LENGTH_BUCKET = 10

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class FrameText:
    """What was read off one frame, and whether reading was even possible."""

    lines: list[str]
    available: bool = True

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def char_count(self) -> int:
        return len(self.text)


def _vision():
    """The Vision framework, or ``None`` on a machine that has no such thing."""
    try:  # pragma: no cover - import availability is the branch, not the logic
        import Quartz  # noqa: PLC0415
        import Vision  # noqa: PLC0415
        from Foundation import NSURL  # noqa: PLC0415

        return Vision, Quartz, NSURL
    except ImportError:
        return None


def available() -> bool:
    return _vision() is not None


def unavailable_reason() -> str | None:
    """Why frames cannot be read here, or ``None`` when they can.

    Phrased for a log and a result field rather than for a user to act on: the
    product works without this, so nobody should be sent to install anything.
    """
    if available():
        return None
    return (
        "本机没有可用的文字识别（需要 macOS 的 Vision 框架，"
        "pip install pyobjc-framework-Vision）。截图清单会退回用画面密度描述。"
    )


def read_frame(path: Path) -> FrameText:
    """Every line of text Vision finds on one frame.

    Returns ``available=False`` when there is no OCR on this machine, which the
    caller reports rather than treating as "this frame has no text" — the two
    look identical in a character count and mean opposite things.
    """
    modules = _vision()
    if modules is None:
        return FrameText(lines=[], available=False)
    vision, quartz, nsurl = modules
    # pyobjc binds these at runtime from the framework, so no static checker can
    # see them; on Linux the modules are not installed at all and this branch is
    # unreachable.
    # pylint: disable=no-member
    try:
        source = quartz.CGImageSourceCreateWithURL(nsurl.fileURLWithPath_(str(path)), None)
        if source is None:
            return FrameText(lines=[])
        image = quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
        if image is None:
            return FrameText(lines=[])
        request = vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(list(RECOGNITION_LANGUAGES))
        request.setRecognitionLevel_(0)  # accurate; the fast level loses small code text
        request.setUsesLanguageCorrection_(True)
        handler = vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
        handler.performRequests_error_([request], None)
        lines: list[str] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            text = _WHITESPACE.sub(" ", str(candidates[0].string())).strip()
            if text:
                lines.append(text)
        return FrameText(lines=lines)
    except Exception as exc:  # noqa: BLE001 - a frame that cannot be read is not a failed note
        logger.warning("Text recognition failed for %s: %s", path, exc)
        return FrameText(lines=[])


@dataclass
class SeenLines:
    """Every line the index has already shown, for asking "again?" cheaply."""

    buckets: dict[int, list[str]] = field(default_factory=dict)

    def add(self, key: str) -> None:
        self.buckets.setdefault(len(key) // _LENGTH_BUCKET, []).append(key)

    def contains(self, key: str, *, similarity: float = DUPLICATE_LINE_SIMILARITY) -> bool:
        bucket = len(key) // _LENGTH_BUCKET
        for offset in (-1, 0, 1):
            for prior in self.buckets.get(bucket + offset, ()):
                if prior == key:
                    return True
                matcher = SequenceMatcher(None, key, prior)
                # Two cheap rejections before the real comparison, which is the
                # expensive one and is why this is bucketed at all.
                if matcher.real_quick_ratio() < similarity:
                    continue
                if matcher.quick_ratio() >= similarity and matcher.ratio() >= similarity:
                    return True
        return False


def new_lines(current: FrameText, seen: SeenLines) -> list[str]:
    """The lines on this frame that no earlier frame already showed.

    The reason the index reports this rather than the whole text: on the coding
    class every frame read about a thousand characters, and nearly all of it was
    the same Feishu sidebar. Ranking by character count would have called a page
    of navigation furniture as informative as the slide beside it. What was not
    there a moment ago is the part worth opening a picture for.

    "Already showed" is deliberately fuzzy — see ``DUPLICATE_LINE_SIMILARITY`` for
    the pairs that set the threshold and for which way it is wrong.
    """
    fresh: list[str] = []
    for line in current.lines:
        key = _normalised(line)
        if not key or seen.contains(key):
            continue
        seen.add(key)
        fresh.append(line)
    return fresh


def _normalised(line: str) -> str:
    return _WHITESPACE.sub("", line).lower()


def summarise(lines: list[str]) -> str:
    """This frame's new text, whole, in the order it appears on screen.

    Neither clipped nor reordered. Both were tried: clipping at 160 characters cut
    rows that average 460-760, and ranking longest-first to survive that clip made
    short lines the casualties — which is backwards, because a port number is four
    characters and is exactly what a note cites a picture for. Reading order is
    also the truthful one: on a slide, what comes first is the heading.

    Interface chrome is left in. Removing it would mean deciding which text on a
    screen is worth showing, and the only rule available for that is "it looks
    like navigation", which is a guess about meaning made by a script. What is
    bounded instead is the total, in ``fit_index``.
    """
    return " / ".join(line for line in lines if line.strip()).strip()


def fit_index(texts: list[str], limit: int = INDEX_TOTAL_CHARS) -> tuple[list[str], bool]:
    """The index within its total budget, and whether anything had to give.

    The budget is spend, not taste, so when it binds the longest rows are trimmed
    rather than any row being dropped: every frame keeps a row saying something
    about itself, and the frames that lose text are the ones that had the most to
    spare. Returns the texts and whether trimming happened, because an index that
    was shortened has to say so — a quietly truncated one reads like a recording
    with less on its screens.
    """
    total = sum(len(text) for text in texts)
    if total <= limit or not texts:
        return list(texts), False
    trimmed = list(texts)
    # Trim the current longest repeatedly; equivalently, cap every row at the
    # largest ceiling that fits the budget.
    low, high = 0, max(len(text) for text in trimmed)
    while low < high:
        cap = (low + high + 1) // 2
        if sum(min(len(text), cap) for text in trimmed) <= limit:
            low = cap
        else:
            high = cap - 1
    return [text if len(text) <= low else text[: max(low - 1, 0)] + "…" for text in trimmed], True


__all__ = [
    "DUPLICATE_LINE_SIMILARITY",
    "FrameText",
    "SeenLines",
    "INDEX_TOTAL_CHARS",
    "RECOGNITION_LANGUAGES",
    "available",
    "fit_index",
    "new_lines",
    "read_frame",
    "summarise",
    "unavailable_reason",
]
