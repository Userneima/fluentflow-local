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

What was done is shown on the task as a processing report (``cut_report``).
The user is asked to judge only what the model was unsure of and what the rules
always put to them (every removed stretch with talk in it, every single cut
longer than a minute); the rest is read. Any decision can still be turned
around, and every change can be undone.

Without a model the volume plan is rendered unchanged. A judgement that would
remove more than a quarter of what was said is not applied at all.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from backend.core import model_usage, task_steps
from backend.core import cut_cases, cut_judge, cut_report, cut_timeline, misheard, original_locator, debreath_job, job_store, output_folder, silence_cuts, task_stop
from backend.core.result_artifacts import DEBREATH_MEDIA_KIND, TRANSCRIPT_MEDIA_CUT, TRANSCRIPT_MEDIA_SOURCE, resolve_artifact_file
from backend.core.storage_paths import _artifact_storage_dir
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
REQUEST_DEADLINE_SECONDS = 600.0
MAX_TRANSCRIPT_CHARS = 120_000

SYSTEM = """开头可能先给你这位用户以前对剪辑的判断，情况相似时照他的判断做。然后你会读到一段录像的时间线：每行开头是秒数，后面是这句话的转录；〔空白 #编号〕是一段没人说话的时间，写着它从几秒到几秒、多长、这段时间画面有没有在变。
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

带〔和全片语言不同，多半是噪音被听成的话〕的行，通常是没人在讲话时（放音乐、现场嘈杂、拍照）转录凭空写出的句子，不是有人在说。判断这段时把它当作没人讲话；写理由时说这段在发生什么（例如"拍照间隙没人讲话，只有现场杂音"），不要复述这些句子，也不要说成"外语字幕"。

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
    shots: dict[int, int] | None = None,
) -> tuple[str, dict[int, int]]:
    """The text the model reads, and gap id → index in ``gaps``."""
    rows: list[tuple[float, str]] = []
    foreign = misheard.tagger(segments)
    for seg in segments:
        text = str(seg.get("text")).strip()
        tag = " 〔和全片语言不同，多半是噪音被听成的话〕" if foreign(text) else ""
        rows.append((_seconds(seg.get("start")), f"[{_seconds(seg.get('start')):.1f}] {text}{tag}"))
    ids: dict[int, int] = {}
    for index, gap in enumerate(gaps):
        if gap.duration < LONG_GAP_SECONDS:
            continue
        gap_id = len(ids) + 1
        ids[gap_id] = index
        picture = f" · {_picture_label(pictures.get(index))}" if index in pictures else ""
        if shots and shots.get(gap_id):
            picture += f" · 附了 {shots[gap_id]} 张截图"
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


# Screenshots for the long gaps whose picture moved, the longest first: the
# transcript cannot say whether that was a demonstration or a window being
# switched (owner's design, 2026-10-16). Two each, a third and two thirds in.
MAX_SHOT_GAPS = 12
SHOT_WIDTH = 1280
SHOTS_NOTE = """

有些空白附了截图（时间线里写着「附了 N 张截图」，截图按「空白 #编号」标好跟在后面）。看截图判断这段画面是不是观众需要看的（讲解中的页面、演示操作、写代码），还是切窗口、找文件、等人、桌面这类不用看的；理由里说你在截图上看到了什么。"""


def _grab(source: Path, seconds: float, target: Path) -> bool:
    result = silence_cuts.run_command([
        silence_cuts._ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(source),
        "-frames:v", "1", "-vf", f"scale='min({SHOT_WIDTH},iw)':-2", "-q:v", "4", "-y", str(target),
    ], timeout=120)
    return result.returncode == 0 and target.is_file()


def gap_screenshots(
    source: Path, gaps: list[TimeRange], measured: dict[int, float | None], ids: dict[int, int], folder: Path,
) -> tuple[list[tuple[str, Path]], dict[int, int]]:
    """Screenshots of the long gaps whose picture moved, labelled for the
    model, and how many each gap id got."""
    by_index = {index: gap_id for gap_id, index in ids.items()}
    candidates = sorted(
        (index for index, share in measured.items()
         if index in by_index and share is not None and share > PICTURE_STILL and gaps[index].duration >= PICTURE_CHECK_SECONDS),
        key=lambda index: gaps[index].duration, reverse=True,
    )[:MAX_SHOT_GAPS]
    images: list[tuple[str, Path]] = []
    counts: dict[int, int] = {}
    for index in sorted(candidates, key=lambda i: gaps[i].start):
        gap, gap_id = gaps[index], by_index[index]
        for n, share in enumerate((1 / 3, 2 / 3), start=1):
            at = gap.start + gap.duration * share
            target = folder / f"gap{gap_id}_{n}.jpg"
            if _grab(source, at, target):
                images.append((f"空白 #{gap_id} 的截图 {n}（原片 {at:.0f} 秒）", target))
                counts[gap_id] = counts.get(gap_id, 0) + 1
    return images, counts


@dataclass
class Material:
    """What the judge reads: the timeline, and the long gaps it numbers."""

    timeline: str
    gaps: list[TimeRange]
    ids: dict[int, int]
    measured: dict[int, float | None]
    images: list[tuple[str, Path]] = field(default_factory=list)


def prepare_material(
    source: Path, plan: CutPlan, segments: list[dict[str, Any]], *,
    pictures: Callable[[Path, list[TimeRange]], dict[int, float | None]] | None = None,
    shots_folder: Path | None = None,
) -> Material:
    """The timeline for ``plan``'s gaps, with screenshots of the long gaps
    whose picture moved when ``shots_folder`` is given. The same material for
    every judge: FluentFlow's model and the user's own agent."""
    duration = plan.source_duration_seconds
    gaps = list(plan.cuts)
    measured = (pictures or measure_pictures)(source, gaps)
    timeline, ids = build_timeline(duration, segments, gaps, measured)
    images: list[tuple[str, Path]] = []
    if shots_folder is not None:
        images, counts = gap_screenshots(source, gaps, measured, ids, shots_folder)
        timeline, ids = build_timeline(duration, segments, gaps, measured, counts)
    return Material(timeline, gaps, ids, measured, images)


def apply_answer(
    answer: dict[str, Any], plan: CutPlan, segments: list[dict[str, Any]], material: Material,
) -> tuple[CutPlan, dict[str, Any]]:
    """The plan a judge's answer turns ``plan`` into, and what was decided.
    The same rules for every judge: gap ids are only those in the timeline,
    each removal is whole lines, and removals that together take more than
    ``MAX_SPEECH_SHARE_REMOVED`` of what was said are not applied (they are
    put to the user in the report instead)."""
    duration = plan.source_duration_seconds
    gaps, ids, measured = material.gaps, material.ids, material.measured
    record: dict[str, Any] = {}
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
            f"要去掉的几段里有全部说话的 {share:.0%}，超过 {MAX_SPEECH_SHARE_REMOVED:.0%}，"
            "不像只是开头结尾多录的部分，没有去掉，逐段放进「需要你判断」。"
        )
        record["refused_removals"] = removals
        removals, spans = [], []

    revised = apply_decisions(plan, kept_gaps, removals)
    record.update({
        "gaps": describe_gaps(gaps, measured, kept_reasons),
        "kept_gaps": kept_gaps,
        "removed": removals,
        "volume_plan_removed_seconds": plan.removed_seconds,
        "removed_seconds": revised.removed_seconds,
    })
    return revised, record


@model_usage.part_of(task_steps.CUT, "判断剪哪里")
def revise_plan(
    source: Path, plan: CutPlan, segments: list[dict[str, Any]], ask: Chat, *,
    pictures: Callable[[Path, list[TimeRange]], dict[int, float | None]] | None = None,
    cases: str = "",
    look: Callable[[str, str, list[tuple[str, Path]]], str] | None = None,
) -> tuple[CutPlan, dict[str, Any]]:
    """The plan the model's reading turns ``plan`` into, and what it decided.
    ``look`` is the judge's call with pictures, when it can see them: the long
    gaps whose picture moved are then shown to it."""
    if look is not None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="fluentflow-gap-shots-") as folder:
            material = prepare_material(source, plan, segments, pictures=pictures, shots_folder=Path(folder))
            from backend.core.note_coverage import _within_deadline

            answer = parse_answer(_within_deadline(
                lambda system, user: look(system, user, material.images), SYSTEM + (SHOTS_NOTE if material.images else ""),
                cases + material.timeline, REQUEST_DEADLINE_SECONDS,
            ))
    else:
        material = prepare_material(source, plan, segments, pictures=pictures)
        answer = parse_answer(_ask(ask, cases + material.timeline))
    revised, record = apply_answer(answer, plan, segments, material)
    return revised, {
        "long_gaps": len(material.ids), "pictures_measured": len(material.measured),
        "screenshots_sent": len(material.images), **record,
    }


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


def word_texts(result: dict[str, Any]) -> list[tuple[float, float, str]]:
    """Every transcribed word with its time and text (see ``words_of``)."""
    for key in ("stt_raw_segments", "raw_segments", "source_stt_raw_segments", "source_raw_segments"):
        segments = result.get(key)
        if not isinstance(segments, list):
            continue
        found = [
            (_seconds(word.get("start")), _seconds(word.get("end")), str(word.get("text") or word.get("word") or "").strip())
            for seg in segments if isinstance(seg, dict) and isinstance(seg.get("words"), list)
            for word in seg["words"] if isinstance(word, dict) and str(word.get("text") or word.get("word") or "").strip()
        ]
        if found:
            return sorted(found)
    return []


def speech_spans(result: dict[str, Any]) -> list[tuple[float, float]]:
    """Where the transcript says someone spoke: each word when the transcript
    has word times, else each line.

    Lines are enough. Measured 2026-10-15 on a 2h28m meeting: with the voice
    detector deciding inside them, lines kept 165.1 s of quiet speech against
    160.5 s from word times (all of it included), while asking the transcriber
    for word times costs 18-40% more transcription time. So it is not asked."""
    words = words_of(result)
    if words:
        return words
    for key in ("source_raw_segments", "raw_segments"):
        lines = _texted(result.get(key))
        if lines:
            return sorted((_seconds(seg.get("start")), _seconds(seg.get("end"))) for seg in lines)
    return []


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


def _judge(chat: tuple[Chat, str] | None = None) -> cut_judge.Judge | None:
    """The judge (``cut_judge``), or a text-only one around ``chat``."""
    if chat is not None:
        return cut_judge.Judge(chat[1], False, lambda system, user, images: chat[0](system, user))
    return cut_judge.choose()


def _default_chat() -> tuple[Chat, str] | None:
    """The judge's text call, for the processing report."""
    judge = _judge()
    return (judge.text, judge.name) if judge else None


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


# Every timed transcript list a task keeps. The corrected lines are what the
# text note and an agent read first, so they move with the others.
SEGMENT_KEYS = ("display_segments", "raw_segments", "stt_raw_segments", "corrected_segments")


def _lines_text(segments: Any) -> str:
    return "\n".join(
        str(seg.get("text") or "").strip() for seg in segments or [] if isinstance(seg, dict) and str(seg.get("text") or "").strip()
    )


# The correction records (subtitle check, glossary step), which carry the
# time of the sentence they changed.
CORRECTION_RECORDS = ("subtitle_corrections", "glossary_corrections")


def _original_time(record: dict[str, Any], edge: str) -> Any:
    kept = record.get(f"source_{edge}")
    return kept if kept is not None else record.get(edge)


def _records_onto_source_clock(result: dict[str, Any]) -> None:
    """The correction records back on the original's clock, with the
    transcript they belong to."""
    for records_key in CORRECTION_RECORDS:
        if isinstance(result.get(records_key), list):
            result[records_key] = [
                {
                    **{k: v for k, v in record.items() if k not in ("source_start", "source_end")},
                    "start": _original_time(record, "start"),
                    "end": _original_time(record, "end"),
                }
                for record in result[records_key] if isinstance(record, dict)
            ]


def _onto_cut_clock(result: dict[str, Any], ranges: list[cut_timeline.KeptRange]) -> dict[str, Any]:
    """Every timed transcript list moved onto the cut file's clock; the
    original lists kept under ``source_*``."""
    for key in SEGMENT_KEYS:
        segments = result.get(key)
        if not isinstance(segments, list) or (key == "corrected_segments" and not segments):
            continue
        report = cut_timeline.remap_segments(segments, ranges)
        result[f"source_{key}"] = segments
        result[key] = [
            {**seg, "words": _remapped_words(seg.get("words"), ranges)} if "words" in seg else seg
            for seg in report.segments
        ]
    result["transcript_text"] = _lines_text(result.get("raw_segments"))
    if result.get("corrected_segments"):
        result["corrected_transcript_text"] = _lines_text(result["corrected_segments"])
    for records_key in CORRECTION_RECORDS:
        if not isinstance(result.get(records_key), list):
            continue
        # Undoing one finds its sentence by time, on the clock the task now
        # has. The original's time is ``source_start`` once a record has been
        # through a cut: a re-render moves the transcript back to the
        # original's clock and cuts again, and converting the time the last
        # cut gave would cut it twice (review, 2026-10-25).
        result[records_key] = [
            {
                **record,
                "start": cut_timeline.new_time(ranges, _seconds(_original_time(record, "start"))),
                "end": cut_timeline.new_time(ranges, _seconds(_original_time(record, "end"))),
                # For the copies kept on the original's clock (``source_*``).
                "source_start": _original_time(record, "start"),
                "source_end": _original_time(record, "end"),
            }
            for record in result[records_key] if isinstance(record, dict)
        ]
    result["transcript_media"] = TRANSCRIPT_MEDIA_CUT
    result["playback_media_kind"] = DEBREATH_MEDIA_KIND
    return result


Decide = Callable[[Path, CutPlan, list[dict[str, Any]]], tuple[CutPlan, dict[str, Any]]]


@model_usage.works_for(task_steps.CUT)
def survey_and_cut(
    task_id: str, client_id: str | None = None, *, chat: tuple[Chat, str] | None = None,
    on_plan: Callable[[list[cut_timeline.KeptRange]], None] | None = None,
    decide: Decide | None = None, judged_by: str | None = None, extra: dict[str, Any] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    """Judge the recording from its transcript, cut it once, and move the
    transcript onto the cut. Returns what was recorded, or None when there was
    nothing to do. Never raises: the transcript of the original stands.

    ``on_plan`` gets the stretches the cut will keep as soon as they are
    decided, before the render, when there is something to cut: the note is
    written from them while the cut renders (to-do batch three).

    ``decide`` stands in for the model when the judgement was made elsewhere
    (the user's own agent, ``agent_cut``): it gets the loudness plan with the
    spoken words already protected and returns the plan to render and what was
    decided. ``judged_by`` names that judge; ``extra`` goes into the record.

    A decision is kept on the task before the render (``DECIDED_KEY``): a cut
    stopped or cut off by a restart while rendering renders that decision
    again instead of asking a model again (decision 6.42). ``runner`` runs the
    cut's commands (a ``TaskWatch`` runner, so stopping the task stops them)."""
    job = job_store.get_job(task_id, client_id=client_id)
    result = dict((job or {}).get("result") or {})
    if not job or str(result.get("transcript_media") or "") == TRANSCRIPT_MEDIA_CUT:
        return None
    if debreath_job.debreath_state(result).get("used_for_transcription"):
        return None
    segments = _texted(result.get("raw_segments"))
    words = speech_spans(result)
    try:
        source = debreath_job.resolve_source(task_id)
    except debreath_job.DebreathError:
        return None

    stored = (debreath_job.debreath_state(result).get(DECIDED_KEY) or None) if decide is None else None
    if isinstance(stored, dict) and stored.get("cuts") is not None:
        decide, judged_by = _replay(stored), stored.get("judged_by")
        extra = {**(extra or {}), "replayed_decision": True}
    judge = None if decide is not None else _judge(chat)
    asker = (judge.text, judge.name) if judge else None
    record: dict[str, Any] = {"judged_by": judged_by if decide is not None else (judge.name if judge else None), **(extra or {})}
    final: dict[str, CutPlan] = {}

    def revise(media: Path, plan: CutPlan) -> CutPlan:
        spoken, protection = spoken_words(media, plan, words)
        plan, kept = protect_words(plan, spoken)
        record["word_protection"] = {**protection, **kept}
        texts = word_texts(result)
        record["spoken"] = [
            {"start": round(a, 2), "end": round(b, 2),
             # Only the words this stretch saved, not the whole line around it.
             "said": "".join(t for ws, we, t in texts if we > a and ws < b)[:30]}
            for a, b in spoken
        ]
        if decide is not None:
            try:
                revised, decided = decide(media, plan, segments)
                record.update(decided)
            except Exception as exc:  # noqa: BLE001 - the volume plan stands
                logger.warning("decision for %s not applied: %s", task_id, exc)
                record["error"] = str(exc)[:300]
                revised = plan
            final["plan"] = revised
            _tell_plan(revised)
            return revised
        if asker is None or not segments:
            record["gaps"] = describe_gaps(list(plan.cuts), {}, {})
            record["skipped"] = "没有可用的模型或转录，只按音量剪。"
            final["plan"] = plan
            _tell_plan(plan)
            return plan
        try:
            revised, decided = revise_plan(
                media, plan, segments, asker[0], cases=cut_cases.as_text(cut_cases.collect()),
                look=judge.ask if judge and judge.sees_images else None,
            )
            record.update(decided)
        except Exception as exc:  # noqa: BLE001 - the volume plan stands
            logger.warning("survey for %s not used: %s", task_id, exc)
            record["error"] = str(exc)[:300]
            revised = plan
        final["plan"] = revised
        _tell_plan(revised)
        return revised

    def _tell_plan(plan: CutPlan) -> None:
        _remember_decision(task_id, client_id, plan, record)
        if on_plan is None or not plan.cuts:
            return
        try:
            on_plan(cut_timeline.kept_ranges(plan.as_dict()))
        except Exception as exc:  # noqa: BLE001 - the cut goes on regardless
            logger.warning("could not hand the plan of %s on: %s", task_id, exc)

    runner = runner or task_stop.bound_runner()
    try:
        debreath_job.run_debreath(task_id, client_id=client_id, revise=revise,
                                  **({"runner": runner} if runner is not None else {}))
    except Exception as exc:  # noqa: BLE001 - the original's transcript stands
        logger.warning("survey cut for %s failed: %s", task_id, exc)
        record["render_error"] = str(exc)[:300]
        _record(task_id, client_id, record)
        return record

    if not _finish(task_id, client_id, source, final.get("plan"), record, asker):
        _record(task_id, client_id, record)
    return record


# The decision a cut renders, kept on the task until the render is done.
DECIDED_KEY = "survey_decided"


def _remember_decision(task_id: str, client_id: str | None, plan: CutPlan, record: dict[str, Any]) -> None:
    """Keep what was decided before rendering it: the stretches to cut and
    what the judge said. Best effort; a cut that cannot keep it renders all
    the same, and is judged again if it has to start over."""
    try:
        kept = json.loads(json.dumps({
            "cuts": [[round(c.start, 3), round(c.end, 3)] for c in plan.cuts],
            "judged_by": record.get("judged_by"),
            "record": {k: v for k, v in record.items() if k not in ("word_protection", "spoken")},
        }, ensure_ascii=False, default=str))
        debreath_job._store(task_id, client_id=client_id, state={DECIDED_KEY: kept})
    except Exception:  # noqa: BLE001 - the render goes ahead regardless
        logger.warning("could not keep the cut decision of %s", task_id, exc_info=True)


def _replay(stored: dict[str, Any]) -> Decide:
    """Stands in for the judge with a decision already made."""
    def decide(media: Path, plan: CutPlan, segments: Any) -> tuple[CutPlan, dict[str, Any]]:
        cuts = [TimeRange(float(a), float(b)) for a, b in stored.get("cuts") or []]
        duration = plan.source_duration_seconds
        replayed = replace(plan, cuts=cuts, keeps=silence_cuts._keeps_between(duration, cuts, silence_cuts.MIN_KEEP_SECONDS),
                           warnings=list(plan.warnings))
        return replayed, dict(stored.get("record") or {})
    return decide


def _finish(
    task_id: str, client_id: str | None, source: Path, plan: CutPlan | None, record: dict[str, Any],
    chat: tuple[Chat, str] | None = None,
) -> bool:
    """After a render: the transcript onto the cut's clock, the cut into the
    user's folder, the survey and its report (``cut_report``) recorded. False
    when nothing was rendered."""
    job = job_store.get_job(task_id, client_id=client_id) or {}
    before = _snapshot(job)
    result = copy.deepcopy(before)
    state = debreath_job.debreath_state(result)
    if plan is not None and not plan.cuts and state.get("status") == debreath_job.STATUS_COMPLETED:
        _finish_uncut(task_id, client_id, before, result, state, plan, record, chat)
        return True
    media = debreath_job.cut_file_on_disk(task_id, result)
    if not state.get("rendered") or plan is None or media is None:
        return False
    result = _onto_cut_clock(result, cut_timeline.kept_ranges(plan.as_dict()))
    record["report"] = build_report(record, _texted(result.get("source_raw_segments")), chat, result)
    record["report"]["after_seconds"] = round(plan.kept_seconds, 1)
    record["report"]["before_seconds"] = round(plan.source_duration_seconds, 1)
    delivered: dict[str, Any] = {}
    if not is_managed_path(source):
        delivered = output_folder.deliver_media(task_id, source, media, output_folder.CUT_LABEL)
    # The transcript is on the cut's clock now, exactly as when the cut came
    # first; the result page and the re-cut guard read this flag for that.
    state.pop(DECIDED_KEY, None)
    result["debreath"] = {**state, **delivered, "ran_before_transcription": True, "survey": record}
    from backend.core.result_artifacts import _attach_result_artifacts

    result = _attach_result_artifacts(task_id, result, parts=("transcript",))
    _save(task_id, client_id, before, result)
    return True


def judged_by_agent(record: dict[str, Any]) -> bool:
    return str(record.get("judged_by") or "").startswith("agent")


@model_usage.part_of(task_steps.CUT, "整理处理报告")
def build_report(
    record: dict[str, Any], segments: list[dict[str, Any]], chat: tuple[Chat, str] | None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The processing report, organised by whoever judged the cut: the user's
    agent organises its own (``agent_cut``; kept by item key, so it survives
    the report being written again after a change), FluentFlow's model its
    own. A cut the agent judged is never organised by FluentFlow's model:
    until the agent's arrives the facts are grouped by kind."""
    if result is not None and not record.get("agent_report"):
        sent = (result.get("agent_cut") or {}).get("report") if isinstance(result.get("agent_cut"), dict) else None
        if isinstance(sent, dict):
            record["agent_report"] = sent
    all_facts = cut_report.facts(record, segments)
    agent = record.get("agent_report")
    if isinstance(agent, dict) and agent.get("sections") and all_facts:
        return cut_report.from_keyed(all_facts, agent, str(record.get("judged_by") or "agent"))
    if judged_by_agent(record):
        chat = None
    return cut_report.organize(all_facts, chat, cut_cases.as_text(cut_cases.collect()))


def _still_our_cut(task_id: str, result: dict[str, Any], state: dict[str, Any], delivered: Path) -> bool:
    """Whether the file at the delivered path is, right now, the same file as
    the cut the task keeps. The record that it was delivered as a hard link is
    not enough: the user may since have put another video there (read-only
    review, 2026-10-16)."""
    if not delivered.name or not delivered.is_file():
        return False
    record = (result.get("artifacts") or {}).get(DEBREATH_MEDIA_KIND) or {}
    filename = record.get("filename") or state.get("media_filename")
    kept = resolve_artifact_file(_artifact_storage_dir() / task_id, filename) if filename else None
    try:
        return kept is not None and os.path.samefile(kept, delivered)
    except OSError:
        return False


def _finish_uncut(
    task_id: str, client_id: str | None, before: dict[str, Any], result: dict[str, Any], state: dict[str, Any], plan: CutPlan,
    record: dict[str, Any], chat: tuple[Chat, str] | None,
) -> None:
    """Nothing is left to cut (every cut was put back, or there was none): the
    original is the video to watch and the transcript stays on its clock. A
    cut delivered earlier is taken out of the user's folder when it is only a
    second name for the file the task keeps; a copy is left alone."""
    record["report"] = build_report(record, _texted(result.get("raw_segments")), chat, result)
    record["report"]["after_seconds"] = record["report"]["before_seconds"] = round(plan.source_duration_seconds, 1)
    delivered = Path(str(state.get("delivered_path") or ""))
    if _still_our_cut(task_id, result, state, delivered):
        try:
            delivered.unlink()
        except OSError as exc:
            logger.warning("could not take the old cut %s out of the folder: %s", delivered, exc)
    for key in ("delivered", "delivered_path", "delivered_name", "delivered_as", "media_filename", "render", "render_verified", DECIDED_KEY):
        state.pop(key, None)
    result["transcript_media"] = TRANSCRIPT_MEDIA_SOURCE
    result.pop("playback_media_kind", None)
    result["debreath"] = {
        **state, "rendered": False, "used_for_transcription": False, "survey": record,
    }
    from backend.core.result_artifacts import _attach_result_artifacts

    _save(task_id, client_id, before, _attach_result_artifacts(task_id, result, parts=("transcript",)))


def _record(task_id: str, client_id: str | None, record: dict[str, Any]) -> None:
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        return
    before = _snapshot(job)
    result = copy.deepcopy(before)
    result["debreath"] = {**debreath_job.debreath_state(result), "survey": record}
    _save(task_id, client_id, before, result)


def _snapshot(job: dict[str, Any] | None) -> dict[str, Any]:
    """The result as read, kept apart from the copy a step changes, so that
    ``_save`` writes only what the step changed."""
    return copy.deepcopy(dict((job or {}).get("result") or {}))


def _save(task_id: str, client_id: str | None, before: dict[str, Any], result: dict[str, Any]) -> None:
    # The note may be written while the cut renders and finishes; only the
    # fields this step changed are written.
    job_store.save_result_changes(task_id, before, result, client_id=client_id)


class ReviewError(RuntimeError):
    """A confirmation that cannot be taken, in the user's words."""


def _cut_list_on_disk(task_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """The cut list the cut on disk was made from: a render writes its own
    only after it succeeded."""
    cut_list = (result.get("artifacts") or {}).get(debreath_job.CUT_LIST_KIND)
    path = resolve_artifact_file(_artifact_storage_dir() / task_id, (cut_list or {}).get("filename")) if cut_list else None
    return json.loads(path.read_text(encoding="utf-8")) if path is not None else None


def _back_onto_source(segments: list[Any], ranges: list[cut_timeline.KeptRange]) -> list[dict[str, Any]]:
    moved = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = cut_timeline.source_time(ranges, _seconds(seg.get("start")))
        end = cut_timeline.source_time(ranges, _seconds(seg.get("end")), end=True)
        if start is None or end is None:
            continue
        line = {**seg, "start": start, "end": max(start, end)}
        line.pop("words", None)  # the words were timed for the text before the edit
        moved.append(line)
    return moved


def _keep_edits(task_id: str, result: dict[str, Any]) -> None:
    """Subtitles the user edited on the cut, put back onto the original's
    clock as the lines a re-render starts from. Without this the re-render
    starts from the lines as they were transcribed and the edits are gone
    while the task still says it was edited. Lines the cut had taken out
    whole were never in front of the user; they come along as transcribed, so
    a stretch put back has its words."""
    if not result.get("transcript_edited") or str(result.get("transcript_media") or "") != TRANSCRIPT_MEDIA_CUT:
        return
    plan = _cut_list_on_disk(task_id, result)
    ranges = cut_timeline.kept_ranges(plan) if plan else []
    if not ranges:
        return
    for key in ("display_segments", "raw_segments"):
        if not isinstance(result.get(key), list):
            continue
        cut_away = [
            seg for seg in result.get(f"source_{key}") or []
            if isinstance(seg, dict) and not cut_timeline.remap_segments([seg], ranges).segments
        ]
        lines = _back_onto_source(result[key], ranges) + cut_away
        result[f"source_{key}"] = sorted(lines, key=lambda seg: (_seconds(seg.get("start")), _seconds(seg.get("end"))))


def _rerender(
    task_id: str, client_id: str | None, before: dict[str, Any], result: dict[str, Any], survey: dict[str, Any],
    kept: list[dict[str, Any]], removals: list[dict[str, Any]], after: Callable[[dict[str, Any]], None],
    *, claimed: bool = False,
) -> dict[str, Any]:
    """Render the cut again from the original with ``kept`` and ``removals``
    as the decisions, without asking the model again; the transcript is moved
    from the original's clock onto the new cut, the report written again.
    ``after`` updates the survey record once the render succeeded. Blocking.

    The decisions the cut on disk was made with are kept aside first; a render
    that fails, or a service stopped half way (``recover_interrupted``), puts
    them and the transcript back as they were (owner, 2026-10-15)."""
    source = original_locator.locate(task_id, client_id)
    survey["before_rerender"] = {
        "removed": survey.get("removed") or [], "kept_gaps": survey.get("kept_gaps") or [],
        "was_cut": str(result.get("transcript_media") or "") == TRANSCRIPT_MEDIA_CUT,
    }
    survey.update({"removed": removals, "kept_gaps": kept})
    if str(result.get("transcript_media") or "") == TRANSCRIPT_MEDIA_CUT:
        _keep_edits(task_id, result)
        # Back onto the original's clock, so the render remaps from there. (A
        # task with nothing cut is on that clock already.)
        for key in SEGMENT_KEYS:
            if isinstance(result.get(f"source_{key}"), list):
                result[key] = result[f"source_{key}"]
        _records_onto_source_clock(result)
    result["transcript_text"] = _lines_text(result.get("raw_segments"))
    if result.get("corrected_segments"):
        result["corrected_transcript_text"] = _lines_text(result["corrected_segments"])
    result["transcript_media"] = TRANSCRIPT_MEDIA_SOURCE
    result["debreath"] = {**debreath_job.debreath_state(result), "survey": survey, "used_for_transcription": False}
    _save(task_id, client_id, before, result)

    final: dict[str, CutPlan] = {}
    words = speech_spans(result)

    def revise(media: Path, plan: CutPlan) -> CutPlan:
        spoken, _ = spoken_words(media, plan, words)
        final["plan"] = apply_decisions(protect_words(plan, spoken)[0], kept, removals)
        return final["plan"]

    try:
        debreath_job.run_debreath(task_id, client_id=client_id, revise=revise, claimed=claimed)
    except Exception as exc:
        put_back(task_id, client_id, f"重新导出没成功，剪后视频还是原来那份：{str(exc)[:200]}")
        raise
    survey.pop("rerendering", None)
    survey.pop("restore_error", None)
    survey.pop("before_rerender", None)
    after(survey)
    _finish(task_id, client_id, source, final.get("plan"), survey, _default_chat())
    return survey


def put_back(task_id: str, client_id: str | None, message: str) -> bool:
    """Return a task whose re-render did not finish to the cut it still has:
    the decisions it was made with, the transcript on its clock, the flags that
    say it is cut. False when there was nothing to put back."""
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        return False
    before_read = _snapshot(job)
    result = copy.deepcopy(before_read)
    state = debreath_job.debreath_state(result)
    survey = dict(state.get("survey") or {})
    if not survey.get("rerendering") and not survey.get("before_rerender"):
        return False
    before = survey.pop("before_rerender", None) or {}
    survey.update({key: before[key] for key in ("removed", "kept_gaps") if key in before})
    survey.pop("rerendering", None)
    survey["restore_error"] = message
    if not before.get("was_cut", True):
        # Nothing was cut before either: the transcript is on the original's
        # clock, as it should stay.
        result["debreath"] = {
            **state, "survey": survey, "status": debreath_job.STATUS_COMPLETED, "stage": "done", "error": None,
        }
        _save(task_id, client_id, before_read, result)
        return True
    plan = _cut_list_on_disk(task_id, result)
    if plan is not None and str(result.get("transcript_media") or "") != TRANSCRIPT_MEDIA_CUT:
        result = _onto_cut_clock(result, cut_timeline.kept_ranges(plan))
    result["debreath"] = {
        **state, "survey": survey, "status": debreath_job.STATUS_COMPLETED, "stage": "done", "error": None,
        "used_for_transcription": True, "ran_before_transcription": True,
    }
    _save(task_id, client_id, before_read, result)
    return True


def recover_interrupted() -> int:
    """At startup: put back every re-render a stopped service left half done."""
    recovered = 0
    for job in job_store.list_jobs_by_statuses(("completed",), include_result=True):
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        survey = debreath_job.debreath_state(result).get("survey") or {}
        if isinstance(survey, dict) and survey.get("rerendering"):
            if put_back(str(job.get("task_id")), job.get("client_id"), "上次重新导出被打断了（服务重启），剪后视频还是原来那份，可以再点一次。"):
                recovered += 1
    return recovered


def _mark_gaps(survey: dict[str, Any], start: float, end: float, kept: bool, reason: str) -> None:
    for gap in (survey.get("gaps") or {}).get("long") or []:
        if _overlaps(TimeRange(_seconds(gap["start"]), _seconds(gap["end"])), start, end):
            gap.update({"kept": kept, "reason": reason})


def _report_item(survey: dict[str, Any], ref: str) -> dict[str, Any] | None:
    for section in (survey.get("report") or {}).get("sections") or []:
        for item in section.get("items") or []:
            if item.get("ref") == ref:
                return item
    return None


def _turn_around(
    task_id: str, client_id: str | None, before: dict[str, Any], result: dict[str, Any], survey: dict[str, Any],
    item: dict[str, Any], change: dict[str, Any] | None, *, undoing: int | None = None, claimed: bool = False,
) -> dict[str, Any]:
    """Render again with ``item``'s decision turned around: what it says was
    cut is put back, what it says was kept is cut."""
    start, end = _seconds(item["start"]), _seconds(item["end"])
    removals = [dict(r) for r in survey.get("removed") or []]
    kept = [dict(k) for k in survey.get("kept_gaps") or []]
    now_removed = item.get("action") != "removed"
    if now_removed:
        kept = [k for k in kept if not _overlaps(TimeRange(_seconds(k["start"]), _seconds(k["end"])), start, end)]
        if item.get("kind") != "long_gap":
            removals.append({"start": start, "end": end, "reason": item.get("reason") or "你说这段应该剪掉。"})
    else:
        removals = [r for r in removals if not _overlaps(TimeRange(_seconds(r["start"]), _seconds(r["end"])), start, end)]
        if item.get("kind") not in ("removed_talk", "quiet_speech"):
            kept.append({"start": start, "end": end, "reason": "你说这段不该剪。"})
    survey["rerendering"] = True

    def after(record: dict[str, Any]) -> None:
        changes = list(record.get("user_changes") or [])
        if change is not None:
            changes.append(change)
        if undoing is not None and 0 <= undoing < len(changes):
            changes[undoing] = {**changes[undoing], "undone_at": _now()}
        record["user_changes"] = changes
        if undoing is not None:
            _mark_gaps(record, start, end, not now_removed, item.get("reason") or "")
        else:
            _mark_gaps(record, start, end, not now_removed, "你说这段应该剪掉。" if now_removed else "你说这段不该剪，已放回。")

    return _rerender(task_id, client_id, before, result, survey, kept, removals, after, claimed=claimed)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _task_survey(task_id: str, client_id: str | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The result as read, a copy to change, and its survey record."""
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        raise ReviewError("任务不存在")
    before = _snapshot(job)
    result = copy.deepcopy(before)
    survey = dict(debreath_job.debreath_state(result).get("survey") or {})
    if not survey.get("report"):
        raise ReviewError("这个任务没有处理说明")
    return before, result, survey


def flip(task_id: str, ref: str, client_id: str | None = None, *, claimed: bool = False) -> dict[str, Any]:
    """Turn one decision in the processing report around: what was cut is put
    back, what was kept is cut. The change is the user's feedback and is kept
    with what the model had decided (``user_changes``, the report's bin, where
    it can be undone later), to measure its judgement and as material for the
    case library. Blocking."""
    before, result, survey = _task_survey(task_id, client_id)
    item = _report_item(survey, ref)
    if item is None or item.get("start") is None or item.get("end") is None:
        raise ReviewError("这一条不能单独改")
    change = {
        "key": item.get("key"), "kind": item.get("kind"), "start": _seconds(item["start"]), "end": _seconds(item["end"]),
        "model_said": item.get("action"), "user_said": "kept" if item.get("action") == "removed" else "removed",
        "model_reason": item.get("text"), "at": _now(),
        **{key: item.get(key) for key in ("picture", "before", "after", "said") if item.get(key)},
    }
    return _turn_around(task_id, client_id, before, result, survey, item, change, claimed=claimed)


def undo_change(task_id: str, index: int, client_id: str | None = None, *, claimed: bool = False) -> dict[str, Any]:
    """Undo one of the user's earlier changes (from the report's bin): the
    model's decision for that stretch is applied again and the cut rendered
    again. Blocking."""
    before, result, survey = _task_survey(task_id, client_id)
    changes = survey.get("user_changes") or []
    if not 0 <= index < len(changes) or changes[index].get("undone_at"):
        raise ReviewError("没有这次改动，或者已经撤销过了")
    change = changes[index]
    item = {
        "kind": change.get("kind"), "start": change["start"], "end": change["end"],
        "action": change.get("user_said"), "reason": change.get("model_reason"),
    }
    return _turn_around(task_id, client_id, before, result, survey, item, None, undoing=index, claimed=claimed)


VERDICTS = {"agree", "pending"}


def verdict(task_id: str, keys: list[str], value: str, client_id: str | None = None) -> dict[str, Any]:
    """Record that the user agrees with these items of the report as they
    are ("剪得对" / "确实该保留"), or take that back (``pending``). An item
    nobody answered counts as not looked at."""
    if value not in VERDICTS or not keys:
        raise ReviewError("回答只能是认可或撤回")
    before, result, survey = _task_survey(task_id, client_id)
    items = {
        item.get("key"): item
        for section in survey["report"].get("sections") or [] for item in section.get("items") or []
    }
    verdicts = dict(survey.get("verdicts") or {})
    for key in keys:
        item = items.get(key)
        if item is None:
            continue
        if value == "pending":
            verdicts.pop(key, None)
        else:
            verdicts[key] = {
                "verdict": "agree", "kind": item.get("kind"), "model_said": item.get("action"),
                "start": item.get("start"), "end": item.get("end"), "text": item.get("text"), "at": _now(),
                **{key: item.get(key) for key in ("picture", "before", "after", "said") if item.get(key)},
            }
    survey["verdicts"] = verdicts
    result["debreath"] = {**debreath_job.debreath_state(result), "survey": survey}
    _save(task_id, client_id, before, result)
    return {"verdicts": verdicts}


__all__ = [
    "flip",
    "undo_change",
    "verdict",
    "ReviewError",
    "survey_and_cut",
    "revise_plan",
    "build_timeline",
    "removal_range",
]
