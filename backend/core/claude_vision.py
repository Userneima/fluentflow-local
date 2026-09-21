"""Ask Claude to read a task's frames and transcript and write one note.

This is the only place in the product that sends pictures to a model in order to
have the *note itself* written from them. The existing screenshot pipeline is a
different thing and stays: a text model proposes where a screenshot would help,
ffmpeg cuts frames at those moments, and a vision model picks which frame in
each window matches the request. The note text there is written from the
transcript alone — the pictures decorate a note nobody looked at pictures to
write. That is a defensible product, but it is not "a note made from what is on
screen", and this module exists so the difference can be stated honestly rather
than blurred.

Two rules follow from that and are enforced here rather than left to a caller:

- No silent substitution. If the Anthropic credential is missing, or the SDK is
  not installed, this raises with a sentence saying what to configure. It never
  falls back to DeepSeek/Qwen/OpenAI and calls the result "combined with the
  picture" — the whole claim of the feature is which model actually saw the
  frames.
- The caller learns what was sent. ``VisualNoteDraft`` carries the frames that
  went into the request, so "the note read the pictures" is something the
  product can show, not something it asserts.

Credentials are read, passed to the SDK, and never logged, returned, or written
into a job result.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # The local edition must still boot for someone who never wants this.
    import anthropic
except ImportError:  # pragma: no cover - exercised by the "not installed" path
    anthropic = None  # type: ignore[assignment]

# Opus is the default because the model's judgement about which pictures are
# worth anything, and what each one adds that the speaking did not, is the
# product here.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 32_000

# How many pictures fit in *one request*, which is all this channel gets: it
# attaches the images itself and cannot go back for more. Twenty frames of a
# lecture is a frame every few minutes on a long recording — thin, and said so in
# the result rather than hidden, because the honest alternative is a request
# nobody can afford.
MAX_FRAMES = 20

# How many distinct pictures are kept on disk and offered to a channel that can
# open files for itself. Measured on four recordings: a 15-minute meeting has 79
# distinct pictures, a 69-minute class has 97, and two others were still at the
# ceiling of a probe that stopped counting at 100. So the material carries five
# times what one request can hold, and at ~1,840 tokens per frame all of them
# would be 184k tokens — most of a context window before the transcript arrives.
#
# That is the whole case for the index: not that choosing for the model is
# inelegant, but that there is no request in which the choice does not have to be
# made. Made here it is made by edge contrast and a duplicate threshold; made by
# the model it is made by looking.
FRAME_INDEX_CAP = 120

# How many pictures may be attached to one note-writing request.
#
# Measured rather than reasoned: a 103-minute lecture's 119 frames went in a
# single request, were all in context, and produced the best note this line of
# work has made. That retired an entire pass — a text index the model read to
# pick a subset — which had existed only to avoid sending everything.
#
# So this is the index cap, and it is here as a guard rather than a policy: if
# extraction ever hands over more than one request can hold, ``spread_across``
# thins them over the whole recording rather than taking a prefix, which would
# quietly describe the opening minutes of a lecture and nothing after.
FRAME_ATTACH_MAX = FRAME_INDEX_CAP

# How much of the talk one note-writing request carries.
#
# Not raised here, and not to be raised without measuring: what fills this
# request is the pictures. A hundred-odd frames is most of the window before a
# word of the transcript goes in, so the ceiling belongs to the two of them
# together and moving one half of it blind is how a request starts failing on
# exactly the long recordings this is for.
#
# What did change is that going over it is no longer silent. See
# ``transcript_for_request``.
MAX_TRANSCRIPT_CHARS = 60_000


@dataclass(frozen=True)
class TranscriptForRequest:
    """As much of the transcript as one request holds, and what did not fit."""

    text: str
    chars_sent: int
    chars_dropped: int
    # The timestamp on the last line that made it, as the transcript writes them.
    # Empty when nothing was dropped. This is the number the reader acts on: it
    # says where to split the recording, which no character count does.
    covered_until: str = ""


_TIMESTAMP_AT_LINE_START = re.compile(r"^\[(\d+:\d{2})\]")


def transcript_for_request(transcript: str) -> TranscriptForRequest:
    """Fit the transcript to one request, and say what was left out.

    The overflow used to be dropped where nobody could see it — no error, no
    warning, nothing in the note — so a five-hour lecture came back as a note
    that reads like a complete one and covers the first half. A note that is
    quietly missing content is worse than one that fails, because nothing about
    it looks wrong.

    Cuts at a line boundary rather than mid-sentence: the lines are what carry
    the timestamps, and half a sentence is not something to hand a model.
    """
    text = (transcript or "").strip()
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return TranscriptForRequest(text=text, chars_sent=len(text), chars_dropped=0)
    head = text[:MAX_TRANSCRIPT_CHARS]
    boundary = head.rfind("\n")
    kept = head[:boundary] if boundary > 0 else head
    last_line = kept.rsplit("\n", 1)[-1]
    stamp = _TIMESTAMP_AT_LINE_START.match(last_line)
    return TranscriptForRequest(
        text=kept,
        chars_sent=len(kept),
        chars_dropped=len(text) - len(kept),
        covered_until=stamp.group(1) if stamp else "",
    )

@dataclass(frozen=True)
class TranscriptPart:
    """One request's worth of transcript, and where it sits in the recording."""

    text: str
    index: int
    total: int
    starts_at: str = ""
    ends_at: str = ""

    @property
    def only_part(self) -> bool:
        return self.total <= 1


def transcript_parts(transcript: str, *, max_chars: int | None = None) -> list[TranscriptPart]:
    """Split a transcript into as many requests as it takes to cover all of it.

    Truncating was the old answer and it was the wrong shape of answer: a note
    that silently stops two thirds of the way through reads like a complete one,
    so nothing about it looks wrong. A recording is not too long to write about,
    it is too long for one request.

    Splits on line boundaries, because the lines are what carry the timestamps
    and half a sentence is not something to hand a model. Each part records the
    stamps it runs between so the note can say which stretch it covers and the
    frames for that stretch can be picked out.
    """
    # Read at call time, not bound as a default: the limit is a property of the
    # request this run is about to make, and a test that moves it expects the
    # next call to honour the new one.
    max_chars = MAX_TRANSCRIPT_CHARS if max_chars is None else max_chars
    text = (transcript or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [TranscriptPart(text=text, index=1, total=1,
                               starts_at=_first_stamp(text), ends_at=_last_stamp(text))]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        head = remaining[:max_chars]
        boundary = head.rfind("\n")
        cut = boundary if boundary > 0 else max_chars
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    total = len(chunks)
    return [
        TranscriptPart(
            text=chunk,
            index=position,
            total=total,
            starts_at=_first_stamp(chunk),
            ends_at=_last_stamp(chunk),
        )
        for position, chunk in enumerate(chunks, start=1)
    ]


def _first_stamp(text: str) -> str:
    for line in text.splitlines():
        found = _TIMESTAMP_AT_LINE_START.match(line)
        if found:
            return found.group(1)
    return ""


def _last_stamp(text: str) -> str:
    for line in reversed(text.splitlines()):
        found = _TIMESTAMP_AT_LINE_START.match(line)
        if found:
            return found.group(1)
    return ""


def stamp_seconds(stamp: str) -> float | None:
    """A transcript stamp as seconds. Minutes are not wrapped at sixty here."""
    minutes, _, seconds = (stamp or "").partition(":")
    try:
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return None


def frames_for_part(frames: list[FrameInput], part: TranscriptPart) -> list[FrameInput]:
    """The pictures that belong to this stretch of the recording.

    Sending all of them to every part would spend the budget on the same twenty
    pictures each time and leave most of the recording unillustrated, which is
    the whole-file version of the bug the split exists to fix. A frame with no
    timestamp cannot be placed, so it goes to the first part rather than being
    dropped or repeated.
    """
    if part.only_part:
        return list(frames)
    start = stamp_seconds(part.starts_at)
    end = stamp_seconds(part.ends_at)
    if start is None or end is None:
        return list(frames)
    chosen = []
    for frame in frames:
        at = frame.timestamp_seconds
        if at is None:
            if part.index == 1:
                chosen.append(frame)
            continue
        if start <= at <= end:
            chosen.append(frame)
    return chosen


def part_instruction(part: TranscriptPart) -> str:
    """What to tell the model about writing one stretch of a longer recording.

    Each part writes only its own stretch. The alternative — let every part write
    a whole note and stitch them afterwards — pays for an extra pass whose job is
    to delete the introductions and conclusions the first pass was told to write.
    """
    if part.only_part:
        return ""
    span = (
        f"（{part.starts_at} 到 {part.ends_at}）"
        if part.starts_at and part.ends_at
        else ""
    )
    return (
        f"\n\n这是同一段录像的第 {part.index} 部分，共 {part.total} 部分{span}。"
        "你手上的转录文字和截图都只属于这一部分。\n"
        "- 只写这一部分讲了什么。不要写整篇的开场介绍，不要写总结收尾，"
        "不要提「上一部分」或「接下来」——这些会和别的部分重复。\n"
        "- 从二级标题开始写，不要写一级标题：整篇的标题由合并时统一给。\n"
        "- 这一部分开头可能接着上一部分的话说到一半，照常写你看到的内容，不用解释缺了前文。"
    )


_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

SYSTEM_PROMPT = """你在为一段课程或讲座录像写学习笔记。

你会拿到两样东西：这段录像的转录文字（带时间点），以及若干张从同一段录像里按画面变化抽出的截图，每张都标了文件名和它在录像里的时间点。

请遵守：

1. 只写你能从转录文字或截图里确认的内容。不要补充你没有看到的背景知识，不要把推测写成录像里讲过的话。
2. **画面的作用是补充口播里没有的信息，不是把屏幕誊写一遍。**

看到画面上有东西时，先问：这一屏给了转录文字里没有的什么？可能是一个具体的数字、一个命令、一个被口播说错或含糊带过的名字、一张图表体现出来的结构关系。**把那件事写进正文的叙述里**——它是笔记的一部分，跟你从口播里得到的内容并列，不需要标明"这是从图上看到的"。

如果这一屏上的内容口播已经完整说过了，那它就没有补充任何东西：不用引用，也不用复述。

需要让读者自己去看那张画面时（幻灯片、板书、代码、公式、图表、界面演示、实物演示本身就是要看的），用 Markdown 图片语法引用：`![一句话说清这是哪张画面](文件名.jpg)`。

方括号里**只写一句话，让读者知道点开会看到什么**，比如「文档目录：七项能力的完整清单」「部署前的坑清单模板」。不要写「示意图」「如图所示」这种不含信息的说明；也不要把屏幕上的文字整段抄进方括号——**图注长到读者不点开图也能读完，说明你在用图注替代笔记**。
3. 文件名必须原样使用给你的那些，不要改写、不要编造。
4. **打开是为了判断，不是承诺引用。**你可以打开很多张，然后只引用其中少数几张——看过之后发现这张没给笔记增加任何东西，就不要引用它，这是正常且期待的结果，不是浪费。片头、片尾、过渡、纯人像、跟前一张几乎一样的画面一律不要引用。一份笔记引用几张都可以，包括一张都不引用。
5. 如果所有截图都没有可用信息，就只根据转录文字写笔记，并在 basis_note 里直说这一点。

用中文写笔记。结构自己定，服务于读者复习，不要套固定模板。"""

NOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "note_markdown": {
            "type": "string",
            "description": "完整的学习笔记，Markdown。画面依据用 ![说明](文件名.jpg) 引用。",
        },
        "basis_note": {
            "type": "string",
            "description": "一两句话说明这份笔记的依据：哪些部分靠画面、哪些只有字幕、有没有看不清或没信息的截图。",
        },
    },
    "required": ["note_markdown", "basis_note"],
    "additionalProperties": False,
}


class ClaudeVisionError(RuntimeError):
    """A reason worth showing the user, in their language.

    ``actionable`` marks the cases the user can fix themselves right now — a
    missing key, a missing package — as opposed to a rate limit or an outage.
    """

    def __init__(self, message: str, *, actionable: bool = False) -> None:
        super().__init__(message)
        self.actionable = actionable


@dataclass(frozen=True)
class FrameInput:
    """One picture that will be sent, and where it came from in the recording."""

    filename: str
    path: Path
    timestamp_seconds: float | None = None
    # How this frame was found: a real picture change, or evenly spaced sampling
    # because detection found nothing. A note written from samples reads exactly
    # like one written from scene changes, so the difference has to be recorded
    # or nobody can tell that a lecture was summarised from five snapshots.
    source: str | None = None
    # And how hard it had to look. The detector relaxes its sensitivity when
    # strong changes cannot fill the budget, so this says whether the pictures
    # are obvious cuts or the faint ones left after two relaxations — which is
    # the difference between a slide deck and a document being scrolled.
    scene_threshold: float | None = None
    # How much detail the picture carries. A page of text scores several times a
    # webcam shot of a face, which is what decides whether a recording is worth
    # sending twenty pictures of. Recorded per frame so the cutoff can be
    # recalibrated against real notes rather than re-guessed.
    detail: float | None = None
    # How far this picture was from the nearest one already kept, as a percentage
    # of the comparison grid. A frame that barely cleared the duplicate threshold
    # is a small edit of its neighbour — one more bullet on the same slide — and a
    # frame at 40% is a different subject. Offered to a model that is choosing
    # what to open, so it can tell those two apart without opening either.
    change_percent: float | None = None
    # The text on this picture that no earlier picture had, read locally. This is
    # the signal the numbers above were standing in for: measured on real course
    # frames, a shared-document lecture reads ~1,000 characters a frame and a
    # talking head reads 0-2, with nothing in between. ``None`` means nothing
    # tried to read it — a different fact from "" , which means it was read and
    # carried no new text, and neither is a reason to withhold the frame.
    new_text: str | None = None


@dataclass(frozen=True)
class VisualNoteDraft:
    markdown: str
    basis_note: str
    model: str
    frames_sent: list[FrameInput] = field(default_factory=list)
    # Which of them the model actually opened, when the channel can tell. The
    # key channel attaches its pictures, so everything sent was seen and this
    # stays empty; the subscription channel offers a hundred and the model picks,
    # so "sent" and "seen" stop being the same thing and only one of them is
    # evidence. Read from the agent's own tool calls, never from its summary.
    frames_opened: list[str] = field(default_factory=list)
    transcript_chars: int = 0
    # What the request could not hold, and where it stopped. Zero on everything
    # short enough, which is nearly everything; a long lecture is the case this
    # exists for, and the one where a note can look finished and not be.
    transcript_chars_dropped: int = 0
    transcript_covered_until: str = ""
    # What this note cost, as the channel reported it. Kept because both channels
    # hand it back on every call and throwing it away means paying again to learn
    # the same number: the subscription channel reports dollars and turns
    # directly, the key channel reports tokens that a price list turns into
    # dollars. Empty when a channel says nothing — never a guess.
    usage: dict[str, Any] = field(default_factory=dict)


def sdk_available() -> bool:
    return anthropic is not None


def configured_model() -> str:
    return (os.environ.get("FLUENTFLOW_VISUAL_NOTE_MODEL") or "").strip() or DEFAULT_MODEL


def resolve_api_key(api_key: str | None = None) -> str | None:
    """The caller's key if it has one, else the environment. Never logged."""
    value = (api_key or "").strip()
    if value:
        return value
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None


def unavailable_reason(api_key: str | None = None) -> str | None:
    """Why this cannot run right now, phrased as something to go and do.

    Returns ``None`` when it can run. Kept separate from the call itself so the
    page can ask "would this work?" without spending a request.
    """
    if not sdk_available():
        return (
            "本机还没有安装调用 Claude 所需的组件。在项目目录运行 "
            "`./venv/bin/pip install -r requirements.txt` 后重启服务即可。"
        )
    if not resolve_api_key(api_key):
        # Deliberately names only the .env path: the field is accepted by the
        # local settings API but the settings page has no input for it yet, and
        # sending someone to look for a box that is not there is worse than
        # telling them the file to edit.
        return (
            "还没有配置 Anthropic 的 API Key，无法请 Claude 看画面。"
            "在项目的 .env 里写入 ANTHROPIC_API_KEY=你的密钥，然后重启本地服务。"
            "没有这个 Key 时不会改用别的模型冒充「看过画面」。"
        )
    return None


def require_available(api_key: str | None = None) -> None:
    reason = unavailable_reason(api_key)
    if reason:
        raise ClaudeVisionError(reason, actionable=True)


def spread_across(frames: list[FrameInput], budget: int) -> list[FrameInput]:
    """The budget's worth, spread over the whole recording.

    Taking the first ``budget`` was harmless while the extractor handed over
    exactly that many, already spread. It stopped being harmless the moment the
    frame pass began keeping everything distinct so a file-reading channel could
    choose: a prefix of a hundred frames is the opening twenty minutes of a
    lecture, and a note written from it would describe the first fifth of the
    class with no sign that the rest was never looked at.

    This channel cannot go back for more — one request, images attached — so
    covering the whole recording thinly is the only honest way to spend a fixed
    budget on it.
    """
    if budget <= 0 or not frames:
        return []
    if len(frames) <= budget:
        return list(frames)
    if budget == 1:
        return [frames[0]]
    step = (len(frames) - 1) / (budget - 1)
    return [frames[round(index * step)] for index in range(budget)]


def _media_type(path: Path) -> str:
    return _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")


def image_block(frame: FrameInput) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": _media_type(frame.path),
            "data": base64.standard_b64encode(frame.path.read_bytes()).decode("ascii"),
        },
    }


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "未知时间"
    total = max(int(seconds), 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def build_user_content(
    transcript: str,
    frames: list[FrameInput],
) -> list[dict[str, Any]]:
    """Assemble the request body: transcript first, then each labelled picture.

    Each image is preceded by its own text block naming the file and timestamp,
    so the model can cite a frame by a name that exists on disk instead of
    inventing one. Public so a test can assert that both the transcript and the
    real image bytes are in what gets sent.
    """
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"以下是这段录像的转录文字：\n\n{transcript}",
        }
    ]
    if not frames:
        content.append({
            "type": "text",
            "text": "这次没有可用的截图，只能根据转录文字写笔记，请在 basis_note 里说明。",
        })
        return content
    content.append({
        "type": "text",
        "text": (
            f"以下是从这段录像里抽出的 {len(frames)} 张截图，按时间先后排列。"
            "引用时必须使用给出的文件名。"
        ),
    })
    for frame in frames:
        content.append({
            "type": "text",
            "text": f"文件名：{frame.filename}　时间点：{_clock(frame.timestamp_seconds)}",
        })
        content.append(image_block(frame))
    return content


def _response_text(message: Any) -> str:
    return "".join(
        block.text for block in getattr(message, "content", []) if getattr(block, "type", "") == "text"
    ).strip()


def write_visual_note(
    transcript: str,
    frames: list[FrameInput],
    *,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: Any | None = None,
    part: TranscriptPart | None = None,
) -> VisualNoteDraft:
    """Send the transcript and the frames to Claude and return the note it wrote.

    ``part`` says this request covers one stretch of a longer recording: its text
    is used as given rather than truncated, and the system prompt gains the
    instruction that keeps a section from being written as a whole note.

    Streamed because a full lecture note can run long, and a non-streaming
    request with this ``max_tokens`` risks an idle-connection timeout rather
    than a useful error. ``client`` is injectable so tests can assert on the
    request without a credential or a network.
    """
    require_available(api_key)
    text = (transcript or "").strip()
    if not text:
        raise ClaudeVisionError("这个任务还没有转录文字，无法生成笔记")
    if part is None:
        fitted = transcript_for_request(text)
        text = fitted.text
    else:
        text = part.text
    picked = spread_across(frames, MAX_FRAMES)
    chosen_model = (model or "").strip() or configured_model()

    active = client if client is not None else anthropic.Anthropic(api_key=resolve_api_key(api_key))
    request = {
        "model": chosen_model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT + (part_instruction(part) if part is not None else ""),
        "messages": [{"role": "user", "content": build_user_content(text, picked)}],
        "output_config": {"format": {"type": "json_schema", "schema": NOTE_SCHEMA}},
    }
    try:
        with active.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except ClaudeVisionError:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to something a user can act on
        raise ClaudeVisionError(_friendly(exc), actionable=_is_actionable(exc)) from exc

    # A safety decline arrives as a normal response, not an error, and its
    # content can be empty — read stop_reason before trusting content.
    if getattr(message, "stop_reason", None) == "refusal":
        raise ClaudeVisionError("Claude 拒绝了这次请求，没有生成笔记。可以换一段素材再试。")
    raw = _response_text(message)
    if not raw:
        raise ClaudeVisionError("Claude 没有返回内容，笔记未生成")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeVisionError("Claude 返回的内容无法解析，笔记未生成") from exc
    markdown = str(payload.get("note_markdown") or "").strip()
    if not markdown:
        raise ClaudeVisionError("Claude 返回的笔记是空的")
    return VisualNoteDraft(
        markdown=markdown,
        basis_note=str(payload.get("basis_note") or "").strip(),
        model=chosen_model,
        frames_sent=picked,
        transcript_chars=len(text),
        transcript_chars_dropped=fitted.chars_dropped,
        transcript_covered_until=fitted.covered_until,
        usage=_usage_from_message(message),
    )


_USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _usage_from_message(message: Any) -> dict[str, Any]:
    """The token counts this response reported, and nothing derived from them.

    No dollar figure: prices change on Anthropic's side, and a number computed
    from a price list frozen into this file would keep looking right long after
    it stopped being right. Tokens are the fact; whoever wants dollars applies a
    current price list to them.
    """
    reported = getattr(message, "usage", None)
    if reported is None:
        return {}
    usage: dict[str, Any] = {}
    for name in _USAGE_TOKEN_FIELDS:
        value = getattr(reported, name, None)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        usage[name] = value
    return usage


def _is_actionable(exc: Exception) -> bool:
    if anthropic is None:
        return False
    return isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError))


def _friendly(exc: Exception) -> str:
    """Map an SDK failure onto one sentence, without echoing any credential."""
    if anthropic is None:
        return "调用 Claude 失败"
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic 的 API Key 不被接受，请检查后重新填写。"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "这个 Anthropic 账号没有调用该模型的权限。"
    if isinstance(exc, anthropic.NotFoundError):
        return f"找不到模型 {configured_model()}，请检查模型名。"
    if isinstance(exc, anthropic.RateLimitError):
        return "Anthropic 接口暂时限流，稍后再试。"
    if isinstance(exc, anthropic.APIConnectionError):
        return "连不上 Anthropic 接口，请检查本机网络。"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Anthropic 接口返回了错误（{exc.status_code}）。"
    return f"调用 Claude 失败：{type(exc).__name__}"


__all__ = [
    "ClaudeVisionError",
    "DEFAULT_MODEL",
    "NOTE_SCHEMA",
    "SYSTEM_PROMPT",
    "FrameInput",
    "FRAME_INDEX_CAP",
    "FRAME_ATTACH_MAX",
    "MAX_FRAMES",
    "MAX_TRANSCRIPT_CHARS",
    "TranscriptPart",
    "transcript_parts",
    "part_instruction",
    "frames_for_part",
    "stamp_seconds",
    "VisualNoteDraft",
    "build_user_content",
    "configured_model",
    "image_block",
    "require_available",
    "resolve_api_key",
    "spread_across",
    "sdk_available",
    "unavailable_reason",
    "write_visual_note",
]
