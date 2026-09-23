"""Write the task's note from the de-breathed version of its recording.

This is the second half of one flow, and the first half is not optional. The
breath gaps come out of the recording first, producing a shortened file and a
cut list; then the note is written from *that* file — its pictures, and the
transcript moved onto its clock. There is no path here that reads the original
recording, and that is the product decision rather than an implementation
detail: a note that was written from the full recording but presented next to
the shortened one leaves the user holding two versions of the same lecture and
no way to tell which the note describes.

Three things are enforced rather than trusted, because each is easy to fake:

- The media is the cut file. Frames are extracted from it, so a frame's label is
  a moment in the file the user can play. No cut file means the flow refuses and
  names its first step instead of quietly falling back to the original.
- The subtitles are remapped. The transcript's timestamps belong to the original
  recording and are simply wrong for a shortened file, so they go through
  ``cut_timeline`` first, and what that cost — lines dropped, lines shortened —
  is recorded rather than smoothed over.
- An audio-only task is told it has no pictures and gets a note written from its
  subtitles alone, recorded as such. What it must never get is that same note
  under a label claiming something looked at a screen.

The rest of the order is: check, extract, ask, record. Everything that can
refuse does so before a paid request is made, and every refusal names the thing
to go and fix — an unfinished task, a cut file no longer on the machine, or no
way to reach Claude at all. It never falls back to another provider, because
which model read this material is the whole claim. Which of the two Claude
channels paid is chosen by ``visual_note_channel``, shown in the preview before
the button, and recorded on the finished note.

Which frames the note cites is measured from the note, not reported by the
model: the markdown is scanned for image references that resolve to frames that
were actually sent, and ``basis`` is derived from that count. A model that says
it read the pictures and cites none is recorded as transcript-only.

Frames are sampled by ffmpeg scene detection — mechanical, and the honest
description of it is "where the picture changed", not "the important moments".
Choosing which of them matters is left to the model that can see them.

The note this produces becomes the task's note, because the flow's whole point
is that there is one note and it describes the file the user now has. The note
it replaces is never destroyed: it is kept on the record that replaced it and
can be put back. Nothing here replaces anything without the caller having asked
— the page asks under a button that says so, and an agent has to pass the flag.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core import claude_vision, cut_timeline, debreath_job, visual_note_channel
from backend.core.claude_vision import ClaudeVisionError, FrameInput
from backend.core.visual_note_channel import Channel
from backend.core.job_store import get_job, list_jobs_by_statuses, update_job_result
from backend.core.result_artifacts import (
    DEBREATH_MEDIA_KIND,
    TRANSCRIPT_MEDIA_CUT,
    VISUAL_NOTE_ARTIFACT_FILENAME,
    VISUAL_NOTE_ARTIFACT_SUFFIXES,
    VISUAL_NOTE_KIND,
    _attach_result_artifacts,
    _write_file_artifact,
    describe_existing_artifact,
    resolve_artifact_file,
    write_text_artifact,
)
from backend.core.storage_paths import _artifact_storage_dir, find_source_file, in_place_source_path

logger = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

BASIS_BOTH = "transcript_and_frames"
BASIS_TRANSCRIPT_ONLY = "transcript_only"

# Which file the note was written from. Recorded on the note, because months
# later "does this describe the recording I uploaded or the shortened one" is not
# answerable from the note's text.
MEDIA_CUT = "debreath_media"
MEDIA_SOURCE_UNCHANGED = "source_no_cuts"

# The provenance stamp left on the task's own note when this flow wrote it.
SUMMARY_WRITTEN_FROM = "debreath_media_note"

# Only a finished task has a transcript that is done being written and a source
# that was fully fetched.
ELIGIBLE_STATUSES = frozenset({"completed"})

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".mpg", ".mpeg"}

# Frames are copied in under their own prefix rather than written straight into
# the shared frames/ directory: the original screenshot pipeline's frames live
# there too, and scene extraction deletes scene_*.jpg in whatever directory it
# is pointed at.
_FRAME_PREFIX = "note"

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

_running: set[str] = set()


class VisualNoteError(RuntimeError):
    """A reason worth showing the user, in their language."""

    def __init__(self, message: str, *, actionable: bool = False) -> None:
        super().__init__(message)
        self.actionable = actionable


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def visual_note_state(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    state = result.get("visual_note")
    return dict(state) if isinstance(state, dict) else {}


def is_running(result: Any) -> bool:
    return visual_note_state(result).get("status") == STATUS_RUNNING


def claim(task_id: str) -> None:
    """Reserve this task's slot, or refuse in the caller's words.

    Public so an HTTP route can claim while the requester is still waiting on
    the response. A background task that claims for itself has already answered
    "accepted" by the time it refuses, where nobody sees it.
    """
    if task_id in _running:
        raise VisualNoteError("这个任务的视觉笔记正在生成中，等它结束再提交")
    _running.add(task_id)


def release(task_id: str) -> None:
    """Give the slot back. Idempotent."""
    _running.discard(task_id)


@dataclass(frozen=True)
class CutMedia:
    """The file this note will be written from, and how it came to be.

    ``unchanged`` is the one case where the file is the recording the user
    uploaded: the cut list found nothing quiet enough to remove, so the shortened
    version and the original are the same audio on the same clock. Saying that
    out loud is the honest version — the alternative would be a flow that dead
    ends on well-edited material, or one that silently reads the original while
    the page claims otherwise.
    """

    path: Path
    filename: str
    has_video: bool
    unchanged: bool
    render_verified: bool | None = None

    @property
    def kind(self) -> str:
        return MEDIA_SOURCE_UNCHANGED if self.unchanged else MEDIA_CUT

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "filename": self.filename,
            "has_video": self.has_video,
            "unchanged": self.unchanged,
            "render_verified": self.render_verified,
            "artifact_kind": None if self.unchanged else DEBREATH_MEDIA_KIND,
        }


def _task_artifact_dir(task_id: str) -> Path:
    return _artifact_storage_dir() / task_id


def cut_media(task_id: str, result: dict[str, Any]) -> CutMedia | None:
    """The de-breathed file for this task, or ``None`` with no guessing.

    Deliberately strict. Everything this returns has been found on disk, so a
    caller cannot end up extracting frames from a file that is not there, and it
    never substitutes the original recording for a missing cut file — the whole
    point of the flow is which file the note read.
    """
    state = debreath_job.debreath_state(result)
    if state.get("status") != debreath_job.STATUS_COMPLETED:
        return None
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}

    def recording() -> CutMedia | None:
        """The recording, as this note's material: FluentFlow's own copy, or the
        user's file where it sits for a task submitted by path."""
        source = find_source_file(task_id) or in_place_source_path(get_job(task_id))
        if not source:
            return None
        return CutMedia(
            path=source,
            filename=source.name,
            has_video=source.suffix.lower() in VIDEO_SUFFIXES,
            unchanged=True,
        )

    # The note reads whatever the transcript describes. That is the whole rule, and
    # it is why a declined cut no longer costs the user their note: when the spot
    # check found the cuts too close to the speech (or the render failed its own
    # check), the transcript was made from the recording — so the note is about the
    # recording, and refusing to write one left a 50-minute task with a transcript
    # and an error message. Reported that way, on a faintly recorded meeting.
    if state.get("used_for_transcription") is False:
        return recording()
    if not state.get("rendered"):
        # Nothing was quiet enough to remove, so the recording already is the
        # shortened version. A plan-only run has no file to read and is refused by
        # the sentence in `_cut_media_reason`.
        if plan.get("cut_count") == 0:
            return recording()
        return None
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    record = artifacts.get(DEBREATH_MEDIA_KIND)
    filename = str((record or {}).get("filename") or state.get("media_filename") or "")
    path = resolve_artifact_file(_task_artifact_dir(task_id), filename)
    if path is None:
        return None
    return CutMedia(
        path=path,
        filename=filename or path.name,
        has_video=path.suffix.lower() in VIDEO_SUFFIXES,
        unchanged=False,
        render_verified=state.get("render_verified"),
    )


def _missing_recording_reason(task_id: str, why: str) -> str:
    job = get_job(task_id) or {}
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    origin = metadata.get("folder_intake") if isinstance(metadata.get("folder_intake"), dict) else {}
    recorded = str(origin.get("original_path") or "").strip()
    if recorded:
        return f"{why}，但它已经不在 {recorded} 了。把文件放回这个位置，再写笔记。"
    return f"{why}，但本机已经找不到这个任务的录音了（可能已过保留期）。重新提交这个文件再写笔记。"


def _cut_media_reason(task_id: str, result: dict[str, Any]) -> str:
    """Why there is no file to write from, as the step to go and do.

    Every branch names the first half of the flow, because that is the answer in
    every case: this note is written from the shortened file, and there is not
    one yet.
    """
    state = debreath_job.debreath_state(result)
    status = state.get("status")
    if not state:
        return "还没有去掉气口的版本。先在媒体那一栏跑一次「去气口」，笔记会根据剪好的文件来写。"
    if status == debreath_job.STATUS_RUNNING:
        return "去气口还在进行，等剪后的文件出来再写笔记。"
    if status == debreath_job.STATUS_FAILED:
        reason = str(state.get("error") or "").strip()
        return f"上一次去气口没成功{('：' + reason) if reason else ''}。先把那一步跑通，再写笔记。"
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    if state.get("used_for_transcription") is False:
        return _missing_recording_reason(task_id, "这次转写用的是原录音，笔记也要读原录音")
    if not state.get("rendered") and plan.get("cut_count") == 0:
        return _missing_recording_reason(task_id, "去气口没找到可剪的空白，笔记要读原录音")
    if not state.get("rendered"):
        return "上一次只生成了剪辑表，还没有剪后的文件。回到「去气口」点「剪掉空白并生成成品」。"
    return "剪后的文件已经不在本机了（可能已过保留期）。重新跑一次「去气口」就能再写笔记。"


def _cut_list_payload(task_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """The stored cut list, read from disk. ``None`` when it is not there.

    The job row keeps only counts, so the ranges needed to move a timestamp come
    from the artifact. Without it the remap cannot be done, and it is not
    something to approximate.
    """
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    record = artifacts.get(debreath_job.CUT_LIST_KIND)
    path = resolve_artifact_file(_task_artifact_dir(task_id), (record or {}).get("filename"))
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _transcript_is_native_to_cut_media(result: dict[str, Any]) -> bool:
    """Whether this transcript was produced from the cut file in the first place.

    True for anything the local edition cut before transcribing: the timestamps
    are the cut file's already. Read from the result, because "a cut list exists"
    does not distinguish the two orders, and converting a native transcript would
    shift every timestamp a second time.
    """
    return str(result.get("transcript_media") or "") == TRANSCRIPT_MEDIA_CUT


def _transcript_for_media(
    task_id: str, result: dict[str, Any], media: CutMedia
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """The transcript on the clock of the file being read, and what that cost.

    The lines themselves come back alongside the text, because the frame pass
    needs their start times on this same clock and deciding that clock twice is
    how the pictures end up captioned by the wrong half of the recording.

    Three cases, and which one applies is recorded rather than inferred:

    - the transcript came *from* this file (the cut ran before transcription):
      used as it stands, because it is already that file's clock;
    - nothing was cut, so the recording is the file: used as it stands;
    - otherwise every timestamp moves through the cut list, and a missing cut list
      refuses rather than sending the recording's timings alongside the shortened
      file's pictures.
    """
    segments = cut_timeline.timeline_segments(result)
    if not segments:
        return "", {"source": cut_timeline.TIMELINE_SOURCE_ORIGINAL, "segments": 0, "chars": 0}, []
    if _transcript_is_native_to_cut_media(result):
        text = cut_timeline.timestamped_transcript(segments)
        return text, {
            "source": cut_timeline.TIMELINE_SOURCE_NATIVE,
            "segments": len(segments),
            "source_segments": len(segments),
            "dropped_segments": 0,
            "shortened_segments": 0,
            "chars": sum(len(str(item.get("text") or "")) for item in segments),
        }, segments
    if media.unchanged:
        text = cut_timeline.timestamped_transcript(segments)
        return text, {
            "source": cut_timeline.TIMELINE_SOURCE_ORIGINAL,
            "segments": len(segments),
            "source_segments": len(segments),
            "dropped_segments": 0,
            "shortened_segments": 0,
            "chars": sum(len(str(item.get("text") or "")) for item in segments),
        }, segments
    payload = _cut_list_payload(task_id, result)
    if payload is None:
        raise VisualNoteError(
            "找不到这次去气口的剪辑表，没法把字幕时间点对到剪后的文件上。重新跑一次「去气口」即可。"
        )
    report = cut_timeline.remap_segments(segments, cut_timeline.kept_ranges(payload))
    if not report.segments:
        raise VisualNoteError("按剪辑表重算之后，字幕没有剩下可用的内容，笔记未生成。")
    return cut_timeline.timestamped_transcript(report.segments), report.as_dict(), report.segments


def describe(
    task_id: str,
    job: dict[str, Any],
    *,
    api_key: str | None = None,
    channel: Channel | None = None,
) -> dict[str, Any]:
    """What would happen if this ran, without spending anything.

    This is the cheap half of the entry, and it exists for four reasons: running
    it spends the user's own model allowance, it sends stills and subtitles of
    their recording to an outside service, it replaces the note the task
    currently has, and which credential pays is a choice they should see before
    it is made rather than after. All four belong on screen before the button.

    The numbers here are the real ones, not a template: the transcript size is
    counted *after* the timestamps have been moved onto the cut file's clock, and
    the frame budget is zero for material that has no picture. A preview that
    promised twenty frames for an audio recording would be the same lie the run
    is built to avoid.
    """
    active = channel or visual_note_channel.resolve_channel(api_key)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    status = str(job.get("status") or "")
    state = visual_note_state(result)
    current_note = str(result.get("summary_markdown") or "").strip()

    payload: dict[str, Any] = {
        "task_id": task_id,
        "eligible": False,
        "reason": None,
        "model": active.model,
        "channel": active.name,
        "channel_label": active.label,
        "frame_budget": 0,
        "transcript_chars": 0,
        "transcript_chars_dropped": 0,
        "transcript_covered_until": "",
        "transcript_parts": 0,
        "media": None,
        "subtitle_timeline": None,
        "media_warnings": [],
        # Whether anything at all can pay for the run. On the subscription
        # channel that is a login, not a key, and the user configured nothing.
        "credential_configured": active.available,
        "source_filename": None,
        "status": state.get("status"),
        "has_note": bool(state.get("markdown")),
        # The flow produces the task's note, so what happens to the one already
        # there is part of the preview rather than a surprise afterwards.
        "will_replace_note": True,
        "current_note_chars": len(current_note),
        "current_note_edited": bool(result.get("summary_edited")),
        "restorable_note": bool(
            str((state.get("replaced_note") or {}).get("previous_markdown") or "").strip()
        ) if isinstance(state.get("replaced_note"), dict) else False,
    }

    if status not in ELIGIBLE_STATUSES:
        payload["reason"] = f"只有已完成的任务能重写笔记，这个任务现在是 {status or '未知状态'}"
        return payload

    media = cut_media(task_id, result)
    if media is None:
        payload["reason"] = _cut_media_reason(task_id, result)
        return payload
    payload["media"] = media.as_dict()
    payload["source_filename"] = Path(media.filename).name
    payload["frame_budget"] = claude_vision.MAX_FRAMES if media.has_video else 0
    if media.render_verified is False:
        payload["media_warnings"].append(
            "剪后的文件和剪辑表对不上，文件保留了下来但没通过自检。先听一遍再用它写笔记。"
        )
    if media.unchanged:
        state = debreath_job.debreath_state(result)
        reason = str(state.get("not_used_reason") or "").strip()
        payload["media_warnings"].append(
            # Two different reasons land on the same file, and the difference matters
            # to the user: nothing worth cutting is a clean recording, while a
            # declined cut means the threshold did not suit the material.
            f"这次按原文件写笔记：{reason}"
            if reason
            else "这份材料没有找到可剪的空白，所以剪后的版本和原文件是同一份，字幕时间点也不用改。"
        )

    try:
        transcript, timeline, _segments = _transcript_for_media(task_id, result, media)
    except VisualNoteError as exc:
        payload["reason"] = str(exc)
        return payload
    payload["subtitle_timeline"] = timeline
    parts = claude_vision.transcript_parts(transcript)
    payload["transcript_chars"] = sum(len(part.text) for part in parts)
    payload["transcript_chars_dropped"] = 0
    payload["transcript_covered_until"] = ""
    payload["transcript_parts"] = len(parts)
    if len(parts) > 1:
        # Said here, in the free preview, because this is where the number that
        # changes — what the run will cost — is still actionable. It is no longer
        # a warning that content will be missing: it says the recording needs more
        # than one request and will get them.
        payload["media_warnings"].append(
            f"这段录像一次请求装不下，会分 {len(parts)} 段来写，最后合成一份笔记。"
            f"覆盖是完整的，代价是这一次要发 {len(parts)} 次请求。"
        )
    if not transcript:
        payload["reason"] = "这个任务没有带时间点的转录文字，没法写这份笔记"
        return payload
    if timeline.get("dropped_segments"):
        payload["media_warnings"].append(
            f"有 {timeline['dropped_segments']} 句字幕整句落在被剪掉的段里，已经不在剪后的音频里，笔记不会用它们。"
        )
    if active.unavailable_reason:
        payload["reason"] = active.unavailable_reason
        return payload
    if is_running(result):
        payload["reason"] = "这个任务的笔记正在重写中"
        return payload

    payload["eligible"] = True
    return payload


def _require_eligible(
    task_id: str, job: dict[str, Any], *, api_key: str | None, channel: Channel
) -> tuple[CutMedia, str, dict[str, Any], list[dict[str, Any]]]:
    """Refuse before anything is spent, and hand back what the run needs.

    The media and the remapped transcript come from here rather than being looked
    up again inside the run, so the run reads exactly what the preview described.
    """
    preview = describe(task_id, job, api_key=api_key, channel=channel)
    if not preview["eligible"]:
        raise VisualNoteError(
            str(preview["reason"]),
            # A channel that cannot run is the one case the user can fix right
            # now — by logging in, or by configuring a key.
            actionable=not channel.available,
        )
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    media = cut_media(task_id, result)
    if media is None:  # pragma: no cover - describe already refused this
        raise VisualNoteError(_cut_media_reason(task_id, result))
    transcript, timeline, segments = _transcript_for_media(task_id, result, media)
    return media, transcript, timeline, segments


def _store(task_id: str, *, client_id: str | None, state: dict[str, Any],
           artifacts: dict[str, dict[str, Any]] | None = None,
           result_fields: dict[str, Any] | None = None,
           rewrite_artifacts: bool = False) -> dict[str, Any]:
    """Merge one transition into the job result and persist it.

    ``result_fields`` is how the finished note becomes the task's note. It writes
    outside the ``visual_note`` block, so it is deliberately explicit and used on
    exactly one transition; ``rewrite_artifacts`` then refreshes the downloadable
    note file so the download and the page cannot disagree.
    """
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise VisualNoteError("任务不存在")
    result = dict(job.get("result") or {})
    result["visual_note"] = {**visual_note_state(result), **state}
    if result_fields:
        result.update(result_fields)
    if rewrite_artifacts:
        result = _attach_result_artifacts(task_id, result)
    if artifacts:
        result["artifacts"] = {**dict(result.get("artifacts") or {}), **artifacts}
    updated = update_job_result(task_id, result, client_id=client_id)
    if not updated:
        raise VisualNoteError("任务不存在")
    return updated


def _promotion(result: dict[str, Any], markdown: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make this note the task's note, keeping the one it displaces.

    The previous note is copied onto the record that replaced it rather than
    dropped. That is what makes this reversible: the page offers to put it back,
    and a note somebody hand-edited is never gone because a rewrite ran.
    """
    previous = str(result.get("summary_markdown") or "")
    replaced = {
        "previous_markdown": previous,
        "previous_summary_edited": bool(result.get("summary_edited")),
        "replaced_at": _now(),
    }
    fields = {
        "summary_markdown": markdown,
        "summary_skipped": False,
        "summary_status": STATUS_COMPLETED,
        "summary_error": None,
        # Not an edit: nobody typed this. The stamp says which flow wrote it, so
        # the page can say what the note describes.
        "summary_written_from": SUMMARY_WRITTEN_FROM,
    }
    return fields, replaced


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cue_seconds(segments: list[dict[str, Any]] | None) -> list[float]:
    """The moment each line starts, on the clock the caller already resolved.

    Times only. What was said is deliberately not read here — see
    ``frame_extractor.ANCHOR_SNAP_SECONDS`` for why a transcript is the wrong
    thing to let choose where this path looks.
    """
    cues: list[float] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start"))
        except (TypeError, ValueError):
            continue
        if start >= 0:
            cues.append(round(start, 1))
    return sorted(set(cues))


def collect_frames(
    task_id: str,
    source: Path,
    *,
    keyframe_extractor: Callable[..., Any],
    segments: list[dict[str, Any]] | None = None,
) -> list[FrameInput]:
    """Sample frames where the picture changes, and register them as artifacts.

    The extraction policy is injected rather than imported, the way ``media_job``
    takes one: the local edition's policy and the hosted one are different
    modules, and picking one here would tie this module to an edition. It also
    means a runtime with extraction switched off answers through the policy's
    own ``skipped_reason``, which is turned into a sentence below.

    Extraction runs in a temporary directory and the survivors are copied into
    the task's frames/ under their own prefix, so this never deletes or renames
    frames the original screenshot pipeline left there.

    Frames ffmpeg's own measurements call blank or near-blank are dropped. That
    filter is mechanical and stays mechanical — nothing here decides whether a
    picture is *worth* citing, because that judgement needs eyes on the picture.
    """
    frames: list[FrameInput] = []
    with tempfile.TemporaryDirectory(prefix="fluentflow-visual-note-") as scratch:
        extraction = keyframe_extractor(
            str(source),
            Path(scratch),
            # Not ``segments``: that parameter grabs three frames around every
            # window it is given, which for a lecture's worth of subtitle cues is
            # thousands of grabs. The cue *times* go in as anchors instead, which
            # moves the looks this pass was already going to take onto the moments
            # somebody was speaking, and costs nothing extra.
            segments=None,
            anchor_seconds=_cue_seconds(segments),
            scene_threshold=0.3,
            # Everything distinct the recording has, not one request's worth. A
            # channel that can open files decides for itself which of these are
            # worth a look; a channel that cannot takes the first MAX_FRAMES. The
            # extractor's own screenless budget still applies, so a talking head
            # does not produce a hundred webcam shots to choose between.
            max_scene_frames=claude_vision.FRAME_INDEX_CAP,
            min_gap_seconds=2.0,
        )
        if extraction.skipped_reason:
            raise VisualNoteError(
                f"没能从剪后的视频里抽出画面（{extraction.skipped_reason}），笔记未生成"
            )
        usable = [
            frame for frame in extraction.frames
            if isinstance(frame, dict) and frame.get("path") and frame.get("low_information") is not True
        ]
        for index, frame in enumerate(usable[: claude_vision.FRAME_INDEX_CAP], start=1):
            filename = f"{_FRAME_PREFIX}_{index:04d}.jpg"
            _write_file_artifact(task_id, "frame", f"frames/{filename}", str(frame["path"]))
            timestamp = frame.get("timestamp_seconds")
            try:
                seconds = float(timestamp) if timestamp is not None else None
            except (TypeError, ValueError):
                seconds = None
            frames.append(
                FrameInput(
                    filename=filename,
                    path=Path(str(_frame_artifact_path(task_id, filename))),
                    timestamp_seconds=seconds,
                    source=(str(frame.get("source")) if frame.get("source") else None),
                    scene_threshold=_as_float(frame.get("scene_threshold")),
                    detail=_as_float(frame.get("edge_contrast")),
                    change_percent=_as_float(frame.get("duplicate_distance")),
                )
            )
    if not frames:
        raise VisualNoteError("剪后的视频里没有抽到可用的画面，笔记未生成")
    # No text is read off these. ``frame_text`` was written so a *text index*
    # could describe each frame to a model choosing which few to open; every
    # frame is attached now, so the model looks at the pictures instead of
    # reading about them, and a minute of recognition per note would buy a column
    # nobody reads. The module and its tests stay: the API-key channel still
    # cannot attach more than twenty and still has to choose, and an index is the
    # better answer than the mechanical thinning it does today.
    return frames


def _frame_artifact_path(task_id: str, filename: str) -> Path:
    return _artifact_storage_dir() / task_id / "frames" / filename


def _frame_record(task_id: str, frame: FrameInput) -> dict[str, Any]:
    return {
        "filename": frame.filename,
        "timestamp_seconds": frame.timestamp_seconds,
        "source": frame.source,
        "scene_threshold": frame.scene_threshold,
        "change_percent": frame.change_percent,
        "new_text": frame.new_text,
        "detail": frame.detail,
        "url": describe_existing_artifact(task_id, "frame", f"frames/{frame.filename}")["url"],
    }


def cited_frames(markdown: str, frames: list[FrameInput]) -> list[FrameInput]:
    """Which sent frames the note actually references.

    Measured from the note rather than taken from the model's own account of
    what it used, because that number is what the word "画面" in the result
    means. A reference to a name that was never sent does not count.
    """
    sent = {frame.filename: frame for frame in frames}
    seen: list[FrameInput] = []
    for match in _IMAGE_RE.finditer(markdown or ""):
        name = Path(str(match.group(1)).split("?")[0]).name
        frame = sent.get(name)
        if frame and frame not in seen:
            seen.append(frame)
    return seen


def _rewrite_image_targets(markdown: str, task_id: str, frames: list[FrameInput]) -> str:
    """Point the note's image references at the artifact URLs that serve them."""
    urls = {
        frame.filename: describe_existing_artifact(task_id, "frame", f"frames/{frame.filename}")["url"]
        for frame in frames
    }

    def replace(match: re.Match[str]) -> str:
        name = Path(str(match.group(1)).split("?")[0]).name
        url = urls.get(name)
        if not url:
            return match.group(0)
        return match.group(0).replace(match.group(1), url)

    return _IMAGE_RE.sub(replace, markdown or "")


def _joined_draft(drafts, parts):
    """One note out of the parts, without paying a model to staple them together.

    Each part was told to write only its own stretch and to start at a second
    level heading, so joining is mechanical: the sections follow each other in
    recording order under whatever title the first part gave. A stitching pass
    would cost another request to delete introductions the first pass had been
    instructed not to write.

    The counts reported are the whole run's, not the last part's, so "how much of
    this recording did the note actually read" stays answerable afterwards.
    """
    if not drafts:
        raise VisualNoteError("没有生成任何笔记内容")
    first = drafts[0]
    if len(drafts) == 1:
        return first
    sections = []
    for draft, part in zip(drafts, parts):
        body = (draft.markdown or "").strip()
        if not body:
            continue
        span = (
            f"（{part.starts_at}–{part.ends_at}）"
            if part.starts_at and part.ends_at
            else ""
        )
        sections.append(f"<!-- 第 {part.index}/{part.total} 部分{span} -->\n\n{body}")
    markdown = "\n\n".join(sections)
    frames_sent = [frame for draft in drafts for frame in draft.frames_sent]
    frames_opened = [name for draft in drafts for name in draft.frames_opened]
    return replace(
        first,
        markdown=markdown,
        frames_sent=frames_sent,
        frames_opened=frames_opened,
        transcript_chars=sum(draft.transcript_chars for draft in drafts),
        transcript_chars_dropped=0,
        transcript_covered_until="",
    )


def run_visual_note(
    task_id: str,
    *,
    keyframe_extractor: Callable[..., Any],
    client_id: str | None = None,
    api_key: str | None = None,
    claimed: bool = False,
    replace_note: bool = True,
    frame_collector: Callable[..., list[FrameInput]] = collect_frames,
    writer: Callable[..., Any] | None = None,
    channel: Channel | None = None,
) -> dict[str, Any]:
    """Read the cut file, ask Claude to write from it, record what it read.

    Runs to completion in the calling thread; an HTTP caller should hand it to a
    background task. Every transition is persisted, so an ordinary job poll
    shows where it is.

    ``replace_note`` is what makes this the task's note rather than another one
    beside it. It defaults to on because that is the flow's purpose, and the
    caller is expected to have said so first — the page's button carries the
    sentence, and the displaced note is kept either way.

    ``writer`` still overrides everything, for tests. Left to itself, the run
    goes through whichever channel ``visual_note_channel`` resolved, and the
    name of that channel is written into the result — so "which Claude wrote
    this, and who paid" is answerable from the stored note months later, not
    only from whatever the settings happened to be at the time.
    """
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise VisualNoteError("任务不存在")
    active = channel or visual_note_channel.resolve_channel(api_key)
    write = writer if writer is not None else active.write
    media, transcript, timeline, segments = _require_eligible(
        task_id, job, api_key=api_key, channel=active
    )
    if not claimed:
        claim(task_id)

    try:
        _store(
            task_id,
            client_id=client_id,
            state={
                "status": STATUS_RUNNING,
                "stage": "extracting",
                "started_at": _now(),
                "model": active.model,
                "provider": "anthropic",
                "channel": active.name,
                "channel_label": active.label,
                "media_source": media.as_dict(),
                "subtitle_timeline": timeline,
                "error": None,
            },
        )
        # No picture, no frames, and no pretending otherwise. An audio task gets a
        # note written from the subtitles of the file it actually has, recorded
        # below as transcript-only because nothing looked at a screen.
        frames = (
            frame_collector(
                task_id, media.path,
                keyframe_extractor=keyframe_extractor,
                segments=segments,
            )
            if media.has_video
            else []
        )
        _store(
            task_id,
            client_id=client_id,
            state={
                "stage": "writing",
                "frames_sent": [_frame_record(task_id, frame) for frame in frames],
            },
        )
        # A recording is never too long to write about, only too long for one
        # request. Truncating was the old answer and it produced the worst kind
        # of wrong result: a note that stops two thirds of the way through and
        # reads like a finished one. Each part is written from its own stretch of
        # transcript and its own pictures, and the parts are joined below.
        parts = claude_vision.transcript_parts(transcript)
        drafts = []
        for part in parts:
            part_frames = claude_vision.frames_for_part(frames, part)
            if len(parts) > 1:
                _store(
                    task_id,
                    client_id=client_id,
                    state={"stage": f"writing {part.index}/{part.total}"},
                )
            drafts.append(write(part.text, part_frames, api_key=api_key, part=part))
        draft = _joined_draft(drafts, parts)
        cited = cited_frames(draft.markdown, frames)
        markdown = _rewrite_image_targets(draft.markdown, task_id, frames)
        artifact = write_text_artifact(
            task_id, VISUAL_NOTE_KIND, VISUAL_NOTE_ARTIFACT_FILENAME, markdown.rstrip() + "\n"
        )
        state: dict[str, Any] = {
            "status": STATUS_COMPLETED,
            "stage": "done",
            "markdown": markdown,
            "model": draft.model,
            "channel": active.name,
            "channel_label": active.label,
            "media_source": media.as_dict(),
            "subtitle_timeline": timeline,
            # Whether this note's own transcript carried speaker labels. Read
            # from the text that was actually sent, not from the task's
            # diarization payload: those are two different questions, and only
            # this one answers "could this note have said who spoke".
            "speaker_labeled": cut_timeline.SPEAKER_LABEL_PREAMBLE in transcript,
            "basis": BASIS_BOTH if cited else BASIS_TRANSCRIPT_ONLY,
            "basis_note": draft.basis_note,
            # Offered, opened, cited — three different numbers now, and the gaps
            # between them are the interesting part. Everything distinct the
            # recording had was offered; the model opened what it judged worth a
            # look, which only a channel with a file tool can do; it cited what
            # ended up carrying a sentence. A frame cited without being opened is
            # the failure this records rather than asserts against.
            "frames_sent": [_frame_record(task_id, frame) for frame in frames],
            "frames_opened": list(getattr(draft, "frames_opened", []) or []),
            "frames_cited": [_frame_record(task_id, frame) for frame in cited],
            "cited_without_opening": sorted(
                {frame.filename for frame in cited}
                - set(getattr(draft, "frames_opened", []) or [])
            ) if getattr(draft, "frames_opened", None) else [],
            "transcript_chars": draft.transcript_chars,
            # Kept on the finished note as well as in the preview: someone reading
            # this note a week later has no preview to go back to, and a note that
            # covers half a lecture must say so on its face.
            "transcript_chars_dropped": draft.transcript_chars_dropped,
            "transcript_covered_until": draft.transcript_covered_until,
            "usage": dict(draft.usage),
            "promoted": bool(replace_note),
            "finished_at": _now(),
            "error": None,
        }
        result_fields: dict[str, Any] | None = None
        if replace_note:
            current = get_job(task_id, client_id=client_id) or {}
            current_result = current.get("result") if isinstance(current.get("result"), dict) else {}
            result_fields, state["replaced_note"] = _promotion(current_result, markdown)
        return _store(
            task_id,
            client_id=client_id,
            state=state,
            artifacts={VISUAL_NOTE_KIND: artifact},
            result_fields=result_fields,
            rewrite_artifacts=bool(result_fields),
        )
    except (VisualNoteError, ClaudeVisionError) as exc:
        _store(
            task_id,
            client_id=client_id,
            state={"status": STATUS_FAILED, "stage": "done", "error": str(exc), "finished_at": _now()},
        )
        raise VisualNoteError(str(exc), actionable=getattr(exc, "actionable", False)) from exc
    except Exception as exc:  # noqa: BLE001 - the job must not be left "running"
        logger.exception("visual note failed for %s", task_id)
        _store(
            task_id,
            client_id=client_id,
            state={
                "status": STATUS_FAILED,
                "stage": "done",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": _now(),
            },
        )
        raise
    finally:
        release(task_id)


def restore_previous_note(task_id: str, *, client_id: str | None = None) -> dict[str, Any]:
    """Put back the note this flow replaced.

    Free, and the reason the replacement is allowed to be the default. The note
    written from the cut file stays on the record, so this is a switch between
    two notes that both exist rather than an undo that loses one.
    """
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise VisualNoteError("任务不存在")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    state = visual_note_state(result)
    replaced = state.get("replaced_note") if isinstance(state.get("replaced_note"), dict) else {}
    previous = str(replaced.get("previous_markdown") or "")
    if not previous.strip():
        raise VisualNoteError("没有可以恢复的上一版笔记")
    return _store(
        task_id,
        client_id=client_id,
        state={
            "promoted": False,
            "replaced_note": {**replaced, "restored_at": _now()},
        },
        result_fields={
            "summary_markdown": previous,
            "summary_skipped": False,
            "summary_status": STATUS_COMPLETED,
            "summary_error": None,
            # The restored note was not written from the cut file, so the stamp
            # that says it was has to go with it.
            "summary_written_from": None,
            "summary_edited": bool(replaced.get("previous_summary_edited")),
        },
        rewrite_artifacts=True,
    )


def use_generated_note(task_id: str, *, client_id: str | None = None) -> dict[str, Any]:
    """Make the already-written note the task's note again, without a new run.

    Exists so changing one's mind after a restore costs nothing. Rewriting would
    spend the user's allowance to produce a note that is already sitting in the
    result.
    """
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise VisualNoteError("任务不存在")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    state = visual_note_state(result)
    markdown = str(state.get("markdown") or "")
    if not markdown.strip():
        raise VisualNoteError("这个任务还没有根据剪后版本写好的笔记")
    fields, replaced = _promotion(result, markdown)
    previous_record = state.get("replaced_note") if isinstance(state.get("replaced_note"), dict) else {}
    if not str(replaced.get("previous_markdown") or "").strip():
        # Nothing to keep this time round, so the earlier record is the one worth
        # holding on to — losing it would make the restore button lie.
        replaced = previous_record or replaced
    return _store(
        task_id,
        client_id=client_id,
        state={"promoted": True, "replaced_note": replaced},
        result_fields=fields,
        rewrite_artifacts=True,
    )


def recover_stranded_notes() -> int:
    """Fail any visual note left saying "running" by a previous process.

    The slot lives in process memory, so a service killed mid-request leaves
    ``running`` in a result nothing will ever finish — and the entry refuses a
    task in that state, so it would be dead for good. Cleared at startup for the
    same reason ``recover_stale_jobs`` clears stranded tasks.
    """
    recovered = 0
    for job in list_jobs_by_statuses(("completed",), include_result=True):
        task_id = str(job.get("task_id") or "")
        if not task_id or not is_running(job.get("result")):
            continue
        result = dict(job.get("result") or {})
        result["visual_note"] = {
            **visual_note_state(result),
            "status": STATUS_FAILED,
            "stage": "done",
            "error": "服务重启中断了这次笔记重写，剪后的文件还在，重新发起即可。",
            "finished_at": _now(),
        }
        # The automatic flow marks the note "pending" before it starts; left
        # alone, the task would say a note is on its way forever.
        if result.get("summary_status") == "pending":
            if str(result.get("summary_markdown") or "").strip():
                result["summary_status"] = "completed"
            else:
                result["summary_status"] = "failed"
                result["summary_error"] = "服务重启中断了笔记生成，重新写一次笔记即可。"
        if update_job_result(task_id, result, client_id=job.get("client_id")):
            recovered += 1
    return recovered


__all__ = [
    "BASIS_BOTH",
    "BASIS_TRANSCRIPT_ONLY",
    "ELIGIBLE_STATUSES",
    "MEDIA_CUT",
    "MEDIA_SOURCE_UNCHANGED",
    "SUMMARY_WRITTEN_FROM",
    "VISUAL_NOTE_ARTIFACT_SUFFIXES",
    "VISUAL_NOTE_KIND",
    "CutMedia",
    "VisualNoteError",
    "cited_frames",
    "claim",
    "collect_frames",
    "cut_media",
    "describe",
    "is_running",
    "recover_stranded_notes",
    "release",
    "restore_previous_note",
    "run_visual_note",
    "use_generated_note",
    "visual_note_state",
]
