"""Move a transcript from the original recording's clock onto the cut one's.

Removing silence shortens the recording, so every timestamp the transcript
carries is wrong for the file that comes out. Handing those original timestamps
to anything that reads the cut media — a subtitle file, a frame label, a note
that says "at 05:12 the slide shows" — would be presenting the original's clock
as the cut version's. That is the one dishonest move available here, and it is
also the easy one, which is why the mapping lives in its own module with its own
tests instead of inside a caller.

The mapping is exact and needs no model. The cut list already records the ranges
that survive, in order, and the rendered file is those ranges concatenated. So a
kept range's new start is the total duration of the kept ranges before it, and a
moment inside it keeps its offset. Nothing is estimated.

Three behaviours are decisions rather than details:

- A moment inside a removed stretch has no position in the cut file. It is
  reported as missing rather than snapped to a nearby time, because a caller
  that gets a number back cannot tell it was invented.
- A subtitle line that straddles a cut is shortened to the part that survived,
  not moved. The words in the removed part are not in the audio any more.
- A line entirely inside a removed stretch is dropped and counted. Silence
  detection removed that audio; keeping the text would put a caption over
  someone else's sentence.

The count of dropped and shortened lines is returned, not logged, so the caller
can record what the remap cost instead of asserting it was lossless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from backend.core.speaker_diarization import speaker_display_map

# Below this a remapped line has no useful duration left: it survived a cut by a
# few milliseconds. Kept as a named constant because it is the line between
# "shortened" and "gone", and both are reported.
MIN_REMAPPED_SECONDS = 0.05

# Ranges in a cut list are rounded to milliseconds, so a boundary comparison has
# to tolerate that rounding or a line that ends exactly where a keep ends is read
# as falling outside it.
_EDGE = 0.002

TIMELINE_SOURCE_CUT_LIST = "debreath_cut_list_remap"
TIMELINE_SOURCE_ORIGINAL = "source_timeline"

# The transcript was produced *from* the cut file, so its timestamps are already
# that file's and there is nothing to convert. Remapping one of these a second
# time would shift everything twice — the exact error the remap exists to prevent,
# arrived at from the other direction — so which case applies is read from the
# result rather than guessed from whether a cut list happens to exist.
TIMELINE_SOURCE_NATIVE = "cut_media_native"


@dataclass(frozen=True)
class KeptRange:
    """One stretch that survived, and where it lands in the cut file."""

    start: float
    end: float
    new_start: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def contains(self, seconds: float) -> bool:
        return self.start - _EDGE <= seconds <= self.end + _EDGE

    def map(self, seconds: float) -> float:
        clamped = min(max(seconds, self.start), self.end)
        return round(self.new_start + (clamped - self.start), 3)


@dataclass
class RemapReport:
    """The remapped lines, and what the remap cost.

    ``dropped`` and ``shortened`` are the honest part: a caller that reports
    "subtitles remapped" without them is claiming the cut version says exactly
    what the original did.
    """

    segments: list[dict[str, Any]] = field(default_factory=list)
    dropped: int = 0
    shortened: int = 0
    kept_seconds: float = 0.0
    source_segments: int = 0

    @property
    def chars(self) -> int:
        return sum(len(str(segment.get("text") or "")) for segment in self.segments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": TIMELINE_SOURCE_CUT_LIST,
            "segments": len(self.segments),
            "source_segments": self.source_segments,
            "dropped_segments": self.dropped,
            "shortened_segments": self.shortened,
            "chars": self.chars,
            "duration_seconds": round(self.kept_seconds, 3),
        }


def kept_ranges(cut_list: Any) -> list[KeptRange]:
    """Read the surviving ranges out of a stored cut list, in file order.

    Takes the persisted payload rather than a ``CutPlan`` so a caller can work
    from the artifact on disk — which is the record that outlives the process
    that made it.
    """
    payload = cut_list if isinstance(cut_list, dict) else {}
    raw = payload.get("keeps")
    if not isinstance(raw, list):
        return []
    spans: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if end > start:
            spans.append((start, end))
    spans.sort()
    ranges: list[KeptRange] = []
    cursor = 0.0
    for start, end in spans:
        ranges.append(KeptRange(start=start, end=end, new_start=round(cursor, 3)))
        cursor += end - start
    return ranges


def kept_duration_seconds(ranges: Sequence[KeptRange]) -> float:
    return round(sum(item.duration for item in ranges), 3)


def new_time(ranges: Sequence[KeptRange], seconds: float) -> float | None:
    """Where a moment from the original lands in the cut file.

    ``None`` means it does not land anywhere: that moment was removed. The
    caller has to decide what to do about it, which is the point — a function
    that returned the nearest surviving second would let a wrong timestamp
    travel silently.
    """
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    for span in ranges:
        if span.contains(value):
            return span.map(value)
    return None


def _first_kept_at_or_after(ranges: Sequence[KeptRange], seconds: float) -> float | None:
    for span in ranges:
        if span.contains(seconds):
            return max(seconds, span.start)
        if span.start > seconds:
            return span.start
    return None


def _last_kept_at_or_before(ranges: Sequence[KeptRange], seconds: float) -> float | None:
    found: float | None = None
    for span in ranges:
        if span.contains(seconds):
            return min(seconds, span.end)
        if span.end < seconds:
            found = span.end
    return found


def timeline_segments(result: Any) -> list[dict[str, Any]]:
    """The task's timestamped lines, whichever field holds them.

    One definition, shared by the subtitle artifact and by the note, so the two
    cannot describe different transcripts while both claiming to describe the
    cut file.
    """
    if not isinstance(result, dict):
        return []
    for key in ("display_segments", "raw_segments"):
        raw = result.get(key)
        if not isinstance(raw, list):
            continue
        segments = [item for item in raw if isinstance(item, dict) and str(item.get("text") or "").strip()]
        if segments:
            return segments
    return []


def remap_segments(segments: Sequence[dict[str, Any]], ranges: Sequence[KeptRange]) -> RemapReport:
    """Put each line on the cut file's clock, dropping what was cut away.

    A line that straddles a cut is shortened to the surviving part; because the
    kept ranges are concatenated back to back in the output, a span that covers
    several of them is still contiguous there, so the shortened line needs no
    splitting.
    """
    usable = [item for item in segments if isinstance(item, dict) and str(item.get("text") or "").strip()]
    report = RemapReport(source_segments=len(usable), kept_seconds=kept_duration_seconds(ranges))
    if not ranges:
        return report
    for segment in usable:
        try:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
        except (TypeError, ValueError):
            report.dropped += 1
            continue
        if end < start:
            start, end = end, start
        kept_start = _first_kept_at_or_after(ranges, start)
        kept_end = _last_kept_at_or_before(ranges, end)
        if kept_start is None or kept_end is None or kept_end <= kept_start:
            report.dropped += 1
            continue
        mapped_start = new_time(ranges, kept_start)
        mapped_end = new_time(ranges, kept_end)
        if mapped_start is None or mapped_end is None:  # pragma: no cover - clipped above
            report.dropped += 1
            continue
        if mapped_end - mapped_start < MIN_REMAPPED_SECONDS:
            report.dropped += 1
            continue
        if kept_start > start + _EDGE or kept_end < end - _EDGE:
            report.shortened += 1
        remapped = dict(segment)
        remapped["start"] = mapped_start
        remapped["end"] = mapped_end
        report.segments.append(remapped)
    report.segments.sort(key=lambda item: (item["start"], item["end"]))
    return report


SPEAKER_LABEL_PREAMBLE = (
    "以下每行的格式是 [时间] 说话人 X：内容。同一个标号代表同一个人。\n"
    "写清一句话是谁说的时候，直接用这些标号；不要换成真实人名，也不要根据对话里的称呼、"
    "自我介绍或署名推断某个标号是谁。拿不准就不写归属。\n\n"
)


def timestamped_transcript(segments: Sequence[dict[str, Any]]) -> str:
    """The lines as ``[mm:ss] text``, on whatever clock they already carry.

    Used for the text that goes to a model. It reads the timestamps it is given
    and does not convert anything, so the caller has to hand it remapped
    segments — which is why remapping is a separate, tested step.

    Where the segments carry speakers, each line becomes
    ``[mm:ss] 说话人 A：text`` and the text is prefixed with the attribution
    rule. The rule rides with the transcript rather than sitting in one prompt
    builder because this text reaches the note through more than one channel,
    and a rule added to only one of them is a rule that silently does not apply
    on the other. It appears only when there are labels to talk about.
    """
    labels = speaker_display_map(segments)
    lines: list[str] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(segment.get("start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        total = max(0, int(start))
        speaker = labels.get(str(segment.get("speaker") or ""))
        body = f"{speaker}：{text}" if speaker else text
        lines.append(f"[{total // 60:02d}:{total % 60:02d}] {body}")
    joined = "\n".join(lines)
    if labels and joined:
        return SPEAKER_LABEL_PREAMBLE + joined
    return joined


__all__ = [
    "MIN_REMAPPED_SECONDS",
    "TIMELINE_SOURCE_CUT_LIST",
    "TIMELINE_SOURCE_NATIVE",
    "TIMELINE_SOURCE_ORIGINAL",
    "KeptRange",
    "RemapReport",
    "kept_duration_seconds",
    "kept_ranges",
    "new_time",
    "remap_segments",
    "timeline_segments",
    "timestamped_transcript",
    "SPEAKER_LABEL_PREAMBLE",
]
