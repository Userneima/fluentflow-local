"""Transcription starts on the cut audio while the cut video renders.

Off in the pipeline for now (see ``local_intake_flow.preprocess_media``): side by
side with a software video encode it was slower, measured. These tests keep the
mechanism correct for when the encode moves to the hardware encoder.

What has to hold, written from the requirement rather than the code:

- Transcription gets the cut audio as soon as it exists, before the cut video.
  Waiting for a whole-file video encode first was the largest idle stretch in a
  task (about nine minutes for a 58-minute 2560x1440 recording).
- The cut audio is on the cut video's clock: a transcript made from it times the
  cut video correctly.
- Once the video is done, the task's file is the cut video, as before, and the
  interim audio is gone.
- A video that does not come out leaves the cut audio as the task's file (the
  transcript is on its clock), says so, and is not delivered beside the original.
- Cancelling the task stops the background render.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.core import debreath_job, local_intake_flow, silence_cuts
from backend.core.task_watch import TaskCancelled, TaskWatch


@pytest.fixture(autouse=True)
def _cut_before_transcription(monkeypatch):
    """These cover cutting before transcription, the flow used when
    ``FLUENTFLOW_SURVEY_FIRST`` is off."""
    monkeypatch.setenv("FLUENTFLOW_SURVEY_FIRST", "0")


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _media(target: Path, seconds: int = 12) -> Path:
    built = subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=red:s=320x240:r=30:d={seconds}",
            "-f", "lavfi", "-i",
            f"sine=frequency=440:duration={seconds}:sample_rate=48000,volume=enable='between(t,4,7)':volume=0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(target),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr[-400:]
    return target


@pytest.fixture()
def artifacts(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    monkeypatch.setattr(debreath_job, "artifact_target_path", lambda task_id, name: root / name)
    monkeypatch.setattr(debreath_job, "write_text_artifact", lambda *a, **k: {"kind": "cut_list"})
    monkeypatch.setattr(debreath_job, "describe_existing_artifact",
                        lambda task_id, kind, filename: {"kind": kind, "filename": filename})
    return root


def test_transcription_gets_the_cut_audio_and_the_task_ends_with_the_cut_video(tmp_path, artifacts):
    source = _media(tmp_path / "lecture.mp4")

    prepared = debreath_job.prepare_cut_media("t-af", source, watch=TaskWatch(), audio_first=True)

    assert prepared.video is not None, "the video renders behind transcription"
    assert prepared.path.suffix == ".m4a" and prepared.path.is_file()
    audio_seconds = silence_cuts.media_duration_seconds(prepared.path)
    plan_kept = prepared.state["plan"]["kept_seconds"]
    assert abs(audio_seconds - plan_kept) < 0.3

    final = prepared.video.result(timeout=120)

    assert final.used and final.path.suffix == ".mp4"
    assert final.state["render_verified"] is True
    assert abs(silence_cuts.media_duration_seconds(final.path) - audio_seconds) < 0.3, "one clock"
    assert not prepared.path.exists(), "the interim audio is removed once the video stands"


def test_a_video_that_does_not_come_out_leaves_the_cut_audio_as_the_file(tmp_path, artifacts, monkeypatch):
    source = _media(tmp_path / "lecture.mp4")

    def broken(*args, **kwargs):
        raise silence_cuts.SilenceCutError("encoder crashed")

    monkeypatch.setattr(silence_cuts, "render_cut_plan", broken)

    prepared = debreath_job.prepare_cut_media("t-af-fail", source, watch=TaskWatch(), audio_first=True)
    final = prepared.video.result(timeout=120)

    assert final.used is True and final.path == prepared.path and final.path.is_file()
    assert final.state["video_render_failed"] is True
    assert "笔记只根据转录写" in final.state["not_used_reason"]
    assert final.artifacts[debreath_job.MEDIA_KIND]["filename"].endswith(".m4a")


def test_cut_audio_alone_is_not_delivered_beside_the_recording(tmp_path, artifacts, monkeypatch):
    folder = tmp_path / "my-recordings"
    folder.mkdir()
    source = _media(folder / "lecture.mp4")
    monkeypatch.setattr(local_intake_flow, "cut_first_enabled", lambda: True)
    monkeypatch.setattr(local_intake_flow, "is_managed_path", lambda path: False)
    monkeypatch.setattr(silence_cuts, "render_cut_plan",
                        lambda *a, **k: (_ for _ in ()).throw(silence_cuts.SilenceCutError("no video")))

    prepared = local_intake_flow.preprocess_media("t-af-deliver", source, audio_first=True)
    final = prepared.video.result(timeout=120)

    assert final.state["video_render_failed"] is True
    assert not final.state.get("delivered")
    assert sorted(p.name for p in source.parent.iterdir()) == ["lecture.mp4"], "nothing new beside the original"


def test_cancelling_the_task_stops_the_background_render(tmp_path, artifacts, monkeypatch):
    source = _media(tmp_path / "lecture.mp4")
    watch = TaskWatch()
    original = silence_cuts.render_cut_plan

    def render_after_cancel(*args, **kwargs):
        watch.cancel()
        return original(*args, **kwargs)

    monkeypatch.setattr(silence_cuts, "render_cut_plan", render_after_cancel)

    prepared = debreath_job.prepare_cut_media("t-af-cancel", source, watch=watch, audio_first=True)

    with pytest.raises(TaskCancelled):
        prepared.video.result(timeout=120)


def test_the_cut_video_still_lands_beside_the_recording(tmp_path, artifacts, monkeypatch):
    folder = tmp_path / "my-recordings"
    folder.mkdir()
    source = _media(folder / "lecture.mp4")
    monkeypatch.setattr(local_intake_flow, "cut_first_enabled", lambda: True)
    monkeypatch.setattr(local_intake_flow, "is_managed_path", lambda path: False)

    prepared = local_intake_flow.preprocess_media("t-af-beside", source, audio_first=True)
    final = prepared.video.result(timeout=120)

    assert final.path.suffix == ".mp4"
    assert final.state.get("delivered") is True, final.state
    assert (folder / "lecture" / "lecture_去气口.mp4").is_file()
