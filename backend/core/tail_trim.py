"""Cut off what was recorded after the meeting or lecture ended.

Requirement (owner, 2026-10-15). A recording is often left running after
everyone says goodbye: a 38-minute operations meeting ended after about 13
minutes of content, and its cut video still carried ten minutes of keyboard,
mouse and someone else's call after the "拜拜". The silences were removed; those
minutes were not silence.

Where the content ends is a judgement about meaning, so a model makes it, not a
rule such as "after the last 拜拜": some meetings say goodbye and then add one
more thing, some never say goodbye. It reads the timed transcript of the
recording's second half, after transcription and before the note, so the note
is written without the leftover either.

What it changes, only when the model names an end that leaves at least
``MIN_TAIL_SECONDS`` behind it:

- the cut video ends one second after the last line of content (stream copy, no
  re-encode), in FluentFlow's store and in the user's output folder;
- the transcript and subtitles stop at the same point;
- the task records where it ended, how much was removed and why.

The original recording is never touched, so a wrong judgement loses nothing.
Recordings that were not cut (the original was transcribed) are left alone.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from backend.core import job_store, silence_cuts
from backend.core.storage_paths import _artifact_storage_dir

logger = logging.getLogger(__name__)

# Shorter recordings rarely run on; the judgement is not worth a request.
MIN_DURATION_SECONDS = 180.0
# Less leftover than this is not worth changing the files for.
MIN_TAIL_SECONDS = 30.0
# At most this share of the transcript's characters may fall after the cut. A
# judgement that would drop more is a misreading, and nothing is cut. Where the
# content ends is not limited by time: in the operations meeting it ended at a
# third of the cut video, with almost nothing said after it.
MAX_SPEECH_SHARE_AFTER = 0.25
# Kept after the last line of content, so the goodbye is not clipped.
AFTER_LAST_LINE_SECONDS = 1.0
# How much of the transcript's end the model reads.
MAX_TRANSCRIPT_CHARS = 12000
REQUEST_DEADLINE_SECONDS = 300.0

SYSTEM = """你会读到一段录像转录稿的最后部分，每行开头是这句话在录像里的秒数，开头还会告诉你录像总长和最后一句之后还有多久没人说话。
判断正式内容（会议、课程、讲座、访谈）在哪里结束。
结束之后如果只剩会后的闲聊、和主题无关的零碎话、没关录制留下的内容，就给出最后一句正式内容那一行的秒数。
内容一直持续到最后，或者你拿不准，ends_at 填 null。宁可不裁，也不要裁掉正式内容。
说完再见又补充了正事的，以补充的那句为准。
只回一个 JSON 对象：{"ends_at": 秒数或 null, "reason": "一句话说明"}"""

Chat = Callable[[str, str], str]


def _media_path(task_id: str, result: dict[str, Any]) -> Path | None:
    record = (result.get("artifacts") or {}).get("debreath_media")
    name = str((record or {}).get("filename") or "").strip()
    if not name or ".." in Path(name).parts:
        return None
    path = _artifact_storage_dir() / task_id / name
    return path if path.is_file() else None


def _transcript_tail(segments: list[dict[str, Any]], duration: float) -> str:
    """The last lines of the transcript, wherever they fall, and how long the
    recording runs on after the last of them."""
    lines = [
        f"[{float(seg.get('start') or 0):.1f}] {str(seg.get('text') or '').strip()}"
        for seg in segments
        if str(seg.get("text") or "").strip()
    ]
    text = "\n".join(lines)[-MAX_TRANSCRIPT_CHARS:]
    last_end = max((float(seg.get("end") or 0) for seg in segments), default=0.0)
    return (
        f"录像总长 {duration:.0f} 秒。最后一句转录在 {last_end:.0f} 秒结束，"
        f"之后的 {max(0.0, duration - last_end):.0f} 秒没有任何转录出来的说话。\n\n{text}"
    )


def _speech_share_after(segments: list[dict[str, Any]], seconds: float) -> float:
    total = sum(len(str(seg.get("text") or "").strip()) for seg in segments)
    after = sum(len(str(seg.get("text") or "").strip()) for seg in segments if float(seg.get("start") or 0) >= seconds)
    return after / total if total else 0.0


def _answer(text: str) -> tuple[float | None, str]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("回答里没有 JSON")
    payload = json.loads(match.group(0))
    value = payload.get("ends_at")
    reason = str(payload.get("reason") or "").strip()
    if value is None:
        return None, reason
    return float(value), reason


def _default_chat() -> tuple[Chat, str] | None:
    from backend.core import visual_note_job
    from backend.core.local_config import resolve_secret

    return visual_note_job.coverage_checker(resolve_secret(None, "anthropic_api_key"))


def _ask(chat: Chat, user: str) -> str:
    from backend.core.note_coverage import _within_deadline

    return _within_deadline(chat, SYSTEM, user, REQUEST_DEADLINE_SECONDS)


def _cut_at(segments: list[dict[str, Any]], ends_at: float) -> float:
    """One second after the end of the last line that starts by ``ends_at``."""
    last = max(
        (float(seg.get("end") or 0) for seg in segments if float(seg.get("start") or 0) <= ends_at + 0.01),
        default=ends_at,
    )
    return last + AFTER_LAST_LINE_SECONDS


def _shorten(media: Path, seconds: float) -> None:
    """``media`` ending at ``seconds``, by stream copy, replaced in place."""
    temp = media.with_name(f".{media.stem}.tail{media.suffix}")
    completed = subprocess.run(
        [silence_cuts._ffmpeg_path(), "-v", "error", "-y", "-i", str(media), "-t", f"{seconds:.3f}",
         "-map", "0", "-c", "copy", "-movflags", "+faststart", str(temp)],
        capture_output=True, text=True, timeout=1800,
    )
    if completed.returncode != 0:
        temp.unlink(missing_ok=True)
        raise RuntimeError((completed.stderr or "").strip()[-300:] or "ffmpeg 失败")
    written = silence_cuts.media_duration_seconds(temp)
    if abs(written - seconds) > 2.0:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"裁完长 {written:.1f} 秒，应为 {seconds:.1f} 秒")
    os.replace(temp, media)


def _redeliver(media: Path, delivered: Path) -> None:
    """Point the user's copy at the shortened file (one file, two names)."""
    temp = delivered.with_name(f".{delivered.name}.tail")
    temp.unlink(missing_ok=True)
    try:
        os.link(media, temp)
    except OSError:
        shutil.copyfile(media, temp)
    os.replace(temp, delivered)


def _keep_until(segments: Any, seconds: float) -> Any:
    if not isinstance(segments, list):
        return segments
    return [seg for seg in segments if isinstance(seg, dict) and float(seg.get("start") or 0) < seconds]


def trim_tail(task_id: str, client_id: str | None = None, *, chat: tuple[Chat, str] | None = None) -> dict[str, Any] | None:
    """Judge where the content ends and cut the rest off. Returns what it
    recorded on the task, or None when there was nothing to judge. Never raises:
    the task's own results stand."""
    job = job_store.get_job(task_id, client_id=client_id)
    result = dict((job or {}).get("result") or {})
    cut = dict(result.get("debreath") or {})
    segments = result.get("raw_segments") if isinstance(result.get("raw_segments"), list) else []
    if not job or not cut.get("used_for_transcription") or not segments:
        return None
    media = _media_path(task_id, result)
    if media is None:
        return None
    try:
        duration = silence_cuts.media_duration_seconds(media)
    except Exception:  # noqa: BLE001 - unreadable media, nothing to judge
        return None
    if duration < MIN_DURATION_SECONDS:
        return None
    asker = chat or _default_chat()
    if asker is None:
        return None
    ask, name = asker
    record: dict[str, Any] = {"trimmed": False, "judged_by": name}
    try:
        ends_at, reason = _answer(_ask(ask, _transcript_tail(segments, duration)))
        record["reason"] = reason
        if ends_at is not None:
            cut_at = _cut_at(segments, ends_at)
            record["ends_at"] = round(cut_at, 1)
            if _speech_share_after(segments, cut_at) > MAX_SPEECH_SHARE_AFTER:
                record["skipped"] = "判断的结束点之后还有不少说话，不像是真的结束，没裁"
            elif duration - cut_at < MIN_TAIL_SECONDS:
                record["skipped"] = f"结束后只剩 {duration - cut_at:.0f} 秒，没裁"
            else:
                _shorten(media, cut_at)
                delivered = Path(str(cut.get("delivered_path") or ""))
                if cut.get("delivered_path") and delivered.is_file():
                    _redeliver(media, delivered)
                record.update({"trimmed": True, "removed_seconds": round(duration - cut_at, 1)})
                for key in ("raw_segments", "display_segments", "stt_raw_segments"):
                    result[key] = _keep_until(result.get(key), cut_at)
                result["transcript_text"] = "\n".join(
                    str(seg.get("text") or "").strip() for seg in result["raw_segments"] if str(seg.get("text") or "").strip()
                )
    except Exception as exc:  # noqa: BLE001 - the recording stays as it was
        logger.warning("tail trim for %s not done: %s", task_id, exc)
        record["error"] = str(exc)[:300]
    cut["tail_trim"] = record
    result["debreath"] = cut
    if record.get("trimmed"):
        from backend.core.result_artifacts import _attach_result_artifacts

        result = _attach_result_artifacts(task_id, result)
    job_store.update_job_result(task_id, result, client_id=client_id)
    return record


__all__ = ["trim_tail"]
