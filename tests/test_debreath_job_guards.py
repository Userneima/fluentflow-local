"""What the de-breath entry points promise the user, checked from the outside.

Written from the requirements, not from the code: the manual route must measure
the material the way the pipeline does; a timeout must cost nothing but the
cut; and the file that comes out must be in a container the encoder actually
wrote.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import backend.core.debreath_job as dj
from backend.core import silence_cuts as sc
from backend.core.job_store import upsert_job
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.storage_paths import _source_storage_dir

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe not installed",
)


def _build_lifted_gap_clip(target: Path) -> None:
    """Loud, normalized material whose gaps sit above the default threshold.

    The tone is lifted to about -4dB and the two-second gap is left at -24dB,
    the shape a noise-reduced or volume-boosted recording has. At -30dB
    silencedetect finds nothing in it.
    """
    built = subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            "sine=frequency=440:duration=8:sample_rate=48000,volume=7,"
            "volume=enable='between(t,3,5)':volume=0.1",
            "-y", str(target),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr[-400:]


@pytest.fixture()
def lifted_task():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    task_id = "task-debreath-lifted-gaps"
    source_dir = _source_storage_dir() / task_id
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "source.m4a"
    _build_lifted_gap_clip(source)
    upsert_job(
        task_id=task_id,
        status="completed",
        client_id=LOCAL_OWNER_ID,
        result={"task_id": task_id, "filename": "lecture.m4a", "display_segments": []},
    )
    dj.release(task_id)
    yield task_id, source
    dj.release(task_id)


@needs_ffmpeg
def test_the_manual_route_measures_the_material_like_the_pipeline_does(lifted_task):
    """A recording the pipeline cut at an adapted threshold was re-cut by hand at
    -30dB and came back with 0% removed, because the manual route handed the
    default straight to the planner. Both routes have to answer the same way."""
    task_id, source = lifted_task
    untouched = sc.plan_silence_cuts(source, noise_db=sc.DEFAULT_NOISE_DB, min_silence_seconds=0.5)
    assert not untouched.cuts, "the fixture is only meaningful if the default finds nothing"

    state = dj.debreath_state(dj.run_debreath(task_id, min_silence_seconds=0.5, render=False)["result"])

    assert state["status"] == dj.STATUS_COMPLETED
    assert state["threshold_choice"]["adapted"] is True
    assert state["settings"]["noise_db"] == state["threshold_choice"]["noise_db"] != sc.DEFAULT_NOISE_DB
    assert state["plan"]["cut_count"] >= 1 and state["plan"]["removed_seconds"] > 1.0


def test_a_threshold_the_user_chose_is_used_as_given(tmp_path, monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("an explicit threshold must not be measured over")

    monkeypatch.setattr(sc, "suggest_noise_db", _refuse)
    assert dj.adapt_threshold(tmp_path / "clip.m4a", -25.0) == (-25.0, {})


def test_the_default_means_measure_and_a_failed_measurement_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "suggest_noise_db", lambda *_a, **_k: (-9.2, {"adapted": True, "noise_db": -9.2}))
    assert dj.adapt_threshold(tmp_path / "clip.m4a", sc.DEFAULT_NOISE_DB) == (-9.2, {"adapted": True, "noise_db": -9.2})

    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(["ffmpeg"], 1)

    monkeypatch.setattr(sc, "suggest_noise_db", _timeout)
    assert dj.adapt_threshold(tmp_path / "clip.m4a", sc.DEFAULT_NOISE_DB) == (sc.DEFAULT_NOISE_DB, {})


class _TimesOutOnDetection:
    """ffmpeg that answers probes but overruns its limit on the detection pass."""

    def __call__(self, command):
        command = [str(part) for part in command]
        if "silencedetect" in " ".join(command):
            raise subprocess.TimeoutExpired(command, 14400)
        if "ffprobe" in command[0]:
            if "stream=codec_type" in " ".join(command):
                return subprocess.CompletedProcess(command, 0, "audio\n", "")
            return subprocess.CompletedProcess(command, 0, "100.0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_a_timed_out_cut_costs_the_recording_nothing(tmp_path, monkeypatch):
    """prepare_cut_media promises never to fail the job. ``run_command`` raises
    TimeoutExpired when ffmpeg overruns, which is not an OSError; uncaught, it
    took the whole transcription down with the cut."""
    monkeypatch.setattr(sc, "_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(sc, "_ffprobe_path", lambda: "/fake/ffprobe")
    source = tmp_path / "lecture.m4a"
    source.write_bytes(b"x")

    prepared = dj.prepare_cut_media("task-timeout", source, noise_db=-25.0, runner=_TimesOutOnDetection())

    assert prepared.used is False and prepared.path == source
    assert prepared.state["status"] == dj.STATUS_FAILED
    assert "去气口没成功" in prepared.state["not_used_reason"]


@pytest.mark.parametrize(
    ("source_name", "expected"),
    [
        ("talk.mp4", "debreath/talk_debreath.mp4"),
        ("talk.mkv", "debreath/talk_debreath.mp4"),
        ("talk.webm", "debreath/talk_debreath.mp4"),
        ("talk.mov", "debreath/talk_debreath.mp4"),
        ("talk.flac", "debreath/talk_debreath.m4a"),
        ("talk.ogg", "debreath/talk_debreath.m4a"),
        ("talk.opus", "debreath/talk_debreath.m4a"),
        ("talk.mp3", "debreath/talk_debreath.m4a"),
        ("talk.m4a", "debreath/talk_debreath.m4a"),
    ],
)
def test_the_cut_file_is_named_for_what_the_encoder_writes(source_name, expected):
    """Every render is H.264 + AAC, or AAC alone. Keeping the source's extension
    put AAC into .webm/.ogg/.opus/.flac files, which failed at the first batch
    after a full normalize pass, and wrote .mkv files that could never pass
    the frame-count self-check."""
    assert dj._media_filename(Path(source_name)) == expected
