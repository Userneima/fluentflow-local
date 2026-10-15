"""What was recorded after the content ended is cut off before the note.

Requirements (owner, 2026-10-15):
1. When the model names where the content ends and more than half a minute of
   leftover follows, the cut video ends one second after that last line, in
   FluentFlow's store and in the user's folder, and the transcript stops there.
2. When the model says the content runs to the end, or is unsure, nothing
   changes.
3. An answer that would cut away more than a quarter of what was said, or
   leave only a few seconds, changes nothing. Where the content ends is not
   limited by time: speech that stops early is cut early.
4. A model that fails or never answers changes nothing; the task goes on.
5. A recording that was not cut is left alone (the original is never touched).
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.core import job_store, silence_cuts, tail_trim
from backend.core.storage_paths import _artifact_storage_dir

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _video(target: Path, seconds: int) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=gray:s=160x90:d={seconds}:r=10",
         "-f", "lavfi", "-i", f"sine=f=300:d={seconds}",
         "-shortest", "-c:v", "libx264", "-g", "10", "-c:a", "aac", str(target)],
        check=True,
    )
    return target


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.setattr(tail_trim, "MIN_DURATION_SECONDS", 10.0)
    monkeypatch.setattr(tail_trim, "MIN_TAIL_SECONDS", 10.0)
    task_id = f"tt-{uuid.uuid4().hex[:8]}"
    media = _video(_artifact_storage_dir() / task_id / "debreath" / "talk_debreath.mp4", 60)
    delivered = tmp_path / "talk" / "talk_去气口.mp4"
    delivered.parent.mkdir()
    shutil.copyfile(media, delivered)
    segments = [{"start": float(t), "end": float(t) + 4.0, "text": f"第{t}秒"} for t in range(0, 60, 5)]
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "raw_segments": segments,
        "display_segments": segments,
        "transcript_text": "\n".join(s["text"] for s in segments),
        "artifacts": {"debreath_media": {"kind": "debreath_media", "filename": "debreath/talk_debreath.mp4"}},
        "debreath": {"used_for_transcription": True, "delivered_path": str(delivered)},
    })
    return {"id": task_id, "media": media, "delivered": delivered}


def _says(text):
    return (lambda system, user: text), "fake"


@needs_ffmpeg
def test_the_leftover_after_the_content_is_cut_off(task):
    # The meeting talks until 35 s; after it only a couple of stray words.
    result = job_store.get_job(task["id"])["result"]
    result["raw_segments"] = [
        {"start": float(t), "end": float(t) + 4.0, "text": f"第{t}秒讲的正事内容" if t <= 35 else "嗯"}
        for t in range(0, 60, 5)
    ]
    result["transcript_text"] = "\n".join(seg["text"] for seg in result["raw_segments"])
    job_store.update_job_result(task["id"], result)

    record = tail_trim.trim_tail(task["id"], chat=_says('{"ends_at": 35, "reason": "35 秒说完再见"}'))

    assert record["trimmed"] is True
    # The line starting at 35 s ends at 39 s; one second more is kept.
    assert abs(silence_cuts.media_duration_seconds(task["media"]) - 40.0) < 1.0
    assert abs(silence_cuts.media_duration_seconds(task["delivered"]) - 40.0) < 1.0
    result = job_store.get_job(task["id"])["result"]
    assert all(seg["start"] < 40.0 for seg in result["raw_segments"])
    assert "嗯" not in result["transcript_text"] and "第35秒" in result["transcript_text"]
    assert result["debreath"]["tail_trim"]["removed_seconds"] == pytest.approx(20.0, abs=1.0)


@needs_ffmpeg
@pytest.mark.parametrize("answer", [
    '{"ends_at": null, "reason": "一直在讲"}',
    '{"ends_at": 12, "reason": "后面还有很多话"}',
    '{"ends_at": 52, "reason": "只剩几秒"}',
    "没有 JSON",
])
def test_nothing_changes_when_the_answer_does_not_justify_a_cut(task, answer):
    tail_trim.trim_tail(task["id"], chat=_says(answer))

    assert abs(silence_cuts.media_duration_seconds(task["media"]) - 60.0) < 1.0
    result = job_store.get_job(task["id"])["result"]
    assert len(result["raw_segments"]) == 12
    assert result["debreath"]["tail_trim"]["trimmed"] is False


@needs_ffmpeg
def test_a_model_that_never_answers_changes_nothing(task, monkeypatch):
    import threading

    monkeypatch.setattr(tail_trim, "REQUEST_DEADLINE_SECONDS", 0.2)
    never = threading.Event()

    record = tail_trim.trim_tail(task["id"], chat=((lambda s, u: never.wait(30) or "{}"), "fake"))
    never.set()

    assert record["trimmed"] is False and "error" in record
    assert abs(silence_cuts.media_duration_seconds(task["media"]) - 60.0) < 1.0


def test_a_recording_that_was_not_cut_is_left_alone():
    task_id = f"tt-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "raw_segments": [{"start": 0.0, "end": 1.0, "text": "你好"}],
        "debreath": {"used_for_transcription": False},
    })

    assert tail_trim.trim_tail(task_id, chat=_says('{"ends_at": 0.5}')) is None


@needs_ffmpeg
def test_speech_that_stops_early_is_cut_early(task):
    """The operations meeting: all the talk in the first third, then minutes of
    nothing. The model is told how long the silence after the last line is."""
    seen = {}
    job = job_store.get_job(task["id"])
    result = job["result"]
    result["raw_segments"] = [{"start": float(t), "end": float(t) + 4.0, "text": f"第{t}秒"} for t in range(0, 20, 5)]
    job_store.update_job_result(task["id"], result)

    def model(system, user):
        seen["user"] = user
        return '{"ends_at": 15, "reason": "15 秒后就没人说话了"}'

    record = tail_trim.trim_tail(task["id"], chat=(model, "fake"))

    assert "之后的 41 秒没有任何转录出来的说话" in seen["user"]
    assert record["trimmed"] is True
    assert abs(silence_cuts.media_duration_seconds(task["media"]) - 20.0) < 1.0
