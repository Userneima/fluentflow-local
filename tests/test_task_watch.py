"""Removing the breath gaps reports how far it got, and stops when the task does.

What has to hold, written from the requirement rather than the code:

- Cancelling a task stops the ffmpeg its cut is running, at once, and no later
  command of that cut starts. Measured before this (2026-10-13): a cancelled
  58-minute task kept re-encoding the recording, and every later task waited.
- A cancelled cut is not a failed cut: it must not fall back to transcribing
  the original, which is what a failed cut does.
- While the cut renders, the task's percentage moves and its progress line says
  what is being done, instead of one number from start to end.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.core import debreath_job, media_job, silence_cuts
from backend.core.task_watch import TaskCancelled, TaskWatch

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


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


# ── cancelling ──────────────────────────────────────────────────────────────

def test_cancelling_stops_the_command_that_is_running():
    watch = TaskWatch()
    outcome: dict[str, object] = {}

    def run() -> None:
        started = time.monotonic()
        try:
            watch.runner([sys.executable, "-c", "import time; time.sleep(30)"])
        except TaskCancelled:
            outcome["cancelled_after"] = time.monotonic() - started

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.5)
    watch.cancel()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert outcome["cancelled_after"] < 5, "stopped at once, not when the command would have ended"


def test_no_command_starts_once_the_task_is_cancelled(tmp_path):
    watch = TaskWatch()
    watch.cancel()
    marker = tmp_path / "ran"

    with pytest.raises(TaskCancelled):
        watch.runner(["touch", str(marker)])

    assert not marker.exists()


@needs_ffmpeg
def test_a_cancelled_cut_is_not_turned_into_transcribing_the_original(tmp_path, monkeypatch):
    monkeypatch.setattr(debreath_job, "artifact_target_path", lambda task_id, name: tmp_path / "out" / name)
    monkeypatch.setattr(debreath_job, "write_text_artifact", lambda *a, **k: {})
    source = _media(tmp_path / "lecture.mp4")

    def cancel_once_rendering(label: str, fraction: float | None) -> None:
        if label == debreath_job.PHASE_RENDER:
            watch.cancel()

    watch = TaskWatch(on_progress=cancel_once_rendering)

    with pytest.raises(TaskCancelled):
        debreath_job.prepare_cut_media("t-cancel", source, watch=watch)


# ── progress ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_an_encode_reports_how_much_it_has_written(tmp_path):
    source = _media(tmp_path / "in.mp4", seconds=6)
    seen: list[tuple[str, float | None]] = []
    watch = TaskWatch(on_progress=lambda label, fraction: seen.append((label, fraction)))

    with watch.phase("导出", expected_seconds=6):
        done = watch.runner([
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-y", str(tmp_path / "out.mp4"),
        ])

    assert done.returncode == 0
    fractions = [fraction for label, fraction in seen if label == "导出" and fraction]
    assert fractions and fractions[-1] > 0.8
    assert fractions == sorted(fractions), "never moves backwards"


@needs_ffmpeg
def test_a_command_that_prints_its_answer_still_gets_it_back(tmp_path):
    source = _media(tmp_path / "in.mp4", seconds=3)
    watch = TaskWatch()

    with watch.phase("导出", expected_seconds=3):
        duration = silence_cuts.media_duration_seconds(source, runner=watch.runner)

    assert 2.5 < duration < 3.5


@needs_ffmpeg
def test_the_cut_names_each_step_and_its_render_reaches_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(debreath_job, "artifact_target_path", lambda task_id, name: tmp_path / "out" / name)
    monkeypatch.setattr(debreath_job, "write_text_artifact", lambda *a, **k: {})
    monkeypatch.setattr(debreath_job, "describe_existing_artifact", lambda *a, **k: {})
    source = _media(tmp_path / "lecture.mp4")
    seen: list[tuple[str, float | None]] = []

    prepared = debreath_job.prepare_cut_media("t-progress", source, watch=TaskWatch(
        on_progress=lambda label, fraction: seen.append((label, fraction)),
    ))

    assert prepared.used, prepared.state.get("not_used_reason")
    labels = [label for label, _ in seen]
    assert debreath_job.PHASE_ANALYSE in labels and debreath_job.PHASE_RENDER in labels
    render = [fraction for label, fraction in seen if label == debreath_job.PHASE_RENDER and fraction is not None]
    assert render[-1] > 0.9, "the render's share of the media is accounted for"


def test_the_task_shows_the_step_and_a_moving_percentage(monkeypatch):
    written: list[dict] = []
    monkeypatch.setattr(media_job, "upsert_job", lambda **fields: written.append(fields))
    write = media_job._prepare_progress_writer("t1")

    write(debreath_job.PHASE_ANALYSE, None)
    write(debreath_job.PHASE_RENDER, 0.5)
    write(debreath_job.PHASE_RENDER, 1.0)

    assert [row["stage"] for row in written] == ["prepare_media"] * 3
    assert written[0]["metadata"]["progress_message"] == debreath_job.PHASE_ANALYSE
    assert written[1]["metadata"]["progress_message"].endswith("50%")
    progress = [row["progress"] for row in written]
    assert progress == sorted(progress) and progress[-1] == media_job.PREPARE_PROGRESS_END


def test_a_query_inside_a_measured_phase_keeps_its_answer(monkeypatch):
    """Requirement (2026-10-14): during a task, a command that answers on its
    own output (``ffmpeg -encoders``) returns that answer intact, so a Mac with
    a hardware encoder is not taken for one without."""
    import subprocess as sp

    from backend.core.task_watch import TaskWatch

    seen = {}

    class FakePopen:
        def __init__(self, argv, **_kwargs):
            seen["argv"] = argv
            self.pid = 0
            self.returncode = 0
            self.stdout = iter([" V....D hevc_videotoolbox    VideoToolbox H.265 Encoder\n"])
            self.stderr = type("E", (), {"read": lambda _self: ""})()

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(sp, "Popen", FakePopen)
    watch = TaskWatch()
    with watch.phase("压缩", 100):
        done = watch.runner(["/opt/homebrew/bin/ffmpeg", "-hide_banner", "-encoders"])
    assert "-progress" not in seen["argv"]
    assert "hevc_videotoolbox" in done.stdout
