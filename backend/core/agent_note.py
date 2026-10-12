"""The task's note, written by the user's own agent instead of a model FluentFlow pays.

Someone who installed FluentFlow through Claude Code or Codex already pays for a
capable model. Asking them to also create an Anthropic or DeepSeek account and
paste a key is the one setup step their agent cannot do for them, so this path
lets that agent write the note itself: it asks for the material, writes, and
hands the note back.

The material is exactly what the frame note sends to Claude, so the two paths
cannot drift apart in what a note is written from:

- the transcript on the clock of the file the note describes, split into the
  same parts a long recording is split into for one request each;
- the frames scene detection found in that file, as paths on this machine the
  agent can open, each with its moment in the recording;
- the note skill in effect (the user's own, else the product default), followed
  by the same fixed contract and, for a long recording, the same per-part
  instruction.

Saving goes through the frame note's own bookkeeping: the note becomes the
task's note, the note it replaces is kept and can be put back for free, which
frames it cites is measured from the markdown rather than taken from the agent,
and the provenance stamp says an agent wrote it. A draft too short for its
transcript is refused before anything is replaced, the same check the frame
note applies to Claude, so the agent gets the reason back and writes again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.core import claude_vision, cut_timeline, note_coverage, note_skills, visual_note_check
from backend.core import visual_note_job as vn
from backend.core.claude_vision import FrameInput
from backend.core.result_artifacts import VISUAL_NOTE_ARTIFACT_FILENAME, VISUAL_NOTE_KIND, write_text_artifact
from backend.core.result_schema import NOTE_FROM_AGENT
from backend.core.visual_note_channel import Channel

CHANNEL_AGENT = "agent"
CHANNEL_AGENT_LABEL = "你的 agent"

# describe() checks that something can write the note. Here the caller is the
# writer, so the check that matters is everything else: a finished task, usable
# media, a transcript on the right clock, no run already going.
_AGENT_CHANNEL = Channel(
    name=CHANNEL_AGENT,
    label=CHANNEL_AGENT_LABEL,
    model=CHANNEL_AGENT,
    unavailable_reason=None,
    write=lambda *_args, **_kwargs: None,
)

# How the agent hands the note back: field names match the save call, so the
# fixed contract's wording ("note_markdown", "basis_note") stays true here.
SAVE_INSTRUCTION = (
    "\n\n## 交回笔记\n\n"
    "写完后调用 save_agent_note：note_markdown 放笔记本身，basis_note 用一两句话说明依据"
    "（哪些部分靠画面、哪些只有字幕、有没有看不清或没信息的截图）。"
    "录像分成几部分时，每部分各取一次材料、各写一份，最后按顺序放进 part_notes 一次交回。"
    "截图在本机的路径就是 frames 里的 path，打开看过再决定引不引用；引用时只写文件名。"
    "\n\n交回之前，对照这部分转录逐条查一遍：讲到的概念、观点和依据、方法步骤、例子、数字、"
    "限制条件、讲者强调的地方，笔记里都要写到，没写到的补进去。"
    "交回后 FluentFlow 会再查一遍：返回的 coverage.missing 里列着它认为没写到的知识点，"
    "非空时把这些补进对应部分，再调用一次 save_agent_note。"
)


_SERVED_FRAME = re.compile(r"\(/jobs/[^)\s]*?[?&]file=(note_\d+\.jpg)\)")


class AgentNoteError(vn.VisualNoteError):
    pass


def _job(task_id: str, client_id: str | None) -> dict[str, Any]:
    job = vn.get_job(task_id, client_id=client_id)
    if not job:
        raise AgentNoteError("任务不存在")
    return job


@dataclass(frozen=True)
class _TranscriptOnly:
    """Stands in for the media of a task that has a transcript and no recording.

    A YouTube link whose captions were used, a subtitle file, a pasted
    transcript: there is no file to take frames from or to cut, and the note is
    written from the transcript alone, on the transcript's own clock.
    """

    has_video: bool = False
    kind: str = "transcript_only"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "has_video": False}


def _eligible(task_id: str, job: dict[str, Any]) -> tuple[Any, list[claude_vision.TranscriptPart], list]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    media = vn.cut_media(task_id, result)
    if media is None:
        if str(job.get("status") or "") not in vn.ELIGIBLE_STATUSES:
            raise AgentNoteError(f"只有已完成的任务能写笔记，这个任务现在是 {job.get('status') or '未知状态'}")
        if vn.is_running(result):
            raise AgentNoteError("这个任务的笔记正在重写中")
        segments = cut_timeline.timeline_segments(result)
        transcript = cut_timeline.timestamped_transcript(segments) if segments else ""
        if not transcript.strip():
            raise AgentNoteError(vn._cut_media_reason(task_id, result))
        return _TranscriptOnly(), claude_vision.transcript_parts(transcript), segments
    described = vn.describe(task_id, job, channel=_AGENT_CHANNEL)
    if not described["eligible"]:
        raise AgentNoteError(str(described["reason"]))
    transcript, _timeline, segments = vn._transcript_for_media(task_id, result, media)
    return media, claude_vision.transcript_parts(transcript), segments


def _stored_frames(task_id: str, result: dict[str, Any]) -> list[FrameInput] | None:
    """Frames an earlier run already put on disk, or ``None`` when there are none.

    Reused rather than extracted again: extraction rewrites the files under the
    same names, and the note the task has now may cite them.
    """
    records = vn.visual_note_state(result).get("frames_sent") or []
    frames: list[FrameInput] = []
    for record in records:
        if not isinstance(record, dict) or not record.get("filename"):
            continue
        path = vn._frame_artifact_path(task_id, str(record["filename"]))
        if not Path(str(path)).is_file():
            return None
        frames.append(FrameInput(
            filename=str(record["filename"]),
            path=Path(str(path)),
            timestamp_seconds=record.get("timestamp_seconds"),
            source=record.get("source"),
        ))
    return frames or None


def _frames(task_id: str, client_id: str | None, result: dict[str, Any], media: vn.CutMedia,
            segments: list, keyframe_extractor: Callable[..., Any],
            frame_collector: Callable[..., list[FrameInput]]) -> list[FrameInput]:
    if not media.has_video:
        return []
    stored = _stored_frames(task_id, result)
    if stored is not None:
        return stored
    frames = frame_collector(task_id, media.path, keyframe_extractor=keyframe_extractor, segments=segments)
    vn._store(task_id, client_id=client_id, state={
        "frames_sent": [vn._frame_record(task_id, frame) for frame in frames],
    })
    return frames


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = max(int(seconds), 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def material(
    task_id: str,
    *,
    part: int = 1,
    client_id: str | None = None,
    keyframe_extractor: Callable[..., Any],
    frame_collector: Callable[..., list[FrameInput]] = vn.collect_frames,
) -> dict[str, Any]:
    """Everything one part of the note is written from, and how to hand it back."""
    job = _job(task_id, client_id)
    media, parts, segments = _eligible(task_id, job)
    if not 1 <= part <= len(parts):
        raise AgentNoteError(f"这段录像分成 {len(parts)} 部分，part 要在 1 到 {len(parts)} 之间")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    frames = _frames(task_id, client_id, result, media, segments, keyframe_extractor, frame_collector)
    chosen = parts[part - 1]
    skill = note_skills.load_note_skill()
    return {
        "task_id": task_id,
        "title": result.get("display_title") or result.get("filename") or "",
        "part": chosen.index,
        "total_parts": chosen.total,
        "starts_at": chosen.starts_at,
        "ends_at": chosen.ends_at,
        "instructions": claude_vision.system_prompt(skill.rules) + claude_vision.part_instruction(chosen)
        + SAVE_INSTRUCTION,
        "transcript": chosen.text,
        "frames": [
            {"filename": frame.filename, "path": str(frame.path), "at": _clock(frame.timestamp_seconds)}
            for frame in claude_vision.frames_for_part(frames, chosen)
        ],
        "has_video": media.has_video,
        "skill": {"path": str(skill.path), "default": skill.is_default, "version": skill.version},
    }


def save(
    task_id: str,
    *,
    note_markdown: str | None = None,
    part_notes: list[str] | None = None,
    basis_note: str = "",
    model: str = "",
    client_id: str | None = None,
    checker: tuple[note_coverage.Chat, str] | None | bool = True,
) -> dict[str, Any]:
    """Make the agent's note the task's note, keeping the one it replaces.

    The note is saved whatever the coverage check finds; what it finds missing
    is recorded on the note's state as ``coverage`` for the caller to hand back
    to the agent. ``checker`` is who checks: ``True`` resolves it the way the
    frame note does (``vn.coverage_checker``), ``None`` checks nothing.
    """
    job = _job(task_id, client_id)
    media, parts, _segments = _eligible(task_id, job)
    drafts = list(part_notes or []) if part_notes else ([note_markdown] if note_markdown is not None else [])
    if len(drafts) != len(parts):
        raise AgentNoteError(
            f"这段录像分成 {len(parts)} 部分，要交回 {len(parts)} 份笔记（part_notes），收到 {len(drafts)} 份"
            if len(parts) > 1 else "没有收到笔记内容（note_markdown）"
        )
    cleaned = []
    for draft, part in zip(drafts, parts):
        # An agent editing the task's existing note hands back the served URLs
        # that note already had; turn them back into file names so the frames it
        # cites are counted and served the same way as freshly cited ones.
        markdown = _SERVED_FRAME.sub(r"(\1)", str(draft or ""))
        markdown = visual_note_check.strip_leading_residue(markdown)
        reason = visual_note_check.too_short_reason(markdown, part.text)
        if reason:
            where = f"第 {part.index} 部分" if len(parts) > 1 else "这份笔记"
            raise AgentNoteError(f"{where}{reason.replace('Claude 返回的笔记', '')}，没有替换现有笔记，请重写后再交")
        cleaned.append(claude_vision.VisualNoteDraft(markdown=markdown, basis_note=basis_note, model=model))
    coverage = _coverage(cleaned, parts, checker)
    joined = vn._joined_draft(cleaned, parts, title=vn.note_title(job))

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    frames = (_stored_frames(task_id, result) or []) if media.has_video else []
    cited = vn.cited_frames(joined.markdown, frames)
    markdown = vn._rewrite_image_targets(joined.markdown, task_id, frames)
    artifact = write_text_artifact(task_id, VISUAL_NOTE_KIND, VISUAL_NOTE_ARTIFACT_FILENAME, markdown.rstrip() + "\n")
    skill = note_skills.load_note_skill()
    result_fields, replaced = vn._promotion(result, markdown, media_kind=media.kind)
    result_fields["summary_written_from"] = NOTE_FROM_AGENT
    state = {
        "status": vn.STATUS_COMPLETED,
        "stage": "done",
        "markdown": markdown,
        "model": model or CHANNEL_AGENT,
        "provider": CHANNEL_AGENT,
        "channel": CHANNEL_AGENT,
        "channel_label": CHANNEL_AGENT_LABEL,
        "media_source": media.as_dict(),
        "basis": vn.BASIS_BOTH if cited else vn.BASIS_TRANSCRIPT_ONLY,
        "basis_note": basis_note,
        "skill_path": str(skill.path),
        "skill_default": skill.is_default,
        "skill_version": skill.version,
        "frames_sent": [vn._frame_record(task_id, frame) for frame in frames],
        "frames_opened": [],
        "frames_cited": [vn._frame_record(task_id, frame) for frame in cited],
        "cited_without_opening": [],
        "transcript_chars": sum(len(part.text) for part in parts),
        "transcript_chars_dropped": 0,
        "transcript_covered_until": "",
        "usage": {},
        "coverage": coverage,
        "promoted": True,
        "replaced_note": replaced,
        "finished_at": vn._now(),
        "error": None,
    }
    return vn._store(
        task_id,
        client_id=client_id,
        state=state,
        artifacts={VISUAL_NOTE_KIND: artifact},
        result_fields=result_fields,
        rewrite_artifacts=True,
    )


def _coverage(drafts, parts, checker) -> dict[str, Any]:
    """Each part checked against its own transcript, nothing rewritten: the
    agent wrote the note, so the agent fills what is missing."""
    if checker is True:
        checker = vn.coverage_checker()
    if not checker:
        return {"checker": "", "parts": [
            {"part": part.index, **note_coverage.unchecked("本机没有可用来查漏的模型，请按材料里的要求自己查漏")}
            for part in parts
        ], "missing": []}
    chat, name = checker
    records = []
    missing = []
    for draft, part in zip(drafts, parts):
        check = note_coverage.check_against_transcript(chat, part.text, draft.markdown)
        records.append({"part": part.index, **check.as_record()})
        missing.extend({"part": part.index, **item} for item in check.missing)
    return {"checker": name, "parts": records, "missing": missing}


__all__ = ["AgentNoteError", "CHANNEL_AGENT", "material", "save"]
