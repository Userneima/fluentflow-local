"""Fix misheard words in the transcript from the subtitles burned into the picture.

Requirement (owner, 2026-10-24): the 3.5-hour Luo Fuli interview came back with
many names and model names marked 「（存疑）」 in the note, while the video
itself carried exact subtitles. At 1:22:58 of the cut the picture read
「训练的应该是GLM-5跟Kimi」, the transcript 「应该是GOM跟KIMI」, the note
「GOM（存疑）」. The fix goes into the transcript, not the note: the transcript
is what the note, the subtitle files and the follow-along page all read, so one
correction reaches all of them.

Second version (owner, 2026-10-25, plan 方案/2026-10-25-画面字幕修正第二版).
The first had a model read the whole transcript for doubtful words and then
looked each one up in the picture: two model passes, some forty calls and
about four thousand separate frame reads, 15 minutes on that interview, and
nothing the model did not flag could be corrected. Now the picture is read
first, locally, and the model sees only what disagrees:

1. ``locate``: a handful of sentences, one frame each, bottom of the picture.
   When too few read back as the transcript, the whole step is skipped. When
   they do, where the subtitle line sits is kept, so the rest reads only that
   strip. Audio-only material and machines without text recognition (Windows)
   skip too; nothing here can fail a task.
2. ``read_track``: the video decoded once, one strip a second, twenty strips
   stacked into one picture for the recogniser, each line put back on its
   second by its height. Measured on ten minutes of the interview: 7 seconds
   to cut the strips, 4 to read them.
3. ``cues``: consecutive seconds that show the same line become one cue, its
   text the reading most seconds agree on (the recogniser now and then reads
   的 as 旳 on one frame).
4. ``mismatches``: each transcript sentence against the cues around it, by
   the code. Sentences the subtitle repeats, or only shortens (subtitle
   authors drop fillers), are left; a sentence where a word is read
   differently goes to the model.
5. ``decide``: the model, in large batches, says which differences are
   mishearings. The code holds it to the picture: the replaced text has to be
   in the sentence and the new text in a subtitle line.

Every change is recorded (old and new text, position, time, the subtitle line
it rests on) and can be undone through ``revert``. The speech recogniser's own
output (``stt_raw_segments``) is never touched.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from backend.core import frame_text

logger = logging.getLogger(__name__)

Chat = Callable[[str, str], str]

# The bottom share of the picture searched for a subtitle line. Burned-in
# subtitles sit in the lowest fifth on the material seen so far; a third leaves
# room for two-line subtitles and for a chapter bar under them.
SUBTITLE_BAND = 0.35

# Detection: sentences sampled, and how many must read back for the video to
# count as subtitled. Three out of ten tolerates sentences whose subtitle is
# already off screen at the transcript's time, and still rejects a talking-head
# recording, which reads nothing of the sentence at all.
PROBE_SENTENCES = 10
PROBE_NEEDED = 3

# How much of the shorter of two texts has to occur, in order, in the longer
# for them to count as the same words (``coverage``). A line that carries the
# right spelling of a misheard word still shares the rest of the sentence:
# 「训练的应该是GLM-5跟Kimi」 against 「应该是GOM跟KIMI」 shares 应该是…跟kimi.
# Recogniser garble and a chapter bar share a stray character or two.
PROBE_COVERAGE = 0.6
ALIGN_COVERAGE = 0.35
SAME_LINE = 0.7
MIN_LINE_CHARS = 4

# The strip read every second: stacked this many to a picture, and scaled so a
# line of text is about this many pixels tall, which reads as well as the full
# frame did and keeps the stacked picture small.
STACK = 20
LINE_PIXELS = 30
# Burned-in subtitles and the transcript's clock drift apart by a few seconds:
# on the Luo Fuli interview the picture at 2:32:19 still showed the sentence
# the transcript had started four seconds earlier.
DRIFT_SECONDS = 2.0

# Sentences the model judges per request, and a runaway guard on the total.
DECIDE_BATCH = 100
MAX_MISMATCHES = 4000
WORKERS = 6
MODEL_WORKERS = 4
FRAME_TIMEOUT_SECONDS = 20

# How much text one replacement may take out, and how much longer or shorter
# its replacement may be, in words: a Han character counts one, a run of
# Latin letters or digits counts one. The longest mishearings measured on the
# Luo Fuli interview were six (「最好吃机笔件」 for 「最好是几比几」,
# 「Code Office 4.6」 for 「Claude Opus 4.6」 is three). Anything longer is the
# model rewriting a sentence, not fixing a word.
MAX_REPLACED_WORDS = 6
_BRACKETS = set("（）()【】[]")

_OFF = {"0", "false", "no", "off", "disabled"}


class Cancelled(Exception):
    """The task was cancelled while this step ran."""


class ModelFailed(Exception):
    """The model raised (quota, login, network), as opposed to answering
    something that cannot be read. The step gives up instead of asking again."""


def _stopped(stop: threading.Event | None) -> None:
    if stop is not None and stop.is_set():
        raise Cancelled("任务已取消")
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
    cues: int = 0
    mismatched: int = 0
    seconds: float = 0.0
    reason: str | None = None
    phase_seconds: dict[str, float] = field(default_factory=dict)
    unreadable_batches: int = 0

    def meta(self, channel: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status,
            "probe_checked": self.checked,
            "probe_matched": self.matched,
            "subtitle_cues": self.cues,
            "mismatched_sentences": self.mismatched,
            "applied": len(self.corrections),
            "unreadable_batches": self.unreadable_batches or None,
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


_LOOKALIKE = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"})


def _loose(text: str) -> str:
    """Text compared the way the recogniser confuses it: case, spacing, and
    the letters it swaps (it read OpenAI off the Luo Fuli interview as
    OpenAl). The model may write OpenAI from a line read as OpenAl; it still
    cannot write a spelling the line does not carry."""
    return re.sub(r"\s+", "", text or "").lower().translate(_LOOKALIKE)


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


def _text(segment: dict[str, Any]) -> str:
    return str(segment.get("text") or "").strip()


def _seconds(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── 1. is there a subtitle, and where ─────────────────────────────────────

@dataclass(frozen=True)
class Band:
    """Where the subtitle line sits, as fractions of the picture's height."""

    top: float
    bottom: float
    line: float

    @property
    def height(self) -> float:
        return self.bottom - self.top


def read_band(video: Path, at: float, workdir: Path) -> list[frame_text.TextBox]:
    """The text in the bottom of the frame at ``at`` seconds, placed on the
    whole picture."""
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
        boxes = frame_text.read_boxes(out, language_correction=False)
    except (subprocess.SubprocessError, OSError):
        return []
    finally:
        out.unlink(missing_ok=True)
    offset = 1 - SUBTITLE_BAND
    return [
        frame_text.TextBox(b.text, offset + b.top * SUBTITLE_BAND, offset + b.bottom * SUBTITLE_BAND, b.left, b.right)
        for b in boxes
    ]


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
    return "".join(_text(segments[j]) for j in range(index - reach, index + reach + 1) if 0 <= j < len(segments))


def locate(
    video: Path, segments: list[dict[str, Any]], workdir: Path, read: Callable = read_band,
) -> tuple[Band | None, int, int]:
    """Where the subtitle line is, or ``None`` when the picture carries none:
    (band, sentences checked, sentences that read back)."""
    checked = matched = 0
    hits: list[frame_text.TextBox] = []
    indices = _probe_indices(segments, PROBE_SENTENCES)
    for position, index in enumerate(indices):
        if matched >= PROBE_NEEDED or matched + (len(indices) - position) < PROBE_NEEDED:
            break
        seg = segments[index]
        checked += 1
        middle = (_seconds(seg.get("start")) + _seconds(seg.get("end"))) / 2
        nearby = _around(segments, index)
        found = [b for b in read(video, middle, workdir)
                 if len(_key(b.text)) >= MIN_LINE_CHARS and coverage(b.text, nearby) >= PROBE_COVERAGE]
        if found:
            matched += 1
            hits.extend(found)
    if matched < PROBE_NEEDED:
        return None, checked, matched
    line = max(b.bottom - b.top for b in hits)
    # Room above for a second line, which subtitles grow upwards; little
    # below, because what sits under a subtitle is often a chapter bar of
    # small text, and with it in the strip the recogniser reads the stacked
    # picture as columns and drops most of the lines (Luo Fuli interview,
    # 2026-10-25: 7 of 20 seconds read).
    top = max(0.0, min(b.top for b in hits) - line * 1.2)
    bottom = min(1.0, max(b.bottom for b in hits) + line * 0.3)
    return Band(top=top, bottom=bottom, line=line), checked, matched


# ── 2. the whole subtitle track ───────────────────────────────────────────

def _duration(video: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
            check=True, capture_output=True, text=True, timeout=15,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def read_track(
    video: Path, band: Band, workdir: Path, stop: threading.Event | None = None,
) -> list[tuple[float, str]]:
    """What the subtitle strip says each second: (second, text), one decode.
    A cancelled task stops the decode and the reading (``Cancelled``)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    height = max(24, math.ceil(LINE_PIXELS * band.height / max(band.line, 1e-3)))
    pattern = workdir / "strip_%05d.png"
    duration = _duration(video) or 0.0
    process = subprocess.Popen(
        [ffmpeg, "-v", "error", "-y", "-i", str(video), "-vf",
         f"fps=1,crop=iw:ih*{band.height:.4f}:0:ih*{band.top:.4f},scale=-2:{height},tile=1x{STACK}",
         str(pattern)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + max(600.0, duration)
    try:
        while True:
            try:
                _out, err = process.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if (stop is not None and stop.is_set()) or time.monotonic() > deadline:
                    process.kill()
                    process.communicate()
                    _stopped(stop)
                    raise RuntimeError("ffmpeg took too long reading the subtitle strip")
    finally:
        if process.poll() is None:
            process.kill()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed reading the subtitle strip: {(err or b'').decode(errors='replace')[-300:]}")
    stacks = sorted(workdir.glob("strip_*.png"))

    def read(path: Path) -> list[frame_text.TextBox]:
        _stopped(stop)
        return frame_text.read_boxes(path, language_correction=False)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        readings = list(pool.map(read, stacks))
    seconds: dict[int, list[frame_text.TextBox]] = {}
    for number, boxes in enumerate(readings):
        for box in boxes:
            row = min(STACK - 1, max(0, int(box.middle * STACK)))
            seconds.setdefault(number * STACK + row, []).append(box)
    return [
        (float(second), " ".join(b.text for b in sorted(boxes, key=lambda b: (round(b.top, 2), b.left))))
        for second, boxes in sorted(seconds.items())
        if not duration or second <= duration
    ]


@dataclass
class Cue:
    start: float
    end: float
    text: str


def cues(track: list[tuple[float, str]]) -> list[Cue]:
    """Consecutive seconds that show the same line, as one cue; its text the
    reading most of those seconds agree on."""
    groups: list[tuple[float, float, list[str]]] = []
    for second, text in track:
        if len(_key(text)) < 2:
            continue
        if groups and second - groups[-1][1] <= 1.5 and coverage(text, groups[-1][2][-1]) >= SAME_LINE \
                and abs(len(_key(text)) - len(_key(groups[-1][2][-1]))) <= max(2, len(_key(text)) // 4):
            start, _end, texts = groups[-1]
            groups[-1] = (start, second, [*texts, text])
        else:
            groups.append((second, second, [text]))
    result: list[Cue] = []
    for start, end, texts in groups:
        counts = Counter(texts)
        best = max(counts.items(), key=lambda item: (item[1], len(item[0])))[0]
        result.append(Cue(start=start, end=end + 1.0, text=best))
    return result


# ── 3. which sentences disagree with the picture ──────────────────────────

def _cues_near(track: list[Cue], start: float, end: float) -> list[Cue]:
    return [c for c in track if c.end >= start - DRIFT_SECONDS and c.start <= end + DRIFT_SECONDS]


# What subtitle authors leave out of what was said, and the particles they add.
# A difference made only of these is editing, not a mishearing. Anything else,
# however small (MA read where the subtitle has MLA is one inserted letter),
# goes to the model.
_DROPPED = set("嗯啊呃哦诶唉嘛吧呢呀哈就那这个的了对是吗么")
_ADDED = set("的了地得着")


def _disagrees(sentence: str, subtitle: str) -> bool:
    """Whether the subtitle reads some word of the sentence differently, as
    opposed to repeating it or only leaving fillers out."""
    s, c = _key(sentence), _key(subtitle)
    blocks = [b for b in SequenceMatcher(None, s, c, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return False
    # The stretch of subtitle that lines up with the whole sentence, reaching
    # past the first and last matches by as much as the sentence does: a
    # misheard first word (内人 against 类人) sits outside every match.
    lead, trail = blocks[0].a, len(s) - (blocks[-1].a + blocks[-1].size)
    # A filler the subtitle left out at either end is not a word to look for.
    if set(s[:lead]) <= _DROPPED:
        lead = 0
    if set(s[len(s) - trail:]) <= _DROPPED:
        trail = 0
    region = c[max(0, blocks[0].b - lead): blocks[-1].b + blocks[-1].size + trail]
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, s, region, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        gone, came = s[i1:i2], region[j1:j2]
        if tag == "delete" and set(gone) <= _DROPPED:
            continue
        if tag == "insert" and set(came) <= _ADDED:
            continue
        return True
    return False


def mismatches(segments: list[dict[str, Any]], track: list[Cue]) -> list[dict[str, Any]]:
    """The sentences whose words the subtitle reads differently, each with the
    subtitle lines around it."""
    found: list[dict[str, Any]] = []
    for index, seg in enumerate(segments):
        sentence = _text(seg)
        start, end = _seconds(seg.get("start")), _seconds(seg.get("end"))
        if len(_key(sentence)) < 2 or start is None or end is None:
            continue
        near = _cues_near(track, start, end)
        if not near:
            continue
        joined_text = "".join(c.text for c in near)
        if coverage(sentence, joined_text) < ALIGN_COVERAGE:
            continue
        if _key(sentence) in _key(joined_text):
            continue
        if not _disagrees(sentence, joined_text):
            continue
        found.append({"index": index, "subtitles": [c.text for c in near]})
    return found[:MAX_MISMATCHES]


# ── 4. the model's judgement ──────────────────────────────────────────────

_DECIDE_SYSTEM = """你会收到一批语音识别转录的句子。每条给出：这句转录（sentence）和它的前一句（before）、后一句（after），以及这句话播放前后几秒视频画面上的字幕（subtitles）。字幕是视频作者做的，通常写对了，但字幕和转录的时间可能差几秒，字幕行也可能属于前后句；字幕识别偶尔会读错个别字。

逐条判断转录里有没有听错的词：人名、公司名、产品名、模型名、英文单词和缩写、数字和版本号、读不通的同音词。只有当字幕明显就是这句话、并且清楚写出了正确写法时，才替换。
- from：sentence 里原样出现的一段文字，只框住听错的部分，尽量短。
- to：从字幕里原样抄下来的写法，不自己改写、不加字。
- 字幕作者删掉的语气词、重复、口头禅，不要补回、也不要删转录里的。
- 字幕和转录只是说法不同（断句、同义词、改写），不改。只换听错的词，不按字幕改写整句。
- 字幕看不出、字幕像识别乱码，不改。
- 字幕里的括号注释（比如「10B的（激活）模型」里的「（激活）」）是视频作者加的说明，不是原话，不要抄进 to。
- 字幕是繁体时，只改专有名词和英文，不把简体字改成繁体。
- 字幕识别会把大写 I 读成小写 l、把 0 读成 O；这种情况按常识写对（OpenAl 写成 OpenAI）。
- 一句里有几处听错，分几条写。

只输出 JSON：{"fixes": [{"id": 编号, "from": "...", "to": "..."}]}。不改的不要列出。"""


def _answer(text: str) -> dict[str, Any] | None:
    """The model's ``{"fixes": [...]}``, or ``None`` when the reply holds no
    such object (a sentence of prose before it, a brace in the prose, a reply
    cut short)."""
    raw = text or ""
    decoder = json.JSONDecoder()
    for start in [m.start() for m in re.finditer(r"\{", raw)]:
        try:
            parsed, _end = decoder.raw_decode(raw, start)
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("fixes"), list):
            return parsed
    return None


def _words(text: str) -> int:
    """Han characters, plus runs of Latin letters or digits, each one word."""
    return len(re.findall(r"[一-鿿]|[A-Za-z0-9]+", text or ""))


def _position(sentence: str, old: str, subtitle: str) -> int | None:
    """Where in the sentence ``old`` is the misheard text. When it occurs more
    than once, the occurrence the subtitle reads differently; ``None`` when
    that cannot be told."""
    places = [m.start() for m in re.finditer(re.escape(old), sentence)]
    if len(places) <= 1:
        return places[0] if places else None
    differing = set()
    for tag, i1, i2, _j1, _j2 in SequenceMatcher(None, sentence, subtitle, autojunk=False).get_opcodes():
        if tag != "equal":
            differing.update(range(i1, max(i2, i1 + 1)))
    scores = [sum(1 for i in range(p, p + len(old)) if i in differing) for p in places]
    best = max(scores)
    return places[scores.index(best)] if best and scores.count(best) == 1 else None


def _acceptable(old: str, new: str, sentence: str) -> bool:
    """A replacement of a word or short phrase, not a rewrite: the text taken
    out is short, the new text is about as long, and nothing the subtitle
    author added in brackets (「10B的（激活）模型」) is copied into what was
    said."""
    if not old.strip() or not new or _key(old) == _key(new):
        # Case or spacing alone (KIMI against Kimi) is not a mishearing.
        return False
    if (_BRACKETS & set(new)) - set(old):
        return False
    taken, given = _words(old), _words(new)
    if taken > MAX_REPLACED_WORDS or abs(given - taken) > max(2, taken):
        return False
    return old in sentence


def decide(
    segments: list[dict[str, Any]], found: list[dict[str, Any]], chat: Chat, stop: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """The replacements the model makes, held to the subtitle lines, and how
    many batches came back unreadable.

    A reply that cannot be read is asked once more, then in two halves, each
    once. A model that raises (quota, login, network) ends the step with
    ``ModelFailed``: asking again would only multiply the failed calls (the
    review measured 4444 calls for 1117 sentences when every call failed and
    every failure was retried and split)."""
    items = [
        {
            "id": n,
            "before": _text(segments[f["index"] - 1]) if f["index"] > 0 else "",
            "sentence": _text(segments[f["index"]]),
            "after": _text(segments[f["index"] + 1]) if f["index"] + 1 < len(segments) else "",
            "subtitles": f["subtitles"],
        }
        for n, f in enumerate(found)
    ]
    failed = threading.Event()

    def once(batch: list[dict[str, Any]]) -> dict[str, Any] | None:
        _stopped(stop)
        if failed.is_set():
            raise ModelFailed("an earlier request failed")
        try:
            reply = chat(_DECIDE_SYSTEM, json.dumps(batch, ensure_ascii=False))
        except Exception as exc:
            failed.set()
            raise ModelFailed(str(exc)[:300]) from exc
        return _answer(reply)

    def ask(batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        answer = once(batch)
        if answer is None:
            answer = once(batch)
        if answer is not None:
            return answer["fixes"], False
        if len(batch) == 1:
            return [], True
        half = len(batch) // 2
        halves = [once(batch[:half]), once(batch[half:])]
        return [fix for part in halves if part for fix in part["fixes"]], any(part is None for part in halves)

    batches = [items[start:start + DECIDE_BATCH] for start in range(0, len(items), DECIDE_BATCH)]
    with ThreadPoolExecutor(max_workers=MODEL_WORKERS) as pool:
        answers = list(pool.map(ask, batches))
    fixes: list[dict[str, Any]] = []
    for batch, (answer, _lost) in zip(batches, answers):
        by_id = {item["id"]: item for item in batch}
        for fix in answer:
            if not isinstance(fix, dict):
                continue
            try:
                item = by_id.get(int(fix.get("id")))
            except (TypeError, ValueError):
                continue
            old, new = str(fix.get("from") or ""), str(fix.get("to") or "").strip()
            if item is None or not _acceptable(old, new, item["sentence"]):
                continue
            line = next((sub for sub in item["subtitles"] if _loose(new) in _loose(sub)), None)
            if line is None:
                continue
            at = _position(item["sentence"], old, "".join(item["subtitles"]))
            if at is None:
                continue
            fixes.append({"index": found[item["id"]]["index"], "from": old, "to": new, "at": at, "subtitle": line})
    return fixes, sum(1 for _answer_, lost in answers if lost)


def apply(segments: list[dict[str, Any]], fixes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The segments with the fixes made, and one record per change.

    Each fix is made where it was found in the sentence (``at``), the later
    places in a sentence first, so the earlier positions still hold; a fix that
    overlaps one already made is dropped."""
    fixed = [dict(seg) for seg in segments]
    records: list[dict[str, Any]] = []
    by_sentence: dict[int, list[dict[str, Any]]] = {}
    for fix in fixes:
        by_sentence.setdefault(fix["index"], []).append(fix)
    for index in sorted(by_sentence):
        taken: list[tuple[int, int]] = []
        for fix in sorted(by_sentence[index], key=lambda f: -int(f.get("at", 0))):
            before = _text(fixed[index])
            at = fix.get("at")
            if not isinstance(at, int) or before[at:at + len(fix["from"])] != fix["from"]:
                continue
            span = (at, at + len(fix["from"]))
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            taken.append(span)
            after = before[:at] + fix["to"] + before[at + len(fix["from"]):]
            fixed[index]["text"] = after
            records.append({
                "id": uuid.uuid4().hex[:12],
                "start": fixed[index].get("start"),
                "end": fixed[index].get("end"),
                "from": fix["from"],
                "to": fix["to"],
                "at": at,
                # What the change rests on: the subtitle line here, the word
                # list entry or the model's reason in the glossary step.
                **{key: value for key, value in fix.items() if key not in {"index", "from", "to", "at"}},
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
    probe: Callable = read_band,
    track_reader: Callable = read_track,
    video_check: Callable[[Path], bool] = _has_video,
    ocr_available: Callable[[], bool] = frame_text.available,
    stop: threading.Event | None = None,
) -> SubtitleCorrectionResult:
    """The whole step. Never raises: anything missing or failing leaves the
    transcript as it was, with the reason recorded. ``stop`` set (the task
    was cancelled) ends it between frames and before each model call."""
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
            band, checked, matched = locate(Path(video), segments, workdir, probe)
            if band is None:
                return done("skipped", reason="no_burned_subtitles", checked=checked, matched=matched)
            if chat is None:
                return done("skipped", reason="no_model", checked=checked, matched=matched)
            phases: dict[str, float] = {"locate": time.perf_counter() - started}
            mark = time.perf_counter()
            _stopped(stop)
            track = cues(track_reader(Path(video), band, workdir, stop))
            phases["read"], mark = time.perf_counter() - mark, time.perf_counter()
            found = mismatches(segments, track)
            phases["align"], mark = time.perf_counter() - mark, time.perf_counter()
            fixes, unreadable = decide(segments, found, chat, stop) if found else ([], 0)
            phases["decide"] = time.perf_counter() - mark
            fixed, records = apply(segments, fixes)
            return done(
                "completed" if records else "no_changes",
                segments=fixed, corrections=records, checked=checked, matched=matched,
                cues=len(track), mismatched=len(found), phase_seconds=phases, unreadable_batches=unreadable,
            )
    except Cancelled:
        return done("cancelled", reason="task_cancelled")
    except ModelFailed as exc:
        logger.warning("Subtitle correction gave up, the model failed: %s", exc)
        return done("failed", reason=f"model_error: {exc}")
    except Exception as exc:  # noqa: BLE001 - this step may never fail a task
        logger.warning("Subtitle correction failed: %s", exc)
        return done("failed", reason=str(exc)[:300])


def joined(segments: list[dict[str, Any]]) -> str:
    return "\n".join(_text(seg) for seg in segments if _text(seg))


class RevertError(ValueError):
    pass


def _find(segments: list[dict[str, Any]], record: dict[str, Any], clock: str = "start") -> int | None:
    """The sentence a correction was made in: the one that still reads as it
    was left, else the nearest in time that still has the new text.
    ``clock`` names the record's time on the clock these segments use."""
    exact = [i for i, seg in enumerate(segments) if _text(seg) == record.get("sentence_after")]
    start = _seconds(record.get(clock, record.get("start")))
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


_COPIES = (
    "raw_segments", "display_segments", "corrected_segments",
    "source_raw_segments", "source_display_segments", "source_corrected_segments",
)


def revert(
    result: dict[str, Any], ids: list[str] | None, field: str = "subtitle_corrections",
    none_made: str = "这个任务没有按画面字幕改过的地方。",
) -> tuple[dict[str, Any], list[str], list[str]]:
    """``result`` with the named corrections (all, when ``ids`` is empty) put
    back to what the transcript said. Returns (result, reverted, not found).
    ``field`` names the step's records: the glossary step keeps its own."""
    records = [dict(r) for r in result.get(field) or [] if isinstance(r, dict)]
    wanted = set(ids or [r.get("id") for r in records if not r.get("reverted")])
    unknown = sorted(wanted - {r.get("id") for r in records})
    if unknown and not records:
        raise RevertError(none_made)
    # Every copy of the transcript that carries the change: what the page and
    # the subtitle files read, what the note and the agent read first when the
    # text-model correction is on, and the copies on the original's clock that
    # a re-export of the cut starts from.
    lists = {key: [dict(seg) for seg in result.get(key) or [] if isinstance(seg, dict)]
             for key in _COPIES if isinstance(result.get(key), list)}
    reverted: list[str] = []
    missing: list[str] = list(unknown)
    # Newest first, so a sentence changed twice is unwound in order and each
    # record's position still points at its own change.
    for record in reversed(records):
        if record.get("id") not in wanted or record.get("reverted"):
            continue
        # The transcript the user sees decides: a sentence edited there since
        # is the user's, and no copy of it is touched.
        if "raw_segments" in lists and _find(lists["raw_segments"], record) is None:
            missing.append(record["id"])
            continue
        hit = False
        for key, segments in lists.items():
            index = _find(segments, record, "source_start" if key.startswith("source_") else "start")
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
    updated[field] = records
    if reverted and "raw_segments" in lists:
        updated["transcript_text"] = joined(lists["raw_segments"])
        updated["transcript_text_preview"] = updated["transcript_text"][:200]
    if reverted and lists.get("corrected_segments"):
        updated["corrected_transcript_text"] = joined(lists["corrected_segments"])
    return updated, reverted[::-1], missing


__all__ = [
    "Band",
    "Cancelled",
    "ModelFailed",
    "Cue",
    "RevertError",
    "SubtitleCorrectionResult",
    "apply",
    "correct_from_subtitles",
    "coverage",
    "cues",
    "decide",
    "enabled",
    "joined",
    "locate",
    "mismatches",
    "read_band",
    "read_track",
    "revert",
]
