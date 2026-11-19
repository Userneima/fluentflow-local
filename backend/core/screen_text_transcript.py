"""A video in which no one speaks: read its on-screen text as the transcript.

Requirements (owner, 2026-11-19, superseding the blanket failure of decision
4.19 for videos; evaluation in
`docs/方案/2026-11-19-没有人声的视频只看画面写笔记.md`):

- The two recordings that failed for "no speech" were Douyin clips with loud
  music whose content is all on-screen captions; an agent had to sample frames
  by hand to write their notes. Such a video now gets a note.
- The recording that 4.19 was made for (a screen recording that took only a
  muted microphone) must still fail: told apart by how loud the sound is.
  Measured: the two clips -16.3 and -19.6 dB on average, the muted microphone
  -44.1 dB. The line sits between them, from three recordings only.
- A video with sound but no speech: its on-screen text, read on this machine
  every couple of seconds, becomes a transcript marked as such, and the task
  runs as if submitted with cut=false (nothing is cut by speech; the note reads
  the recording, pictures included). Everything after transcription works on
  it unchanged, for the user's agent and FluentFlow's own note writer alike.
- FluentFlow writing that note itself costs money, so a long one (over ten
  minutes) is not done unasked; neither is a near-silent one. Those fail with a
  reason and the page offers 「照画面写笔记」, which retries with
  ``picture_note`` and skips both checks. The user's agent writing the note
  costs nothing, so only the near-silent check applies to it.
- No text on screen (or no text recognition on this machine), or an audio
  file: there is nothing to write from, and the task fails as before.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.core import frame_text
from backend.core.local_error_diagnostics import (
    NO_SPEECH,
    NO_SPEECH_LONG,
    NO_SPEECH_NO_TEXT,
    NO_SPEECH_QUIET,
)

logger = logging.getLogger(__name__)

# What a task records in its result when its transcript is on-screen text.
SCREEN_TEXT = "screen_text"

# Average loudness at or below this is "almost no sound": a muted microphone,
# a recorder that took no system sound (see the module docstring).
QUIET_MEAN_DB = -35.0

# Longer than this, FluentFlow does not write a picture note unasked.
AUTO_LIMIT_SECONDS = 600.0

# The first line of such a transcript, so whoever reads it (the user on the
# page, the agent or the model writing the note, the subtitle files) knows what
# it is: every note path reads the transcript, so no path has to be told apart.
HEADER = "（视频里没人说话。这份转录稿是每隔几秒读出的画面文字，可能有错字。）"

# One look every two seconds, as the agent that worked around this did by hand,
# but no more than this many looks however long the video.
SAMPLE_SECONDS = 2.0
MAX_SAMPLES = 300


def has_speech(transcript_text: Any, segments: Any) -> bool:
    return bool(str(transcript_text or "").strip() or segments)


def picture_note_wanted(job: dict[str, Any] | None) -> bool:
    """Whether the user asked for a note from the picture (the
    「照画面写笔记」 retry): recorded beside ``cut`` (link metadata, or the
    in-place origin) and carried by a retry the same way."""
    metadata = (job or {}).get("metadata") if isinstance((job or {}).get("metadata"), dict) else {}
    origin = metadata.get("folder_intake") if isinstance(metadata.get("folder_intake"), dict) else {}
    return metadata.get("picture_note") is True or origin.get("picture_note") is True


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg is not installed")
    return found


def has_video(media: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(media)],
        capture_output=True, text=True, check=False,
    )
    return "video" in completed.stdout


def mean_volume_db(media: Path) -> float | None:
    """The average loudness of the recording's sound; None when it has none
    or it cannot be measured."""
    completed = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(media), "-vn", "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    match = re.search(r"mean_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB", completed.stderr)
    return float(match.group(1)) if match else None


def refusal(*, mean_db: float | None, duration_seconds: float | None,
            agent_writes: bool, asked: bool) -> str | None:
    """Why this video gets no picture note unasked, or None when it does."""
    if asked:
        return None
    if mean_db is None or mean_db <= QUIET_MEAN_DB:
        return NO_SPEECH_QUIET
    if not agent_writes and (duration_seconds or 0) > AUTO_LIMIT_SECONDS:
        return NO_SPEECH_LONG
    return None


def read_screen(video: Path, duration_seconds: float | None) -> list[dict[str, Any]]:
    """The video's on-screen text as transcript segments: one look every
    ``SAMPLE_SECONDS`` (fewer on a long video), each look keeping only the
    lines not already seen, timed from that look to the next."""
    step = SAMPLE_SECONDS
    if duration_seconds and duration_seconds / step > MAX_SAMPLES:
        step = duration_seconds / MAX_SAMPLES
    with tempfile.TemporaryDirectory(prefix="screen-text-") as tmp:
        folder = Path(tmp)
        subprocess.run(
            [_ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(video),
             "-vf", f"fps=1/{step:.3f}", "-q:v", "3", str(folder / "f_%05d.jpg")],
            check=True, capture_output=True,
        )
        seen = frame_text.SeenLines()
        looks: list[tuple[float, list[str]]] = []
        for index, frame in enumerate(sorted(folder.glob("f_*.jpg"))):
            lines = frame_text.new_lines(frame_text.read_frame(frame), seen)
            looks.append((round(index * step, 2), lines))
    end_of_video = float(duration_seconds or (looks[-1][0] + step if looks else 0.0))
    segments: list[dict[str, Any]] = []
    for position, (start, lines) in enumerate(looks):
        text = " ".join(line.strip() for line in lines if line.strip())
        if not text:
            continue
        end = looks[position + 1][0] if position + 1 < len(looks) else max(end_of_video, start + step)
        segments.append({"start": start, "end": round(end, 2), "text": text})
    return segments


def transcript_or_refuse(media: Path, *, duration_seconds: float | None,
                         agent_writes: bool, asked: bool) -> list[dict[str, Any]]:
    """The on-screen text of a video no one speaks in, as segments; raises
    with the reason (each starting with ``NO_SPEECH``) when it gets none."""
    if not has_video(media) or not frame_text.available():
        raise RuntimeError(NO_SPEECH)
    reason = refusal(mean_db=mean_volume_db(media), duration_seconds=duration_seconds,
                     agent_writes=agent_writes, asked=asked)
    if reason:
        raise RuntimeError(reason)
    segments = read_screen(media, duration_seconds)
    if not segments:
        raise RuntimeError(NO_SPEECH_NO_TEXT)
    logger.info("no speech in %s: %d stretches of on-screen text read instead", media.name, len(segments))
    return [{"start": 0.0, "end": 0.0, "text": HEADER}, *segments]


def joined(segments: list[dict[str, Any]]) -> str:
    return "\n".join(str(segment.get("text") or "") for segment in segments)
