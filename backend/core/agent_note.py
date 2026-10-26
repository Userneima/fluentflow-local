"""The task's note, written by the user's own agent instead of a model FluentFlow pays.

Someone who installed FluentFlow through Claude Code or Codex already pays for a
capable model. Asking them to also create an Anthropic or DeepSeek account and
paste a key is the one setup step their agent cannot do for them, so this path
lets that agent write the note itself: it asks for the material, writes, and
hands the note back.

The material is exactly what the frame note sends to Claude, so the two paths
cannot drift apart in what a note is written from:

- the transcript on the clock of the file the note describes, whole by default
  (owner, 2026-10-16: a note is no strain for an agent, and the host's limit on
  one tool result can be raised), or, for a host that cannot take that much in
  one result, split into parts small enough that one arrives whole
  (``AGENT_PART_CHARS``, ``small_parts``);
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

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.core import claude_vision, correction_notes, cut_timeline, note_coverage, note_skills, note_versions, visual_note_check
from backend.core import model_usage, task_steps
from backend.core import visual_note_job as vn
from backend.core.claude_vision import FrameInput
from backend.core.result_artifacts import (
    VISUAL_NOTE_ARTIFACT_FILENAME,
    VISUAL_NOTE_KIND,
    artifact_target_path,
    write_text_artifact,
)
from backend.core.result_schema import NOTE_FROM_AGENT, content_versions
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

# A part is sized for the agent's host, not for a request FluentFlow makes. Claude
# Code hands a tool result over 25,000 tokens to the agent as a file to read in
# pieces, and its file reader stops at the same size; Chinese runs about one
# token a character (measured 2026-10-14: a 60,000-character part came to 54,766
# tokens and arrived as a file). 15,000 characters leaves room for the
# instructions, the user's own rules and the list of frames in the same answer.
# The frame note's own requests keep claude_vision's larger parts.
AGENT_PART_CHARS = 15_000

# Parts saved so far, kept beside the task's other artifacts so a conversation
# that was compacted, or a new one, carries on from them.
_PARTS_FILENAME = "agent_note_parts.json"

# Present while this task's material is handed over in small parts: the save
# calls that follow must split the transcript the way the material did.
_SMALL_PARTS_FILENAME = "agent_note_small_parts"

# How the agent hands the note back: field names match the save call, so the
# fixed contract's wording ("note_markdown", "basis_note") stays true here.
SAVE_INSTRUCTION = (
    "\n\n## 交回笔记\n\n"
    "写完后调用 save_agent_note：note_markdown 放笔记本身，basis_note 用一两句话说明依据"
    "（哪些部分靠画面、哪些只有字幕、有没有看不清或没信息的截图）。"
    "录像分成几部分时，每写完一部分就带上 part（这一部分的序号）保存，不用等全部写完；"
    "存过的部分留在 FluentFlow 里，换了对话也能接着写下一部分，全部存齐后才替换任务的笔记。"
    "截图在本机的路径就是 frames 里的 path，打开看过再决定引不引用；引用时只写文件名。"
    "\n\n交回之前，按上面写法里「不漏」一节对照这部分转录逐条查一遍，没写到的写进讲它的那一段。"
    "第一次交回后 FluentFlow 会查一遍：返回的 coverage.missing 里列着它认为没写到的知识点，"
    "非空时把值得写的补进去，再保存一次；补完再存不会再查。"
)

# Said once the last part is in. Each part only saw its own stretch, so what the
# parts share is what a reader trips over: the same conclusion twice, a term
# explained in two places, a question raised early and answered later.
READ_THROUGH_INSTRUCTION = (
    "所有部分已经拼成一篇，成了这个任务的笔记。最后通读一遍：用 get_agent_note_part 逐部分取回已存的笔记"
    "（只有笔记，没有转录），找出几部分之间重复的结论、重复解释的术语（只在第一次出现的地方解释）、"
    "前面提出后面才回答的问题。改动落在哪一部分，就用 save_agent_note 带同一个 part 重新保存那一部分；"
    "没有要改的就结束。"
)


_SERVED_FRAME = re.compile(r"\(/jobs/[^)\s]*?[?&]file=(note_\d+(?:_crop_[0-9a-f]{8})?\.jpg)\)")


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


def _small_parts(task_id: str) -> bool:
    return artifact_target_path(task_id, _SMALL_PARTS_FILENAME).is_file()


def _set_small_parts(task_id: str, small: bool) -> None:
    marker = artifact_target_path(task_id, _SMALL_PARTS_FILENAME)
    if small:
        marker.write_text("1\n", encoding="utf-8")
    else:
        marker.unlink(missing_ok=True)


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
        return _TranscriptOnly(), _parts(transcript), segments
    described = vn.describe(task_id, job, channel=_AGENT_CHANNEL)
    if not described["eligible"]:
        raise AgentNoteError(str(described["reason"]))
    transcript, _timeline, segments = vn._transcript_for_media(task_id, result, media)
    return media, _parts(transcript, small=_small_parts(task_id)), segments


def _parts(transcript: str, *, small: bool = False) -> list[claude_vision.TranscriptPart]:
    size = AGENT_PART_CHARS if small else len(transcript) + 1
    return claude_vision.transcript_parts(transcript, max_chars=size)


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
    small_parts: bool = False,
    client_id: str | None = None,
    keyframe_extractor: Callable[..., Any],
    frame_collector: Callable[..., list[FrameInput]] = vn.collect_frames,
) -> dict[str, Any]:
    """Everything the note is written from, and how to hand it back: the whole
    recording, or with ``small_parts`` one part of it."""
    job = _job(task_id, client_id)
    _set_small_parts(task_id, small_parts)
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
        "instructions": claude_vision.system_prompt(correction_notes.rules_for(task_id, skill.rules, client_id))
        + claude_vision.part_instruction(chosen)
        + SAVE_INSTRUCTION,
        "transcript": chosen.text,
        "frames": [
            {"filename": frame.filename, "path": str(frame.path), "at": _clock(frame.timestamp_seconds)}
            for frame in claude_vision.frames_for_part(frames, chosen)
        ],
        "has_video": media.has_video,
        "skill": {"path": str(skill.path), "default": skill.is_default, "version": skill.version},
        # The note this material was read against: pass it back as
        # ``base_version`` when saving, so an edit made meanwhile is not replaced.
        "note_version": content_versions(result).get("note_version"),
    }


def _cleaned(draft: str | None, part: claude_vision.TranscriptPart, *, where: str,
             basis_note: str, model: str) -> claude_vision.VisualNoteDraft:
    # An agent editing the task's existing note hands back the served URLs
    # that note already had; turn them back into file names so the frames it
    # cites are counted and served the same way as freshly cited ones.
    markdown = _SERVED_FRAME.sub(r"(\1)", str(draft or ""))
    markdown = visual_note_check.strip_leading_residue(markdown)
    reason = visual_note_check.too_short_reason(markdown, part.text)
    if reason:
        raise AgentNoteError(f"{where}{reason.replace('Claude 返回的笔记', '')}，没有替换现有笔记，请重写后再交")
    return claude_vision.VisualNoteDraft(markdown=markdown, basis_note=basis_note, model=model)


@model_usage.works_for(task_steps.NOTE)
def save(
    task_id: str,
    *,
    note_markdown: str | None = None,
    part_notes: list[str] | None = None,
    basis_note: str = "",
    model: str = "",
    client_id: str | None = None,
    checker: tuple[note_coverage.Chat, str] | None | bool = True,
    base_version: str | None = None,
) -> dict[str, Any]:
    """Make the agent's note the task's note, keeping the one it replaces.

    Three shapes: ``note_markdown`` alone for a one-part recording or for a whole
    note an agent edited; ``part_notes`` with every part at once. A note written
    part by part goes through ``save_part`` instead.

    The note is saved whatever the coverage check finds; what it finds missing
    is recorded on the note's state as ``coverage`` for the caller to hand back
    to the agent. ``checker`` is who checks: ``True`` resolves it the way the
    frame note does (``vn.coverage_checker``), ``None`` checks nothing.
    """
    job = _job(task_id, client_id)
    # The note version the agent read before writing: checked now, before the
    # coverage check spends a model call, and again in the write itself.
    expect = vn.check_base(job.get("result"), base_version)
    media, parts, _segments = _eligible(task_id, job)
    kept = _kept_from_earlier_save(job)
    if part_notes:
        if len(part_notes) != len(parts):
            raise AgentNoteError(
                f"这段录像分成 {len(parts)} 部分，要交回 {len(parts)} 份笔记（part_notes），收到 {len(part_notes)} 份"
            )
        cleaned = [
            _cleaned(draft, part, where=f"第 {part.index} 部分" if len(parts) > 1 else "这份笔记",
                     basis_note=basis_note, model=model)
            for draft, part in zip(part_notes, parts)
        ]
        coverage = _not_again(parts) if kept else _coverage(cleaned, parts, checker)
        updated = _finish(task_id, job, media, parts, cleaned, coverage,
                          basis_note=basis_note, model=model, client_id=client_id, keep_replaced=kept,
                          expect_versions=expect)
        _write_store(task_id, parts, {
            part.index: _stored(part, draft.markdown, record)
            for part, draft, record in zip(parts, cleaned, coverage["parts"])
        }, joined=_note_of(updated), kept_replaced=kept or _replaced_of(updated), basis_note=basis_note, model=model)
        return updated
    if note_markdown is None:
        raise AgentNoteError("没有收到笔记内容（note_markdown）")
    if len(parts) == 1:
        cleaned = [_cleaned(note_markdown, parts[0], where="这份笔记", basis_note=basis_note, model=model)]
        coverage = _not_again(parts) if kept else _coverage(cleaned, parts, checker)
        updated = _finish(task_id, job, media, parts, cleaned, coverage,
                          basis_note=basis_note, model=model, client_id=client_id, keep_replaced=kept,
                          expect_versions=expect)
        _write_store(task_id, parts, {1: _stored(parts[0], cleaned[0].markdown, coverage["parts"][0])},
                     joined=_note_of(updated), kept_replaced=kept or _replaced_of(updated),
                     basis_note=basis_note, model=model)
        return updated
    # A whole note for a recording in several parts: an agent fixing the note it
    # read. It is taken as it is, measured against the whole transcript; the
    # parts saved earlier no longer describe the note, so they go.
    whole = claude_vision.TranscriptPart(text="\n".join(part.text for part in parts), index=1, total=1)
    cleaned = [_cleaned(note_markdown, whole, where="这份笔记", basis_note=basis_note, model=model)]
    coverage = {"checker": "", "parts": [
        {"part": part.index, **note_coverage.unchecked("整篇交回的修改，没有逐部分查漏")} for part in parts
    ], "missing": []}
    updated = _finish(task_id, job, media, parts, cleaned, coverage, basis_note=basis_note, model=model,
                      client_id=client_id, joined_markdown=cleaned[0].markdown, keep_replaced=kept,
                      expect_versions=expect)
    # The saved parts no longer describe the note; what is kept is which note
    # an agent's later save must still be able to put back.
    _write_store(task_id, [], {}, joined=_note_of(updated), kept_replaced=kept or _replaced_of(updated),
                 basis_note=basis_note, model=model)
    return updated


def _kept_from_earlier_save(job: dict[str, Any]) -> dict[str, Any] | None:
    """The note from before the agent started, when the task's note is still
    the one the agent saved last: saving again (a coverage fill, a fix) must not
    make the agent's own earlier draft the note to put back (found 2026-10-16:
    after three whole saves, restoring gave back the agent's second draft)."""
    store = _read_store(job.get("task_id") or "") if job.get("task_id") else {}
    joined = store.get("joined_hash")
    if not joined or joined != _hash(_note_of(job)):
        return None
    return store.get("kept_replaced") or None


@model_usage.works_for(task_steps.NOTE)
def save_part(
    task_id: str,
    *,
    part: int,
    note_markdown: str | None,
    basis_note: str = "",
    model: str = "",
    client_id: str | None = None,
    checker: tuple[note_coverage.Chat, str] | None | bool = True,
    base_version: str | None = None,
) -> dict[str, Any]:
    """Keep one part of the note; once every part is in, they become the task's note.

    Saving as each part is written keeps a long recording from having to sit in
    the agent's conversation whole: the conversation can be compacted, or a new
    one started, and nothing written is lost. Until the last part arrives the
    task's note is left alone.

    Saving a part again after the note was joined updates that part in the note,
    which is how the read-through and the coverage fill land; the note that can
    be put back stays the one from before the agent's first join, also when a
    transcript edit in between made some parts be written again.

    When the task's note changed after the join (an edit in the editor, another
    writer), the saved parts no longer describe it. They are dropped and this
    part starts a new round: nothing replaces the changed note until every part
    is in again, and then the changed note is the one that can be put back.
    """
    job = _job(task_id, client_id)
    # Checked on every part: the note the parts will replace once joined is
    # the one the agent read.
    expect = vn.check_base(job.get("result"), base_version)
    media, parts, _segments = _eligible(task_id, job)
    if not note_markdown:
        raise AgentNoteError("没有收到这一部分的笔记内容（note_markdown）")
    if not 1 <= part <= len(parts):
        raise AgentNoteError(f"这段录像分成 {len(parts)} 部分，part 要在 1 到 {len(parts)} 之间")
    target = parts[part - 1]
    where = f"第 {part} 部分" if len(parts) > 1 else "这份笔记"
    draft = _cleaned(note_markdown, target, where=where, basis_note=basis_note, model=model)
    store = _read_store(task_id)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    joined = store.get("joined_hash")
    if joined and joined != _hash(str(result.get("summary_markdown") or "")):
        store = {}
        joined = None
    saved = _valid_parts(store, parts)
    coverage = _not_again([target]) if part in saved else _coverage([draft], [target], checker)
    saved[part] = _stored(target, draft.markdown, coverage["parts"][0])
    basis_note = basis_note or str(store.get("basis_note") or "")
    model = model or str(store.get("model") or "")
    remaining = [p.index for p in parts if p.index not in saved]
    answer: dict[str, Any] = {
        "part": part,
        "total_parts": len(parts),
        "remaining_parts": remaining,
        "coverage": {"checker": coverage["checker"], "parts": coverage["parts"], "missing": coverage["missing"]},
        "completed": not remaining,
        "job": None,
    }
    kept = store.get("kept_replaced") if joined else None
    if remaining:
        _write_store(task_id, parts, saved, joined_hash=joined, kept_replaced=kept,
                     basis_note=basis_note, model=model)
        answer["next"] = f"第 {part} 部分已保存。还差第 {'、'.join(str(i) for i in remaining)} 部分，写完一部分存一部分。"
        return answer
    cleaned = [
        claude_vision.VisualNoteDraft(markdown=saved[p.index]["markdown"], basis_note=basis_note, model=model)
        for p in parts
    ]
    combined = {"checker": coverage["checker"], "parts": [saved[p.index]["coverage"] for p in parts],
                "missing": [{"part": p.index, **item}
                            for p in parts for item in (saved[p.index]["coverage"].get("missing") or [])]}
    updated = _finish(task_id, job, media, parts, cleaned, combined, basis_note=basis_note, model=model,
                      client_id=client_id, keep_replaced=kept, expect_versions=expect)
    _write_store(task_id, parts, saved, joined=_note_of(updated), kept_replaced=kept or _replaced_of(updated),
                 basis_note=basis_note, model=model)
    answer["job"] = updated
    answer["next"] = READ_THROUGH_INSTRUCTION if len(parts) > 1 else "笔记已保存。"
    return answer


def saved_part(task_id: str, *, part: int, client_id: str | None = None) -> dict[str, Any]:
    """One part of the note as it was saved, without its transcript: what the
    read-through reads, a part at a time, so it fits where the whole note would not."""
    job = _job(task_id, client_id)
    _media, parts, _segments = _eligible(task_id, job)
    if not 1 <= part <= len(parts):
        raise AgentNoteError(f"这段录像分成 {len(parts)} 部分，part 要在 1 到 {len(parts)} 之间")
    saved = _valid_parts(_read_store(task_id), parts)
    if part not in saved:
        raise AgentNoteError(f"第 {part} 部分还没有保存过（或转录改过之后没有重新保存）")
    return {
        "part": part,
        "total_parts": len(parts),
        "starts_at": parts[part - 1].starts_at,
        "ends_at": parts[part - 1].ends_at,
        "markdown": saved[part]["markdown"],
        "coverage": saved[part]["coverage"],
    }


def _finish(task_id, job, media, parts, cleaned, coverage, *, basis_note: str, model: str,
            client_id: str | None, joined_markdown: str | None = None,
            keep_replaced: dict[str, Any] | None = None,
            expect_versions: dict[str, str] | None = None) -> dict[str, Any]:
    joined = joined_markdown if joined_markdown is not None else (
        vn._joined_draft(cleaned, parts, title=vn.note_title(job)).markdown
    )
    # The task as it is now, not as it was before the coverage check: an edit
    # saved during the check is the note this one replaces, and stays
    # restorable (audit, 2026-10-18).
    job = vn.get_job(task_id, client_id=client_id) or job
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    frames = (_stored_frames(task_id, result) or []) if media.has_video else []
    joined, frames = vn.apply_crops(joined, task_id, frames)
    cited = vn.cited_frames(joined, frames)
    markdown = vn._rewrite_image_targets(joined, task_id, frames)
    artifact = write_text_artifact(task_id, VISUAL_NOTE_KIND, VISUAL_NOTE_ARTIFACT_FILENAME, markdown.rstrip() + "\n")
    skill = note_skills.load_note_skill()
    result_fields, replaced = vn._promotion(result, markdown, media_kind=media.kind, by=note_versions.AGENT_NOTE)
    # A part saved again after the join replaces the agent's own earlier join;
    # the note to put back is still the one from before the agent started.
    if keep_replaced:
        replaced = keep_replaced
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
        "switch_back_edited": None,
        "switch_back_written_from": None,
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
        expect_versions=expect_versions,
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stored(part: claude_vision.TranscriptPart, markdown: str, coverage: dict[str, Any]) -> dict[str, Any]:
    return {"transcript_hash": _hash(part.text), "markdown": markdown, "coverage": coverage}


def _replaced_of(job: dict[str, Any]) -> dict[str, Any] | None:
    """The note an agent's join displaced, as the task records it for restoring."""
    return vn.visual_note_state(job.get("result")).get("replaced_note") or None


def _note_of(job: dict[str, Any]) -> str:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return str(result.get("summary_markdown") or "")


def _read_store(task_id: str) -> dict[str, Any]:
    path = artifact_target_path(task_id, _PARTS_FILENAME)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid_parts(store: dict[str, Any], parts: list[claude_vision.TranscriptPart]) -> dict[int, dict[str, Any]]:
    """The saved parts that still match the transcript as it is now split.

    A part is kept only while its stretch of transcript is unchanged: after an
    edit to the transcript, or a change in how it splits, the saved note no
    longer describes that stretch and is written again.
    """
    saved = store.get("parts") if isinstance(store.get("parts"), dict) else {}
    if store.get("total_parts") != len(parts):
        return {}
    valid = {}
    for part in parts:
        entry = saved.get(str(part.index))
        if isinstance(entry, dict) and entry.get("transcript_hash") == _hash(part.text):
            valid[part.index] = entry
    return valid


def _write_store(task_id: str, parts, saved: dict[int, dict[str, Any]], *, joined: str | None = None,
                 joined_hash: str | None = None, kept_replaced: dict[str, Any] | None = None,
                 basis_note: str, model: str) -> None:
    """``joined`` is the task's note right after a join; ``joined_hash`` carries an
    earlier join forward while parts are being written again. ``kept_replaced``
    is the note from before the agent's first join, the one restore puts back."""
    path = artifact_target_path(task_id, _PARTS_FILENAME)
    path.write_text(json.dumps({
        "total_parts": len(parts),
        "parts": {str(index): entry for index, entry in sorted(saved.items())},
        "joined_hash": _hash(joined) if joined is not None else joined_hash,
        "kept_replaced": kept_replaced,
        "basis_note": basis_note,
        "model": model,
    }, ensure_ascii=False), encoding="utf-8")


def _drop_store(task_id: str) -> None:
    artifact_target_path(task_id, _PARTS_FILENAME).unlink(missing_ok=True)


def _not_again(parts) -> dict[str, Any]:
    """A note checked once is not checked again when the agent saves it after
    filling what was missing or fixing it (owner, 2026-10-16). Each check lists
    different points, so every re-check reported new gaps, mostly the details
    of examples, and every one cost a model call and minutes."""
    return {"checker": "", "parts": [
        {"part": part.index, **note_coverage.unchecked("补写或修改后再存，不再重查：每篇只查一遍")} for part in parts
    ], "missing": []}


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
    rules = note_skills.load_note_skill().rules
    records = []
    missing = []
    for draft, part in zip(drafts, parts):
        check = note_coverage.check_against_transcript(chat, part.text, draft.markdown, rules=rules)
        records.append({"part": part.index, **check.as_record()})
        missing.extend({"part": part.index, **item} for item in check.missing)
    return {"checker": name, "parts": records, "missing": missing}


__all__ = ["AGENT_PART_CHARS", "AgentNoteError", "CHANNEL_AGENT", "material", "save", "save_part", "saved_part"]
