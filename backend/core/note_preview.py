"""Write a task's note with two versions of a note skill, side by side.

Used before any skill change takes effect: the user's own skill when they
confirm a rule, and the product default when it is edited
(``scripts/evaluate_note_quality.py --compare-skills``). Nothing here touches
the task's note, its visual-note state, or its frames:

- The frames are the ones the task's last frame note already extracted, read
  from disk. Extracting again would rewrite the files the current note cites,
  and the two versions must see the same pictures for the comparison to mean
  anything.
- The drafts are returned, not stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.core import claude_vision, visual_note_channel
from backend.core import visual_note_job as vn
from backend.core.claude_vision import FrameInput


class PreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillDraft:
    label: str
    markdown: str
    model: str
    cited: list[str]
    usage: dict[str, Any]


def _existing_frames(task_id: str, result: dict[str, Any]) -> list[FrameInput]:
    state = vn.visual_note_state(result)
    frames: list[FrameInput] = []
    for record in state.get("frames_sent") or []:
        if not isinstance(record, dict) or not record.get("filename"):
            continue
        path = Path(str(vn._frame_artifact_path(task_id, str(record["filename"]))))
        if path.is_file():
            frames.append(FrameInput(
                filename=str(record["filename"]),
                path=path,
                timestamp_seconds=record.get("timestamp_seconds"),
                source=record.get("source"),
            ))
    return frames


def draft_with_rules(
    task_id: str,
    rules: str,
    *,
    label: str,
    client_id: str | None = None,
    api_key: str | None = None,
    writer: Callable[..., Any] | None = None,
) -> SkillDraft:
    """One draft of this task's note, written with ``rules``."""
    job = vn.get_job(task_id, client_id=client_id)
    if not job:
        raise PreviewError("任务不存在")
    channel = visual_note_channel.resolve_channel(api_key)
    try:
        media, transcript, _timeline, _segments = vn._require_eligible(task_id, job, api_key=api_key, channel=channel)
    except vn.VisualNoteError as exc:
        raise PreviewError(str(exc)) from exc
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    frames = _existing_frames(task_id, result) if media.has_video else []
    write = writer if writer is not None else channel.write
    parts = claude_vision.transcript_parts(transcript)
    drafts = [
        vn._checked_draft(write, part, claude_vision.frames_for_part(frames, part), api_key=api_key, rules=rules)
        for part in parts
    ]
    draft = vn._joined_draft(drafts, parts)
    return SkillDraft(
        label=label,
        markdown=draft.markdown,
        model=draft.model,
        cited=[frame.filename for frame in vn.cited_frames(draft.markdown, frames)],
        usage=dict(draft.usage),
    )


def compare(
    task_id: str,
    old_rules: str,
    new_rules: str,
    **kwargs: Any,
) -> tuple[SkillDraft, SkillDraft]:
    """The same task written with the old and the new rules."""
    return (
        draft_with_rules(task_id, old_rules, label="现在的版本", **kwargs),
        draft_with_rules(task_id, new_rules, label="改动后的版本", **kwargs),
    )


__all__ = ["PreviewError", "SkillDraft", "compare", "draft_with_rules"]
