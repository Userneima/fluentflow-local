"""Read the whole recording first, then decide what to cut, then render once.

Requirement (owner, 2026-10-15, plan draft 方案/2026-10-15-先转录再定怎么剪).
Cutting before anything has read the recording only knows loudness. It cannot
tell a pause in which the speaker lets people read a slide from one in which
they look for a file, and it leaves whatever was recorded after the meeting
ended, because that was not silence. So on this path the recording is
transcribed as it is, and before the cut is rendered a model reads one timeline
that holds the transcript, every long gap and whether the picture moved during
it, and answers two things:

- which gaps to keep (the picture shows something, or the speaker asked people
  to look);
- which stretches with talk in them to remove (waiting before the start,
  leftover after the end).

Measuring stays mechanical (``silence_cuts`` finds the gaps, the voice and
level checks still run); only the judgement is the model's. The cut is rendered
once (``debreath_job``), the transcript is moved onto the cut file's clock
(``cut_timeline``), and from there the task looks exactly like one that was cut
first: the note, the subtitles and the user's folder all read the cut file.

The cuts that carry the most risk are listed for the user to confirm after the
task, in the original recording's time: every removed stretch with talk in it,
every single cut longer than a minute, and gaps the volume rule cut although
words were transcribed in them. The edit itself is never shown for editing.

Without a model the volume plan is rendered unchanged. A judgement that would
remove more than a quarter of what was said is not applied at all.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from backend.core import cut_report, cut_timeline, debreath_job, job_store, output_folder, silence_cuts
from backend.core.result_artifacts import DEBREATH_MEDIA_KIND, TRANSCRIPT_MEDIA_CUT
from backend.core.silence_cuts import CutPlan, TimeRange
from backend.core.storage_cleanup import is_managed_path

logger = logging.getLogger(__name__)

# Gaps shorter than this are always cut; the model is not shown them. A pause
# under three seconds is not worth keeping for anyone to read or watch, and
# hundreds of numbered short gaps made the answer long and easy to get wrong.
LONG_GAP_SECONDS = 3.0
# Cuts are kept this far off every transcribed word.
WORD_MARGIN_SECONDS = 0.05
# A piece of a cut left between two words shorter than this is not cut.
MIN_CUT_PIECE_SECONDS = 0.1
# Whether the picture moved is measured for gaps at least this long, the
# longest first, at most ``MAX_PICTURE_CHECKS`` of them (each is a decode).
PICTURE_CHECK_SECONDS = 3.0
MAX_PICTURE_CHECKS = 80
PICTURE_CHECK_MAX_SECONDS = 30.0
# Share of frames that differ from the one before.
PICTURE_MOVING = 0.15
PICTURE_STILL = 0.02
# A removal must be at least this long, and all of them together may hold at
# most this share of the transcript's characters.
MIN_REMOVAL_SECONDS = 2.0
MAX_SPEECH_SHARE_REMOVED = 0.25
# Margin left around the first and last removed line.
EDGE_SECONDS = 0.1
# Reported for confirmation.
LONG_CUT_SECONDS = 60.0
# Every removed stretch with talk in it is listed; of the rest, at most this
# many, the riskiest first.
MAX_OTHER_REVIEW_ITEMS = 5
REQUEST_DEADLINE_SECONDS = 600.0
MAX_TRANSCRIPT_CHARS = 120_000

SYSTEM = """你会读到一段录像的时间线：每行开头是秒数，后面是这句话的转录；〔空白 #编号〕是一段没人说话的时间，写着它从几秒到几秒、多长、这段时间画面有没有在变。
空白默认全部剪掉，这一步要你决定两件事。

一、哪些空白要保留。只有观众在这段时间里需要看画面时才保留：
- 讲者刚说了"大家看一下""我演示一下""稍等大家读一下"之类，而且画面在变或停在要看的内容上；
- 画面一直在变，前后文说明是在演示、操作、写代码、写板书。
等人入会、切换窗口、找文件、调设备、"能听到吗"之后的空白，照剪。拿不准就剪。

二、哪些有人说话的段落要去掉。去掉的每一段都会交给用户确认，所以该去的都列出来：
- 正式开始前的等待、试音、闲聊；
- 正式结束（例如说完再见）之后多录的部分；说完再见又补充正事的，以补充的那句为准；
- 中间的废话：调设备和确认声音（"能听到吗""我共享一下屏幕"）、被打断后和主题无关的对话、整句重说（同一句话讲了两遍，去掉没讲好的那遍）、和主题无关的闲聊。
只去整句，不去半句；讲正事时的口头禅、语气词不算废话。
每一段给出第一句和最后一句开头的秒数；一直到录像结尾，to 填 null；从录像开头算起，from 填 0。

只回一个 JSON 对象：
{"keep_gaps": [{"id": 编号, "reason": "一句话理由"}], "remove": [{"from": 秒数, "to": 秒数或null, "reason": "一句话理由"}]}"""

Chat = Callable[[str, str], str]


def _texted(segments: Any) -> list[dict[str, Any]]:
    if not isinstance(segments, list):
        return []
    return [seg for seg in segments if isinstance(seg, dict) and str(seg.get("text") or "").strip()]


def _seconds(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def picture_change(source: Path, start: float, end: float, fps: float) -> float | None:
    """Share of frames in ``start..end`` that differ from the one before,
    decoded small. ``None`` when it could not be read."""
    seconds = min(end - start, PICTURE_CHECK_MAX_SECONDS)
    if seconds <= 0 or fps <= 0:
        return None
    result = silence_cuts.run_command([
        silence_cuts._ffmpeg_path(), "-hide_banner", "-nostats",
        "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(source), "-an",
        "-vf", "scale=160:-2,mpdecimate=hi=200:lo=64:frac=0.1,showinfo", "-f", "null", "-",
    ], timeout=300)
    if result.returncode != 0:
        return None
    changed = len(re.findall(r"pts_time:", result.stderr or ""))
    # The first frame always "changes"; it says nothing about motion.
    return max(0.0, min((changed - 1) / (seconds * fps), 1.0))


def _picture_label(share: float | None) -> str:
    if share is None:
        return "画面没测"
    if share >= PICTURE_MOVING:
        return "画面一直在变"
    if share > PICTURE_STILL:
        return "画面偶尔变化"
    return "画面静止"


def measure_pictures(source: Path, gaps: list[TimeRange]) -> dict[int, float | None]:
    """Picture change for the longest gaps, by their index in ``gaps``."""
    try:
        if not silence_cuts.has_video_stream(source):
            return {}
        fps = float(silence_cuts.source_frame_rate(source))
    except Exception:  # noqa: BLE001 - no picture measurement, still a timeline
        return {}
    candidates = sorted(
        (index for index, gap in enumerate(gaps) if gap.duration >= PICTURE_CHECK_SECONDS),
        key=lambda index: gaps[index].duration, reverse=True,
    )[:MAX_PICTURE_CHECKS]
    shares: dict[int, float | None] = {}
    for index in candidates:
        try:
            shares[index] = picture_change(source, gaps[index].start, gaps[index].end, fps)
        except Exception:  # noqa: BLE001 - one unreadable stretch
            shares[index] = None
    return shares


def build_timeline(
    duration: float, segments: list[dict[str, Any]], gaps: list[TimeRange], pictures: dict[int, float | None],
) -> tuple[str, dict[int, int]]:
    """The text the model reads, and gap id → index in ``gaps``."""
    rows: list[tuple[float, str]] = []
    for seg in segments:
        rows.append((_seconds(seg.get("start")), f"[{_seconds(seg.get('start')):.1f}] {str(seg.get('text')).strip()}"))
    ids: dict[int, int] = {}
    for index, gap in enumerate(gaps):
        if gap.duration < LONG_GAP_SECONDS:
            continue
        gap_id = len(ids) + 1
        ids[gap_id] = index
        picture = f" · {_picture_label(pictures.get(index))}" if index in pictures else ""
        rows.append((gap.start, f"〔空白 #{gap_id} · {gap.start:.1f}–{gap.end:.1f} 秒 · 长 {gap.duration:.1f} 秒{picture}〕"))
    rows.sort(key=lambda row: row[0])
    body = "\n".join(text for _, text in rows)
    if len(body) > MAX_TRANSCRIPT_CHARS:
        body = body[:MAX_TRANSCRIPT_CHARS // 2] + "\n……（中间省略）……\n" + body[-MAX_TRANSCRIPT_CHARS // 2:]
    return f"录像总长 {duration:.0f} 秒。\n\n{body}", ids


def parse_answer(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("回答里没有 JSON")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("回答不是 JSON 对象")
    return payload


def _nearest_line(segments: list[dict[str, Any]], seconds: float) -> int:
    return min(range(len(segments)), key=lambda i: abs(_seconds(segments[i].get("start")) - seconds))


def removal_range(
    segments: list[dict[str, Any]], duration: float, start_at: Any, end_at: Any,
) -> TimeRange | None:
    """The stretch from the line starting nearest ``start_at`` to the end of
    the line starting nearest ``end_at`` (or the end of the recording), edges
    kept a little off the neighbouring lines."""
    if not segments:
        return None
    first = _nearest_line(segments, _seconds(start_at))
    if first == 0:
        start = 0.0
    else:
        start = max(_seconds(segments[first - 1].get("end")), _seconds(segments[first].get("start")) - EDGE_SECONDS)
    if end_at is None:
        end = duration
    else:
        last = _nearest_line(segments, _seconds(end_at))
        if last == len(segments) - 1:
            end = duration
        else:
            end = min(_seconds(segments[last + 1].get("start")), _seconds(segments[last].get("end")) + EDGE_SECONDS)
    start, end = max(0.0, start), min(duration, end)
    if end - start < MIN_REMOVAL_SECONDS:
        return None
    return TimeRange(round(start, 3), round(end, 3))


def _inside(seg: dict[str, Any], span: TimeRange, share: float = 0.5) -> bool:
    start, end = _seconds(seg.get("start")), _seconds(seg.get("end"))
    length = max(end - start, 1e-6)
    overlap = max(0.0, min(end, span.end) - max(start, span.start))
    return overlap / length >= share


def speech_share(segments: list[dict[str, Any]], spans: list[TimeRange]) -> float:
    total = sum(len(str(seg.get("text")).strip()) for seg in segments)
    removed = sum(len(str(seg.get("text")).strip()) for seg in segments if any(_inside(seg, span) for span in spans))
    return removed / total if total else 0.0


def _clock(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def review_items(
    segments: list[dict[str, Any]], cuts: list[TimeRange], removals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The cuts the user is asked to confirm, in the original's time."""
    items: list[dict[str, Any]] = [
        {"kind": "removed_talk", "start": r["start"], "end": r["end"], "reason": r["reason"]} for r in removals
    ]
    spans = [TimeRange(r["start"], r["end"]) for r in removals]
    others: list[dict[str, Any]] = []
    for cut in cuts:
        if any(span.start <= cut.start + 0.01 and cut.end <= span.end + 0.01 for span in spans):
            continue
        words = [str(seg.get("text")).strip() for seg in segments if _inside(seg, cut, 0.8)]
        words = [w for w in words if len(w) >= 2]
        if cut.duration >= LONG_CUT_SECONDS:
            others.append({"kind": "long_gap", "start": cut.start, "end": cut.end,
                           "reason": f"这一段 {_clock(cut.duration)} 没人说话，整段剪掉了。"})
        elif words:
            quoted = "」「".join(words)[:60]
            others.append({"kind": "words_in_gap", "start": cut.start, "end": cut.end,
                           "reason": f"按音量判断是空白，但转录在这里认出了字：「{quoted}」。"})
    # Riskiest first: a long stretch with nothing said is a whole part of the
    # recording; words in a gap are usually a short aside.
    others.sort(key=lambda item: (item["kind"] != "long_gap", -(item["end"] - item["start"])))
    items.extend(others[:MAX_OTHER_REVIEW_ITEMS])
    for item in items:
        item["seconds"] = round(item["end"] - item["start"], 1)
        item["start"], item["end"] = round(item["start"], 1), round(item["end"], 1)
    items.sort(key=lambda item: item["start"])
    return items


def revise_plan(
    source: Path, plan: CutPlan, segments: list[dict[str, Any]], ask: Chat, *,
    pictures: Callable[[Path, list[TimeRange]], dict[int, float | None]] = measure_pictures,
) -> tuple[CutPlan, dict[str, Any]]:
    """The plan the model's reading turns ``plan`` into, and what it decided."""
    duration = plan.source_duration_seconds
    gaps = list(plan.cuts)
    measured = pictures(source, gaps)
    timeline, ids = build_timeline(duration, segments, gaps, measured)
    answer = parse_answer(_ask(ask, timeline))
    record: dict[str, Any] = {"long_gaps": len(ids), "pictures_measured": len(measured)}

    kept_gaps: list[dict[str, Any]] = []
    kept_reasons: dict[int, str] = {}
    for item in answer.get("keep_gaps") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = ids[int(item.get("id"))]
        except (KeyError, TypeError, ValueError):
            continue
        gap = gaps[index]
        kept_reasons[index] = str(item.get("reason") or "").strip()
        kept_gaps.append({"start": gap.start, "end": gap.end, "reason": str(item.get("reason") or "").strip(),
                          "picture": measured.get(index)})

    removals: list[dict[str, Any]] = []
    for item in answer.get("remove") or []:
        if not isinstance(item, dict):
            continue
        span = removal_range(segments, duration, item.get("from"), item.get("to"))
        if span is not None:
            removals.append({"start": span.start, "end": span.end, "reason": str(item.get("reason") or "").strip()})
    spans = [TimeRange(r["start"], r["end"]) for r in removals]
    share = speech_share(segments, spans)
    if share > MAX_SPEECH_SHARE_REMOVED:
        record["removal_refused"] = (
            f"模型要去掉的几段里有全部说话的 {share:.0%}，超过 {MAX_SPEECH_SHARE_REMOVED:.0%}，"
            "不像只是开头结尾多录的部分，没有去掉。"
        )
        record["refused_removals"] = removals
        removals, spans = [], []

    revised = apply_decisions(plan, kept_gaps, removals)
    record.update({
        "gaps": describe_gaps(gaps, measured, kept_reasons),
        "kept_gaps": kept_gaps,
        "removed": removals,
        "review": review_items(segments, revised.cuts, removals),
        "volume_plan_removed_seconds": plan.removed_seconds,
        "removed_seconds": revised.removed_seconds,
    })
    return revised, record


def describe_gaps(
    gaps: list[TimeRange], pictures: dict[int, float | None], kept: dict[int, str],
) -> dict[str, Any]:
    """Every gap the loudness plan found, for the processing report: the
    short ones counted together, the long ones one by one with what happened
    to them."""
    short = [gap for gap in gaps if gap.duration < LONG_GAP_SECONDS]
    long = [
        {
            "start": round(gap.start, 2), "end": round(gap.end, 2), "seconds": round(gap.duration, 1),
            "picture": _picture_label(pictures.get(index)) if index in pictures else None,
            "kept": index in kept, "reason": kept.get(index, ""),
        }
        for index, gap in enumerate(gaps) if gap.duration >= LONG_GAP_SECONDS
    ]
    return {"short": {"count": len(short), "seconds": round(sum(g.duration for g in short), 1)}, "long": long}


def words_of(result: dict[str, Any]) -> list[tuple[float, float]]:
    """Every transcribed word's time, from whichever segment list carries
    them. Empty when the transcription did not record word times."""
    for key in ("stt_raw_segments", "raw_segments", "source_stt_raw_segments", "source_raw_segments"):
        segments = result.get(key)
        if not isinstance(segments, list):
            continue
        spans = [
            (_seconds(word.get("start")), _seconds(word.get("end")))
            for seg in segments if isinstance(seg, dict) and isinstance(seg.get("words"), list)
            # Stored as {"start", "end", "text", "conf"}; "word" is
            # faster-whisper's own name for the same field.
            for word in seg["words"] if isinstance(word, dict) and str(word.get("text") or word.get("word") or "").strip()
        ]
        if spans:
            return sorted(spans)
    return []


# How the voice detector reads the stretches where a word overlaps a cut. More
# sensitive than the fragment cleanup's 0.5: the words in question were cut for
# being quiet.
VOICE_THRESHOLD = 0.3
VOICE_RATE = 16000
VOICE_CONTEXT_SECONDS = 0.3
# Voice heard inside a word's overlap with a cut for less than this is a
# breath or a click at the edge of the word, not the word: tens of
# milliseconds of it would otherwise keep a whole short gap.
MIN_SPOKEN_SECONDS = 0.08


def _word_regions(plan: CutPlan, words: list[tuple[float, float]]) -> list[TimeRange]:
    """Where a transcribed word overlaps a cut: the overlap, merged."""
    regions: list[TimeRange] = []
    index = 0
    for cut in plan.cuts:
        while index < len(words) and words[index][1] <= cut.start:
            index += 1
        probe = index
        while probe < len(words) and words[probe][0] < cut.end:
            start, end = max(words[probe][0], cut.start), min(words[probe][1], cut.end)
            if end > start:
                regions.append(TimeRange(start, end))
            probe += 1
    return silence_cuts._merge_ranges(regions)


def voiced_parts(source: Path, regions: list[TimeRange]) -> list[TimeRange] | None:
    """The parts of ``regions`` in which the voice detector hears a voice. One
    streamed decode of the audio; only the regions are examined. None when the
    detector or the audio is not available."""
    try:
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except Exception:  # noqa: BLE001 - no detector here
        return None
    if not regions:
        return []
    options = VadOptions(threshold=VOICE_THRESHOLD, min_speech_duration_ms=silence_cuts.FRAGMENT_MIN_VOICE_MS,
                         min_silence_duration_ms=100, speech_pad_ms=30)
    process = subprocess.Popen(
        [silence_cuts._ffmpeg_path(), "-v", "error", "-i", str(source), "-vn", "-ac", "1",
         "-ar", str(VOICE_RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    found: list[TimeRange] = []
    buffer = np.zeros(0, dtype=np.int16)
    offset = 0  # sample index of buffer[0]
    pending = sorted(regions, key=lambda r: r.start)
    block = VOICE_RATE * 2 * 60
    try:
        while pending:
            data = process.stdout.read(block) if process.stdout else b""
            done = not data
            if data:
                buffer = np.concatenate([buffer, np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16)])
            end_sample = offset + len(buffer)
            while pending and (done or (pending[0].end + VOICE_CONTEXT_SECONDS) * VOICE_RATE <= end_sample):
                region = pending.pop(0)
                lo = max(int((region.start - VOICE_CONTEXT_SECONDS) * VOICE_RATE), offset)
                hi = min(int((region.end + VOICE_CONTEXT_SECONDS) * VOICE_RATE), end_sample)
                if hi <= lo:
                    continue
                samples = buffer[lo - offset: hi - offset].astype(np.float32) / 32768.0
                for speech in get_speech_timestamps(samples, options):
                    start = max(region.start, (lo + speech["start"]) / VOICE_RATE)
                    end = min(region.end, (lo + speech["end"]) / VOICE_RATE)
                    if end > start:
                        found.append(TimeRange(round(start, 3), round(end, 3)))
            if done:
                break
            keep_from = int(((pending[0].start if pending else 0) - VOICE_CONTEXT_SECONDS) * VOICE_RATE)
            drop = max(0, min(keep_from, end_sample) - offset)
            if drop:
                buffer = buffer[drop:]
                offset += drop
    finally:
        if process.stdout:
            process.stdout.close()
        process.kill()
        process.wait()
    return silence_cuts._merge_ranges(found)


def spoken_words(
    source: Path, plan: CutPlan, words: list[tuple[float, float]],
    voiced: Callable[[Path, list[TimeRange]], list[TimeRange] | None] = None,
) -> tuple[list[tuple[float, float]], dict[str, Any]]:
    """Where the cuts reach into speech: a transcribed word there, and the
    voice detector hears it too.

    Measured on an 8-minute meeting (2026-10-15): words overlapped 37.8 s of
    the loudness cuts, but the detector heard a voice in only 2.1 s of it
    (6.5 s at a lower threshold). The transcriber stretches a word's time over
    the pause after it, so word times alone would have kept 122 slivers of
    silence."""
    if not words:
        return [], {"words_known": False}
    regions = _word_regions(plan, words)
    heard = (voiced or voiced_parts)(source, regions)
    record: dict[str, Any] = {"words_known": True, "seconds_words_overlap_cuts": round(sum(r.duration for r in regions), 2)}
    if heard is None:
        record["voice_unchecked"] = True
        return [], record
    return [(r.start, r.end) for r in heard if r.duration >= MIN_SPOKEN_SECONDS], record


def protect_words(plan: CutPlan, words: list[tuple[float, float]]) -> tuple[CutPlan, dict[str, Any]]:
    """``plan`` with every cut kept off ``words`` (spoken word spans, see
    ``spoken_words``): loudness cuts quiet speech as if it were a gap, and the
    transcript says where someone spoke, so the cut stops short of it instead
    of asking the user afterwards."""
    if not words:
        return plan, {}
    pieces: list[TimeRange] = []
    index = 0
    for cut in plan.cuts:
        while index < len(words) and words[index][1] + WORD_MARGIN_SECONDS <= cut.start:
            index += 1
        cursor = cut.start
        probe = index
        while probe < len(words) and words[probe][0] - WORD_MARGIN_SECONDS < cut.end:
            start, end = words[probe][0] - WORD_MARGIN_SECONDS, words[probe][1] + WORD_MARGIN_SECONDS
            if start > cursor:
                pieces.append(TimeRange(cursor, start))
            cursor = max(cursor, end)
            probe += 1
        if cursor < cut.end:
            pieces.append(TimeRange(cursor, cut.end))
    kept = [piece for piece in pieces if piece.duration >= MIN_CUT_PIECE_SECONDS]
    protected = plan.removed_seconds - sum(piece.duration for piece in kept)
    cuts = [TimeRange(round(c.start, 3), round(c.end, 3)) for c in kept]
    revised = replace(
        plan, cuts=cuts,
        keeps=silence_cuts._keeps_between(plan.source_duration_seconds, cuts, silence_cuts.MIN_KEEP_SECONDS),
        warnings=list(plan.warnings),
    )
    return revised, {"seconds_kept_for_words": round(max(protected, 0.0), 2)}


def _overlaps(a: TimeRange, start: float, end: float) -> bool:
    return min(a.end, end) - max(a.start, start) > 0.01


def apply_decisions(plan: CutPlan, kept_gaps: list[dict[str, Any]], removals: list[dict[str, Any]]) -> CutPlan:
    """``plan`` with the gaps overlapping ``kept_gaps`` left in and the
    ``removals`` cut. Decisions are matched by time, so they can be applied
    again to the same recording's loudness plan without asking anyone."""
    duration = plan.source_duration_seconds
    cuts = [
        gap for gap in plan.cuts
        if not any(_overlaps(gap, _seconds(k["start"]), _seconds(k["end"])) for k in kept_gaps)
    ]
    cuts = silence_cuts._merge_ranges(cuts + [TimeRange(_seconds(r["start"]), _seconds(r["end"])) for r in removals])
    return replace(
        plan,
        cuts=[TimeRange(round(c.start, 3), round(c.end, 3)) for c in cuts],
        keeps=silence_cuts._keeps_between(duration, cuts, silence_cuts.MIN_KEEP_SECONDS),
        warnings=list(plan.warnings),
    )


def _ask(ask: Chat, user: str) -> str:
    from backend.core.note_coverage import _within_deadline

    return _within_deadline(ask, SYSTEM, user, REQUEST_DEADLINE_SECONDS)


def _default_chat() -> tuple[Chat, str] | None:
    from backend.core import visual_note_job
    from backend.core.local_config import resolve_secret

    return visual_note_job.coverage_checker(resolve_secret(None, "anthropic_api_key"))


def _remapped_words(words: Any, ranges: list[cut_timeline.KeptRange]) -> Any:
    if not isinstance(words, list):
        return words
    moved = []
    for word in words:
        if not isinstance(word, dict):
            continue
        start = cut_timeline.new_time(ranges, _seconds(word.get("start")))
        end = cut_timeline.new_time(ranges, _seconds(word.get("end")))
        if start is None or end is None:
            continue
        moved.append({**word, "start": start, "end": end})
    return moved


def _onto_cut_clock(result: dict[str, Any], ranges: list[cut_timeline.KeptRange]) -> dict[str, Any]:
    """Every timed transcript list moved onto the cut file's clock; the
    original lists kept under ``source_*``."""
    for key in ("display_segments", "raw_segments", "stt_raw_segments"):
        segments = result.get(key)
        if not isinstance(segments, list):
            continue
        report = cut_timeline.remap_segments(segments, ranges)
        result[f"source_{key}"] = segments
        result[key] = [
            {**seg, "words": _remapped_words(seg.get("words"), ranges)} if "words" in seg else seg
            for seg in report.segments
        ]
    result["transcript_text"] = "\n".join(
        str(seg.get("text") or "").strip() for seg in result.get("raw_segments") or [] if str(seg.get("text") or "").strip()
    )
    result["transcript_media"] = TRANSCRIPT_MEDIA_CUT
    result["playback_media_kind"] = DEBREATH_MEDIA_KIND
    return result


def survey_and_cut(
    task_id: str, client_id: str | None = None, *, chat: tuple[Chat, str] | None = None,
) -> dict[str, Any] | None:
    """Judge the recording from its transcript, cut it once, and move the
    transcript onto the cut. Returns what was recorded, or None when there was
    nothing to do. Never raises: the transcript of the original stands."""
    job = job_store.get_job(task_id, client_id=client_id)
    result = dict((job or {}).get("result") or {})
    if not job or str(result.get("transcript_media") or "") == TRANSCRIPT_MEDIA_CUT:
        return None
    if debreath_job.debreath_state(result).get("used_for_transcription"):
        return None
    segments = _texted(result.get("raw_segments"))
    words = words_of(result)
    try:
        source = debreath_job.resolve_source(task_id)
    except debreath_job.DebreathError:
        return None

    asker = chat if chat is not None else _default_chat()
    record: dict[str, Any] = {"judged_by": asker[1] if asker else None}
    final: dict[str, CutPlan] = {}

    def revise(media: Path, plan: CutPlan) -> CutPlan:
        spoken, protection = spoken_words(media, plan, words)
        plan, kept = protect_words(plan, spoken)
        record["word_protection"] = {**protection, **kept}
        record["spoken"] = [{"start": round(a, 2), "end": round(b, 2)} for a, b in spoken]
        if asker is None or not segments:
            record["gaps"] = describe_gaps(list(plan.cuts), {}, {})
            record["skipped"] = "没有可用的模型或转录，只按音量剪。"
            final["plan"] = plan
            return plan
        try:
            revised, decided = revise_plan(media, plan, segments, asker[0])
            record.update(decided)
        except Exception as exc:  # noqa: BLE001 - the volume plan stands
            logger.warning("survey for %s not used: %s", task_id, exc)
            record["error"] = str(exc)[:300]
            revised = plan
        final["plan"] = revised
        return revised

    try:
        debreath_job.run_debreath(task_id, client_id=client_id, revise=revise)
    except Exception as exc:  # noqa: BLE001 - the original's transcript stands
        logger.warning("survey cut for %s failed: %s", task_id, exc)
        record["render_error"] = str(exc)[:300]
        _record(task_id, client_id, record)
        return record

    if not _finish(task_id, client_id, source, final.get("plan"), record, asker):
        _record(task_id, client_id, record)
    return record


def _finish(
    task_id: str, client_id: str | None, source: Path, plan: CutPlan | None, record: dict[str, Any],
    chat: tuple[Chat, str] | None = None,
) -> bool:
    """After a render: the transcript onto the cut's clock, the cut into the
    user's folder, the survey and its report (``cut_report``) recorded. False
    when nothing was rendered."""
    job = job_store.get_job(task_id, client_id=client_id) or {}
    result = dict(job.get("result") or {})
    state = debreath_job.debreath_state(result)
    media = debreath_job.cut_file_on_disk(task_id, result)
    if not state.get("rendered") or plan is None or media is None:
        return False
    result = _onto_cut_clock(result, cut_timeline.kept_ranges(plan.as_dict()))
    record["report"] = cut_report.build(record, _texted(result.get("source_raw_segments")), chat)
    record["report"]["after_seconds"] = round(plan.kept_seconds, 1)
    record["report"]["before_seconds"] = round(plan.source_duration_seconds, 1)
    delivered: dict[str, Any] = {}
    if not is_managed_path(source):
        delivered = output_folder.deliver_media(task_id, source, media, output_folder.CUT_LABEL)
    # The transcript is on the cut's clock now, exactly as when the cut came
    # first; the result page and the re-cut guard read this flag for that.
    result["debreath"] = {**state, **delivered, "ran_before_transcription": True, "survey": record}
    from backend.core.result_artifacts import _attach_result_artifacts

    result = _attach_result_artifacts(task_id, result)
    job_store.update_job_result(task_id, result, client_id=client_id)
    return True


def _record(task_id: str, client_id: str | None, record: dict[str, Any]) -> None:
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        return
    result = dict(job.get("result") or {})
    result["debreath"] = {**debreath_job.debreath_state(result), "survey": record}
    job_store.update_job_result(task_id, result, client_id=client_id)


ANSWERS = {"right", "wrong", "pending"}


class ReviewError(RuntimeError):
    """A confirmation that cannot be taken, in the user's words."""


def _survey(task_id: str, client_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        raise ReviewError("任务不存在")
    result = dict(job.get("result") or {})
    survey = dict(debreath_job.debreath_state(result).get("survey") or {})
    if not survey.get("review"):
        raise ReviewError("这个任务没有需要确认的剪辑")
    return result, survey


def answer(task_id: str, index: int, value: str, client_id: str | None = None) -> dict[str, Any]:
    """Record the user's answer to one listed cut: ``right`` (the cut was
    right), ``wrong`` (it should not have been cut; ``restore`` does the
    work) or ``pending`` (answer taken back). Kept to measure the model's
    judgement and as material for the case library."""
    if value not in ANSWERS:
        raise ReviewError("回答只能是剪得对、不该剪或撤回")
    result, survey = _survey(task_id, client_id)
    if not 0 <= index < len(survey["review"]):
        raise ReviewError("没有这一处剪辑")
    answers = dict(survey.get("answers") or {})
    if value == "pending":
        answers.pop(str(index), None)
    else:
        answers[str(index)] = value
    survey["answers"] = answers
    result["debreath"] = {**debreath_job.debreath_state(result), "survey": survey}
    job_store.update_job_result(task_id, result, client_id=client_id)
    return survey


def _rerender(
    task_id: str, client_id: str | None, result: dict[str, Any], survey: dict[str, Any],
    kept: list[dict[str, Any]], removals: list[dict[str, Any]], after: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Render the cut again from the original with ``kept`` and ``removals``
    as the decisions, without asking the model again; the transcript is moved
    from the original's clock onto the new cut, the report written again.
    ``after`` updates the survey record once the render succeeded. Blocking."""
    source = debreath_job.resolve_source(task_id)
    survey.update({"removed": removals, "kept_gaps": kept})
    # Back onto the original's clock, so the render remaps from there.
    for key in ("display_segments", "raw_segments", "stt_raw_segments"):
        if isinstance(result.get(f"source_{key}"), list):
            result[key] = result[f"source_{key}"]
    result["transcript_media"] = "source"
    result["debreath"] = {**debreath_job.debreath_state(result), "survey": survey, "used_for_transcription": False}
    job_store.update_job_result(task_id, result, client_id=client_id)

    final: dict[str, CutPlan] = {}
    words = words_of(result)

    def revise(media: Path, plan: CutPlan) -> CutPlan:
        spoken, _ = spoken_words(media, plan, words)
        final["plan"] = apply_decisions(protect_words(plan, spoken)[0], kept, removals)
        return final["plan"]

    try:
        debreath_job.run_debreath(task_id, client_id=client_id, revise=revise)
    except Exception as exc:
        survey.pop("restoring", None)
        survey.pop("rerendering", None)
        survey["restore_error"] = f"重新导出没成功：{str(exc)[:200]}"
        _record(task_id, client_id, survey)
        raise
    survey.pop("restoring", None)
    survey.pop("rerendering", None)
    survey.pop("restore_error", None)
    after(survey)
    _finish(task_id, client_id, source, final.get("plan"), survey, _default_chat())
    return survey


def _mark_gaps(survey: dict[str, Any], start: float, end: float, kept: bool, reason: str) -> None:
    for gap in (survey.get("gaps") or {}).get("long") or []:
        if _overlaps(TimeRange(_seconds(gap["start"]), _seconds(gap["end"])), start, end):
            gap.update({"kept": kept, "reason": reason})


def restore(task_id: str, index: int, client_id: str | None = None) -> dict[str, Any]:
    """Put one cut listed for confirmation back and render again."""
    result, survey = _survey(task_id, client_id)
    if not 0 <= index < len(survey["review"]):
        raise ReviewError("没有这一处剪辑")
    item = survey["review"][index]
    removals = [r for r in survey.get("removed") or [] if not _overlaps(TimeRange(r["start"], r["end"]), item["start"], item["end"])]
    kept = list(survey.get("kept_gaps") or [])
    if item.get("kind") != "removed_talk":
        kept.append({"start": item["start"], "end": item["end"], "reason": "用户说这段不该剪"})
    survey.update({"restoring": index, "answers": {**dict(survey.get("answers") or {}), str(index): "wrong"}})

    def after(record: dict[str, Any]) -> None:
        if item.get("kind") != "removed_talk":
            _mark_gaps(record, item["start"], item["end"], True, "你说这段不该剪，已放回。")
        item["restored"] = True

    return _rerender(task_id, client_id, result, survey, kept, removals, after)


def _report_item(survey: dict[str, Any], ref: str) -> dict[str, Any] | None:
    for section in (survey.get("report") or {}).get("sections") or []:
        for item in section.get("items") or []:
            if item.get("ref") == ref:
                return item
    return None


def flip(task_id: str, ref: str, client_id: str | None = None) -> dict[str, Any]:
    """Turn one decision in the processing report around: what was cut is put
    back, what was kept is cut. The change is the user's feedback and is kept
    with what the model had decided (``user_changes``), to measure its
    judgement and as material for the case library. Blocking."""
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        raise ReviewError("任务不存在")
    result = dict(job.get("result") or {})
    survey = dict(debreath_job.debreath_state(result).get("survey") or {})
    item = _report_item(survey, ref)
    if item is None or item.get("start") is None or item.get("end") is None:
        raise ReviewError("这一条不能单独改")
    start, end = _seconds(item["start"]), _seconds(item["end"])
    removals = [dict(r) for r in survey.get("removed") or []]
    kept = [dict(k) for k in survey.get("kept_gaps") or []]
    now_removed = item.get("action") != "removed"
    if now_removed:
        kept = [k for k in kept if not _overlaps(TimeRange(_seconds(k["start"]), _seconds(k["end"])), start, end)]
        if item.get("kind") != "long_gap":
            removals.append({"start": start, "end": end, "reason": "你说这段应该剪掉。"})
    else:
        removals = [r for r in removals if not _overlaps(TimeRange(_seconds(r["start"]), _seconds(r["end"])), start, end)]
        if item.get("kind") != "removed_talk":
            kept.append({"start": start, "end": end, "reason": "你说这段不该剪。"})
    from datetime import datetime, timezone

    change = {
        "ref": ref, "kind": item.get("kind"), "start": start, "end": end,
        "model_said": item.get("action"), "user_said": "removed" if now_removed else "kept",
        "model_reason": item.get("text"), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    survey["rerendering"] = True

    def after(record: dict[str, Any]) -> None:
        record["user_changes"] = [*(record.get("user_changes") or []), change]
        reason = "你说这段应该剪掉。" if now_removed else "你说这段不该剪，已放回。"
        _mark_gaps(record, start, end, not now_removed, reason)
        if now_removed:
            record["spoken"] = [sp for sp in record.get("spoken") or [] if not _overlaps(TimeRange(_seconds(sp["start"]), _seconds(sp["end"])), start, end)]
            record["refused_removals"] = [
                r for r in record.get("refused_removals") or []
                if not _overlaps(TimeRange(_seconds(r["start"]), _seconds(r["end"])), start, end)
            ]

    return _rerender(task_id, client_id, result, survey, kept, removals, after)


__all__ = [
    "answer",
    "flip",
    "restore",
    "ReviewError","survey_and_cut", "revise_plan", "build_timeline", "removal_range", "review_items"]
