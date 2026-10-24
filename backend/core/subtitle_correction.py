"""Fix misheard words in the transcript from the subtitles burned into the picture.

Requirement (owner, 2026-10-24): the 3.5-hour Luo Fuli interview came back with
many names and model names marked 「（存疑）」 in the note, while the video
itself carried exact subtitles. At 1:22:58 of the cut the picture read
「训练的应该是GLM-5跟Kimi」, the transcript 「应该是GOM跟KIMI」, the note
「GOM（存疑）」. The fix goes into the transcript, not the note: the transcript
is what the note, the subtitle files and the follow-along page all read, so one
correction reaches all of them.

Only the doubtful places are read, not the whole film frame by frame:

1. Is there a burned-in subtitle at all? A handful of sentences spread over the
   recording, one frame each, only the bottom band of the picture. When too few
   of them read back as the sentence the transcript has at that moment, the
   whole step is skipped. Audio-only material and machines without text
   recognition (Windows) skip it too; nothing here can fail a task.
2. A model reads the transcript and names the words likely misheard: names,
   products, English, numbers, sentences that do not read.
3. For each, a few frames across that sentence, bottom band only. A recognised
   line is kept only when it reads largely as the sentence itself: the
   recogniser garbles text (``frame_text`` records 如何监督 read as 如們血苔),
   and a garbled line, or the chapter bar many videos run along the bottom, is
   not a source of spellings.
4. The model compares and replaces a word only when a kept subtitle line spells
   it out. The code holds it to that: the replaced text has to be in the
   sentence and the new text has to be in a subtitle line as it was read.

Every change is recorded (old and new text, time, the subtitle line it rests on)
and can be undone through ``revert``. The speech recogniser's own output
(``stt_raw_segments``) is never touched.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from backend.core import frame_text

logger = logging.getLogger(__name__)

Chat = Callable[[str, str], str]

# The bottom share of the picture that is read. Burned-in subtitles sit in the
# lowest fifth on the material seen so far; a third leaves room for two-line
# subtitles and for videos that put a chapter bar under them.
SUBTITLE_BAND = 0.35

# Detection: sentences sampled, and how many must read back for the video to
# count as subtitled. Three out of ten tolerates sentences whose subtitle is
# already off screen at the transcript's time, and still rejects a talking-head
# recording, which reads nothing of the sentence at all.
PROBE_SENTENCES = 10
PROBE_NEEDED = 3

# How much of a recognised line has to occur, in order, in the transcript
# around that moment for the line to count as its subtitle (``coverage``). A
# line that carries the right spelling of a misheard word still shares the rest
# of the sentence: 「训练的应该是GLM-5跟Kimi」 against 「应该是GOM跟KIMI」
# shares 应该是…跟kimi. Recogniser garble and the chapter bar share a stray
# character or two.
PROBE_COVERAGE = 0.6
EVIDENCE_COVERAGE = 0.6
MIN_LINE_CHARS = 4

# A transcript chunk the suspect model reads at once, and a cap on how many
# places are looked up. The cap is a runaway guard, not a budget: the 3.5-hour
# Luo Fuli interview had 1117 doubtful places, about one sentence in six, nearly
# all real mishearings, and an earlier cap of 300 silently covered only its
# first hour.
SUSPECT_CHUNK_CHARS = 6_000
MAX_SUSPECTS = 1500
# Frames are read six at a time (ffmpeg plus Vision, local); model requests
# ten at a time, because each waits on the network, not on this machine.
WORKERS = 6
MODEL_WORKERS = 10
DECIDE_BATCH = 40
FRAME_TIMEOUT_SECONDS = 20

_OFF = {"0", "false", "no", "off", "disabled"}
_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


def enabled() -> bool:
    """On unless ``FLUENTFLOW_SUBTITLE_CORRECTION`` turns it off (owner,
    2026-10-24): a video without subtitles costs a few seconds."""
    return (os.environ.get("FLUENTFLOW_SUBTITLE_CORRECTION") or "").strip().lower() not in _OFF


@dataclass
class SubtitleCorrectionResult:
    status: str
    segments: list[dict[str, Any]]
    corrections: list[dict[str, Any]] = field(default_factory=list)
    checked: int = 0
    matched: int = 0
    suspects: int = 0
    looked_up: int = 0
    seconds: float = 0.0
    reason: str | None = None
    phase_seconds: dict[str, float] = field(default_factory=dict)

    def meta(self, channel: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status,
            "probe_checked": self.checked,
            "probe_matched": self.matched,
            "suspects": self.suspects,
            "looked_up": self.looked_up,
            "applied": len(self.corrections),
            "seconds": round(self.seconds, 1),
            "phase_seconds": {key: round(value, 1) for key, value in self.phase_seconds.items()} or None,
            "channel": channel,
            "reason": self.reason,
        }
        return {key: value for key, value in data.items() if value not in (None, "")}


def _key(text: str) -> str:
    return _STRIP.sub("", text or "").lower()


def coverage(line: str, sentence: str) -> float:
    """How much of the shorter of the two occurs, in order, in the longer.

    Measured against the shorter because neither side bounds the other: a
    subtitle line often holds two transcript sentences (「那么就回到了另外一个
    命题」 against 「那么就回到了」), and a transcript sentence often spans two
    subtitle lines.
    """
    a, b = _key(line), _key(sentence)
    if not a or not b:
        return 0.0
    matched = sum(block.size for block in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return matched / min(len(a), len(b))


def _has_video(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_name,disposition",
             "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=15,
        )
        streams = json.loads(out.stdout or "{}").get("streams") or []
    except (subprocess.SubprocessError, ValueError, OSError):
        return False
    # Cover art in an audio file is a video stream too.
    return any(not (s.get("disposition") or {}).get("attached_pic") for s in streams)


def read_band(video: Path, at: float, workdir: Path) -> list[str]:
    """The text lines in the bottom band of the frame at ``at`` seconds."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    out = workdir / f"band_{uuid.uuid4().hex}.png"
    try:
        subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-ss", f"{max(at, 0):.3f}", "-i", str(video), "-frames:v", "1",
             "-vf", f"crop=iw:ih*{SUBTITLE_BAND}:0:ih*{1 - SUBTITLE_BAND}", str(out)],
            check=True, capture_output=True, timeout=FRAME_TIMEOUT_SECONDS,
        )
        if not out.is_file():
            return []
        return list(frame_text.read_frame(out).lines)
    except (subprocess.SubprocessError, OSError):
        return []
    finally:
        out.unlink(missing_ok=True)


def _text(segment: dict[str, Any]) -> str:
    return str(segment.get("text") or "").strip()


def _seconds(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probe_indices(segments: list[dict[str, Any]], count: int) -> list[int]:
    usable = [
        i for i, seg in enumerate(segments)
        if len(_key(_text(seg))) >= 6
        and (_seconds(seg.get("start")) is not None and _seconds(seg.get("end")) is not None)
        and _seconds(seg.get("end")) - _seconds(seg.get("start")) >= 1.0
    ]
    if len(usable) <= count:
        return usable
    step = len(usable) / count
    return [usable[int(step * k + step / 2)] for k in range(count)]


def _around(segments: list[dict[str, Any]], index: int, reach: int = 3) -> str:
    """The sentence with its neighbours. Burned-in subtitles and the
    transcript's clock drift apart by a few seconds: on the Luo Fuli interview
    the picture at 2:32:19 still showed the sentence the transcript had
    started four seconds earlier."""
    return "".join(_text(segments[j]) for j in range(index - reach, index + reach + 1) if 0 <= j < len(segments))


def _mid(segment: dict[str, Any]) -> float:
    return (_seconds(segment.get("start")) + _seconds(segment.get("end"))) / 2


def detect(video: Path, segments: list[dict[str, Any]], workdir: Path, read: Callable = read_band) -> tuple[bool, int, int]:
    """Whether the picture carries the transcript as subtitles: (yes, checked, matched)."""
    checked = matched = 0
    indices = _probe_indices(segments, PROBE_SENTENCES)
    for position, index in enumerate(indices):
        if matched >= PROBE_NEEDED or matched + (len(indices) - position) < PROBE_NEEDED:
            break
        seg = segments[index]
        checked += 1
        lines = read(video, _mid(seg), workdir)
        nearby = _around(segments, index)
        if any(len(_key(line)) >= MIN_LINE_CHARS and coverage(line, nearby) >= PROBE_COVERAGE for line in lines):
            matched += 1
    return matched >= PROBE_NEEDED, checked, matched


_SUSPECT_SYSTEM = """你会收到一段语音识别转录稿，每行前面是句号 [编号]。语音识别常听错：人名、公司名、产品名、模型名、英文单词和缩写、数字和版本号，以及读不通的词。

找出这些可能听错的地方。只挑真的可疑的；读得通、常见的词不要挑。每处给出它在那一行里原样出现的文字（不改写、不加标点），尽量短，只框住可疑的那个词或短语。

只输出 JSON：{"suspects": [{"i": 编号, "text": "原样文字"}]}。没有就输出 {"suspects": []}。"""


_DECIDE_SYSTEM = """你会收到转录稿里几处可能听错的词。每处给出：那一句转录（sentence）和它的前一句（before）、后一句（after），可疑的词（suspect），以及这句话前后几秒从视频画面上识别出的字幕行（subtitles）。字幕和转录的时间可能差几秒，字幕行也可能是前后句的。字幕是视频作者做的，通常写对了，但识别时偶尔会读错个别字。

逐处判断：只有当字幕行明显就是这句话、并且清楚地给出了可疑词的正确写法时，才替换。
- from：sentence 里原样出现的一段文字，只框住要改的部分，尽量短。
- to：从字幕行里原样抄下来的写法，不自己改写、不加字。
- 字幕和转录只是说法不同（语气词、断句、同义词），不改。只换听错的词，不按字幕改写整句。
- 字幕识别会把大写 I 读成小写 l、把 0 读成 O；这种情况按常识写对（OpenAl 写成 OpenAI）。
- 字幕看不出这个词、或字幕行本身像识别乱码，不改。
- 字幕是繁体时，只改专有名词和英文，不把简体字改成繁体。

只输出 JSON：{"fixes": [{"id": 编号, "from": "...", "to": "..."}]}。不改的不要列出。"""


_LOOKALIKE = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"})


def _loose(text: str) -> str:
    """Text compared the way the recogniser confuses it: case, spacing, and
    the letters it swaps (it read OpenAI off the Luo Fuli interview as
    OpenAl). The model may write OpenAI from a line read as OpenAl; it still
    cannot write a spelling the line does not carry."""
    return re.sub(r"\s+", "", text or "").lower().translate(_LOOKALIKE)


def _json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _chunks(segments: list[dict[str, Any]]) -> list[str]:
    chunks: list[str] = []
    lines: list[str] = []
    size = 0
    for i, seg in enumerate(segments):
        text = _text(seg)
        if not text:
            continue
        line = f"[{i}] {text}"
        if lines and size + len(line) > SUSPECT_CHUNK_CHARS:
            chunks.append("\n".join(lines))
            lines, size = [], 0
        lines.append(line)
        size += len(line) + 1
    if lines:
        chunks.append("\n".join(lines))
    return chunks


def pick_suspects(segments: list[dict[str, Any]], chat: Chat) -> list[dict[str, Any]]:
    """The places the model thinks were misheard, each held to text that is
    really in its sentence."""
    def ask(chunk: str) -> list[dict[str, Any]]:
        try:
            return list(_json_object(chat(_SUSPECT_SYSTEM, chunk)).get("suspects") or [])
        except Exception as exc:  # noqa: BLE001 - one chunk failing loses its suspects only
            logger.warning("Suspect pass failed on a chunk: %s", exc)
            return []

    with ThreadPoolExecutor(max_workers=MODEL_WORKERS) as pool:
        answers = list(pool.map(ask, _chunks(segments)))
    found: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in (x for answer in answers for x in answer if isinstance(x, dict)):
        try:
            index = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        word = str(item.get("text") or "").strip()
        if not (0 <= index < len(segments)) or not word or word not in _text(segments[index]):
            continue
        if (index, word) in seen:
            continue
        seen.add((index, word))
        found.append({"index": index, "word": word})
    return found


def _times(seg: dict[str, Any]) -> list[float]:
    """Frames across the sentence and a little either side of it, for the
    drift described in ``_around``."""
    start, end = _seconds(seg.get("start")), _seconds(seg.get("end"))
    inside = [(start + end) / 2] if end - start < 1.2 else [start + 0.3, (start + end) / 2, end - 0.3]
    return [max(start - 2.0, 0.0), *inside, end + 2.0]


def gather(
    video: Path, segments: list[dict[str, Any]], indices: list[int], workdir: Path, read: Callable = read_band,
) -> dict[int, list[str]]:
    """For each sentence, the recognised lines that read as the transcript
    around it and share something with the sentence itself."""
    jobs = [(index, round(at * 2) / 2) for index in indices for at in _times(segments[index])]
    # Doubtful sentences cluster (a name misheard three times in a minute), so
    # many of their frames are the same frame; each is read once.
    moments = sorted({at for _index, at in jobs})
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        read_at = dict(zip(moments, pool.map(lambda at: read(video, at, workdir), moments)))
    evidence: dict[int, list[str]] = {index: [] for index in indices}
    for index, at in jobs:
        lines = read_at[at]
        context = _around(segments, index)
        own = _text(segments[index])
        for line in lines:
            line = line.strip()
            if len(_key(line)) < MIN_LINE_CHARS or line in evidence[index]:
                continue
            # The line has to read as this sentence, and share something with
            # it in particular, not only with a neighbour.
            if coverage(line, context) >= EVIDENCE_COVERAGE and coverage(line, own) >= 0.3:
                evidence[index].append(line)
    return evidence


def decide(
    segments: list[dict[str, Any]], suspects: list[dict[str, Any]], evidence: dict[int, list[str]], chat: Chat,
) -> list[dict[str, Any]]:
    """The replacements the model makes, held to the subtitle lines."""
    items = [
        {
            "id": n,
            "before": _text(segments[s["index"] - 1]) if s["index"] > 0 else "",
            "sentence": _text(segments[s["index"]]),
            "after": _text(segments[s["index"] + 1]) if s["index"] + 1 < len(segments) else "",
            "suspect": s["word"],
            "subtitles": evidence[s["index"]],
        }
        for n, s in enumerate(suspects) if evidence.get(s["index"])
    ]
    def ask(batch: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            return _json_object(chat(_DECIDE_SYSTEM, json.dumps(batch, ensure_ascii=False)))
        except Exception as exc:  # noqa: BLE001 - a failed batch changes nothing
            logger.warning("Subtitle decision failed on a batch: %s", exc)
            return {}

    batches = [items[start:start + DECIDE_BATCH] for start in range(0, len(items), DECIDE_BATCH)]
    with ThreadPoolExecutor(max_workers=MODEL_WORKERS) as pool:
        answers = list(pool.map(ask, batches))
    fixes: list[dict[str, Any]] = []
    for batch, answer in zip(batches, answers):
        by_id = {item["id"]: item for item in batch}
        for fix in answer.get("fixes") or []:
            if not isinstance(fix, dict):
                continue
            try:
                item = by_id.get(int(fix.get("id")))
            except (TypeError, ValueError):
                continue
            old, new = str(fix.get("from") or ""), str(fix.get("to") or "").strip()
            if item is None or not old.strip() or not new or old == new:
                continue
            line = next((sub for sub in item["subtitles"] if _loose(new) in _loose(sub)), None)
            if old not in item["sentence"] or line is None:
                continue
            fixes.append({"suspect": suspects[item["id"]], "from": old, "to": new, "subtitle": line})
    return fixes


def apply(segments: list[dict[str, Any]], fixes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The segments with the fixes made, and one record per change."""
    fixed = [dict(seg) for seg in segments]
    records: list[dict[str, Any]] = []
    for fix in fixes:
        index = fix["suspect"]["index"]
        before = _text(fixed[index])
        if fix["from"] not in before:
            continue
        at = before.index(fix["from"])
        after = before[:at] + fix["to"] + before[at + len(fix["from"]):]
        fixed[index]["text"] = after
        records.append({
            "id": uuid.uuid4().hex[:12],
            "start": fixed[index].get("start"),
            "end": fixed[index].get("end"),
            "from": fix["from"],
            "to": fix["to"],
            "at": at,
            "subtitle": fix["subtitle"],
            "sentence_before": before,
            "sentence_after": after,
            "reverted": False,
        })
    return fixed, records


def correct_from_subtitles(
    video: Path | None,
    segments: list[dict[str, Any]],
    chat: Chat | None,
    *,
    read: Callable = read_band,
    video_check: Callable[[Path], bool] = _has_video,
    ocr_available: Callable[[], bool] = frame_text.available,
) -> SubtitleCorrectionResult:
    """The whole step. Never raises: anything missing or failing leaves the
    transcript as it was, with the reason recorded."""
    started = time.perf_counter()

    def done(status: str, **kwargs: Any) -> SubtitleCorrectionResult:
        kwargs.setdefault("segments", segments)
        return SubtitleCorrectionResult(status=status, seconds=time.perf_counter() - started, **kwargs)

    if not ocr_available():
        return done("skipped", reason="no_text_recognition")
    if video is None or not Path(video).is_file() or not video_check(Path(video)):
        return done("skipped", reason="no_picture")
    if not segments:
        return done("skipped", reason="no_transcript")
    try:
        with tempfile.TemporaryDirectory(prefix="ff-subtitles-") as tmp:
            workdir = Path(tmp)
            found, checked, matched = detect(Path(video), segments, workdir, read)
            if not found:
                return done("skipped", reason="no_burned_subtitles", checked=checked, matched=matched)
            if chat is None:
                return done("skipped", reason="no_model", checked=checked, matched=matched)
            phases: dict[str, float] = {"detect": time.perf_counter() - started}
            mark = time.perf_counter()
            suspects = pick_suspects(segments, chat)[:MAX_SUSPECTS]
            indices = sorted({s["index"] for s in suspects})
            phases["pick"], mark = time.perf_counter() - mark, time.perf_counter()
            evidence = gather(Path(video), segments, indices, workdir, read)
            phases["read"], mark = time.perf_counter() - mark, time.perf_counter()
            fixes = decide(segments, suspects, evidence, chat)
            phases["decide"] = time.perf_counter() - mark
            fixed, records = apply(segments, fixes)
            return done(
                "completed" if records else "no_changes",
                segments=fixed, corrections=records, checked=checked, matched=matched,
                suspects=len(suspects), looked_up=sum(1 for i in indices if evidence.get(i)),
                phase_seconds=phases,
            )
    except Exception as exc:  # noqa: BLE001 - this step may never fail a task
        logger.warning("Subtitle correction failed: %s", exc)
        return done("failed", reason=str(exc)[:300])


def joined(segments: list[dict[str, Any]]) -> str:
    return "\n".join(_text(seg) for seg in segments if _text(seg))


class RevertError(ValueError):
    pass


def _find(segments: list[dict[str, Any]], record: dict[str, Any]) -> int | None:
    """The sentence a correction was made in: the one that still reads as it
    was left, else the nearest in time that still has the new text."""
    exact = [i for i, seg in enumerate(segments) if _text(seg) == record.get("sentence_after")]
    start = _seconds(record.get("start"))
    if len(exact) == 1 or (exact and start is None):
        return exact[0]
    if start is None:
        return None
    best: tuple[float, int] | None = None
    for i, seg in enumerate(segments):
        if record["to"] not in _text(seg):
            continue
        distance = abs((_seconds(seg.get("start")) or 0.0) - start)
        if distance <= 2.0 and (best is None or distance < best[0]):
            best = (distance, i)
    return best[1] if best else None


def _undone(text: str, record: dict[str, Any]) -> str:
    """``text`` with this one change taken out: at the place it was made when
    the text there is still the new spelling (「现在才做」 corrected to
    「现在在做」 has two 在, and the first is not the one that changed),
    otherwise at its first occurrence."""
    at, new = record.get("at"), record["to"]
    if isinstance(at, int) and text[at:at + len(new)] == new:
        return text[:at] + record["from"] + text[at + len(new):]
    return text.replace(new, record["from"], 1)


def revert(result: dict[str, Any], ids: list[str] | None) -> tuple[dict[str, Any], list[str], list[str]]:
    """``result`` with the named corrections (all, when ``ids`` is empty) put
    back to what the transcript said. Returns (result, reverted, not found)."""
    records = [dict(r) for r in result.get("subtitle_corrections") or [] if isinstance(r, dict)]
    wanted = set(ids or [r.get("id") for r in records if not r.get("reverted")])
    unknown = sorted(wanted - {r.get("id") for r in records})
    if unknown and not records:
        raise RevertError("这个任务没有按画面字幕改过的地方。")
    lists = {key: [dict(seg) for seg in result.get(key) or [] if isinstance(seg, dict)]
             for key in ("raw_segments", "display_segments")}
    reverted: list[str] = []
    missing: list[str] = list(unknown)
    # Newest first, so a sentence changed twice is unwound in order and each
    # record's position still points at its own change.
    for record in reversed(records):
        if record.get("id") not in wanted or record.get("reverted"):
            continue
        hit = False
        for segments in lists.values():
            index = _find(segments, record)
            if index is not None:
                segments[index]["text"] = _undone(_text(segments[index]), record)
                hit = True
        if hit:
            record["reverted"] = True
            reverted.append(record["id"])
        else:
            missing.append(record["id"])
    updated = dict(result)
    for key, segments in lists.items():
        if result.get(key) is not None:
            updated[key] = segments
    updated["subtitle_corrections"] = records
    if reverted:
        updated["transcript_text"] = joined(lists["raw_segments"])
        updated["transcript_text_preview"] = updated["transcript_text"][:200]
    return updated, reverted[::-1], missing


__all__ = [
    "RevertError",
    "SubtitleCorrectionResult",
    "apply",
    "correct_from_subtitles",
    "coverage",
    "decide",
    "detect",
    "enabled",
    "gather",
    "joined",
    "pick_suspects",
    "read_band",
    "revert",
]
