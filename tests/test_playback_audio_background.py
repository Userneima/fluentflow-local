"""The playback MP3 encodes beside the transcription (2026-11-25).

What should hold:
- the transcription starts without waiting for the playback MP3, and the MP3
  is waited for before the result records it;
- the background encode writes the same kind of file the step wrote before,
  fails loudly when ffmpeg fails, and is stopped when the task ends without it;
- the digital-silence check reads the recognizer's WAV, not a third pass.
- (review, 2026-11-26) a damaged recording's messages do not hold the encode
  still; an encode that already failed fails the task before the
  transcription, one failing later costs the playback take and never the
  transcript; the encode runs at the user's CPU share; a prepared (cut) file's
  audio is not kept for carrying on.
"""
from __future__ import annotations

import inspect
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from backend.core import audio_handler, media_job
from backend.core.audio_handler import CompressedMp3InBackground

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _tone(path: Path, seconds: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-ac", "2", "-ar", "48000", str(path)],
        check=True,
    )
    return path


def test_the_transcription_starts_before_the_playback_mp3_is_waited_for():
    source = inspect.getsource(media_job._stream_media_job)
    started = source.index("pending_playback = CompressedMp3InBackground(")
    transcribing = source.index("start_transcription_process(")
    waited = source.index("pending_playback.path")
    recorded = source.index("_transcribed_checkpoint(")
    assert started < transcribing < waited < recorded
    assert "extract_compressed_mp3" not in source


def test_a_task_that_ends_early_stops_the_encode():
    source = inspect.getsource(media_job._stream_media_job)
    finally_block = source[source.rindex("    finally:"):]
    assert "pending_playback.stop()" in finally_block


@needs_ffmpeg
def test_the_background_encode_writes_an_mp3(tmp_path):
    source = _tone(tmp_path / "tone.wav", 3)
    pending = CompressedMp3InBackground(source, output_path=tmp_path / "out" / "playback.mp3")
    out = pending.path()
    assert out == (tmp_path / "out" / "playback.mp3").resolve()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "mp3"


@needs_ffmpeg
def test_a_failed_encode_raises_when_waited_for(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a recording")
    pending = CompressedMp3InBackground(broken, output_path=tmp_path / "playback.mp3")
    with pytest.raises(subprocess.CalledProcessError):
        pending.path()


@needs_ffmpeg
def test_stop_ends_a_running_encode(tmp_path):
    source = _tone(tmp_path / "long.wav", 1800)
    pending = CompressedMp3InBackground(source, output_path=tmp_path / "playback.mp3")
    started = time.monotonic()
    pending.stop()
    assert time.monotonic() - started < 5
    assert pending._process.returncode is not None
    pending.stop()  # stopping twice is harmless


@needs_ffmpeg
def test_the_silence_check_reads_a_video_without_its_picture(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def run(args, **kwargs):
        seen.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr(audio_handler.subprocess, "run", run)
    audio_handler.require_audible_audio(_tone(tmp_path / "tone.wav", 2))
    assert "-vn" in seen[-1]


@needs_ffmpeg
def test_stop_while_another_thread_waits_for_the_encode(tmp_path):
    """A task cancelled while it waits for the MP3 stops the encode from the
    task's cleanup while the wait still runs in a worker thread: the stop must
    not raise, and the waiting thread must come back."""
    source = _tone(tmp_path / "long.wav", 600)
    pending = CompressedMp3InBackground(source, output_path=tmp_path / "playback.mp3")
    outcome: list[BaseException | Path] = []

    def wait() -> None:
        try:
            outcome.append(pending.path())
        except BaseException as exc:  # noqa: BLE001 - recorded for the assert
            outcome.append(exc)

    waiter = threading.Thread(target=wait)
    waiter.start()
    time.sleep(0.3)
    pending.stop()
    waiter.join(timeout=10)
    assert not waiter.is_alive()
    assert pending._process.returncode is not None
    assert len(outcome) == 1 and isinstance(outcome[0], subprocess.CalledProcessError)


# ── review of 2026-11-26: a damaged recording, a failed encode, the CPU share ──

def _damaged_aac(path: Path, seconds: int) -> Path:
    """An AAC whose packets are partly overwritten: ffmpeg decodes it to the
    end and writes an error line for each bad packet (about 170 KB a minute,
    far past what a pipe holds)."""
    clean = path.with_name("clean.m4a")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-ac", "2", "-ar", "48000", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(clean)],
        check=True,
    )
    data = bytearray(clean.read_bytes())
    start = data.find(b"mdat") + 4096
    for offset in range(start, len(data) - 16, 1024):
        data[offset:offset + 16] = bytes((offset * 7 + n * 31) % 256 for n in range(16))
    path.write_bytes(bytes(data))
    return path


@needs_ffmpeg
def test_a_damaged_recording_does_not_hold_the_encode_still(tmp_path):
    source = _damaged_aac(tmp_path / "damaged.m4a", 60)
    pending = CompressedMp3InBackground(source, output_path=tmp_path / "job" / "playback.mp3")
    # Nobody waits for it while the transcription runs: it must finish anyway.
    deadline = time.monotonic() + 20
    while pending._process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pending._process.poll() == 0, "the encode ran to its end without anyone reading its messages"
    assert pending.path().stat().st_size > 100_000
    assert (tmp_path / "job" / "playback.mp3.log").stat().st_size > 65_536, "more than a pipe would have held"


@needs_ffmpeg
def test_a_failed_encode_says_why_and_is_seen_without_waiting(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a recording")
    pending = CompressedMp3InBackground(broken, output_path=tmp_path / "playback.mp3")
    deadline = time.monotonic() + 10
    while pending.failure() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    error = pending.failure()
    assert isinstance(error, subprocess.CalledProcessError)
    assert error.stderr.strip(), "ffmpeg's own words are in the error"


@needs_ffmpeg
def test_the_encode_runs_at_the_cpu_share_the_user_chose(tmp_path, monkeypatch):
    lowered: list[int] = []
    monkeypatch.setattr(audio_handler.device_profile, "lower_priority", lowered.append)
    pending = CompressedMp3InBackground(_tone(tmp_path / "tone.wav", 1), output_path=tmp_path / "playback.mp3")
    pending.path()
    assert lowered == [pending._process.pid]


class _FakeEncode:
    """The background MP3 encode, failing either before the transcription
    starts (``early``) or only when waited for after it."""

    def __init__(self, *, early: bool) -> None:
        self.early = early
        self.stopped = False

    def failure(self):
        return subprocess.CalledProcessError(1, ["ffmpeg"], stderr="Unknown encoder 'libmp3lame'") if self.early else None

    def path(self):
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr="No space left on device")

    def stop(self):
        self.stopped = True


def _pipeline_run(tmp_path, monkeypatch, *, encode=None, preprocessed: Path | None = None):
    """The real media pipeline on a short tone, with the recognizer faked."""
    import asyncio
    import types

    from backend.core import job_store, task_intelligence, task_submission
    from backend.core.local_stt import TranscriptionResult, TranscriptSegment

    transcribed: list[str] = []
    checkpoints: list[dict] = []
    task_id = f"playback-review-{time.time_ns()}"
    source = _tone(tmp_path / "talk.wav", 2)
    monkeypatch.setattr(media_job, "_artifact_storage_dir", lambda: tmp_path / "artifacts")
    if encode is not None:
        monkeypatch.setattr(media_job, "CompressedMp3InBackground", lambda *_a, **_k: encode)

    def start(audio, **_kw):
        transcribed.append(str(audio))
        return types.SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None, exitcode=0), None

    result = TranscriptionResult(text="大家好", segments=(TranscriptSegment(0.0, 2.0, "大家好"),),
                                 language="zh", language_probability=0.9, duration=2.0)
    monkeypatch.setattr(media_job, "start_transcription_process", start)
    monkeypatch.setattr(media_job, "drain_queue", lambda _q: [{"type": "result", "result": result}])

    async def run():
        ctx = task_submission._local_media_job_context(
            task_id=task_id, client_id="anonymous", source_type="audio", source_filename="talk.wav",
            raw_title="talk", display_title="talk", suffix=".wav", td=str(tmp_path / "scratch"),
            in_path=source, content=b"", source_file_size_mb=1, duration_preflight_sec=2,
            options=task_intelligence.agent_options(),
        )
        ctx.finalize_result_storage = lambda _task, result, _meta: result
        ctx.on_transcribed = checkpoints.append
        ctx.save_checkpoint = lambda _c: None
        if preprocessed is not None:
            ctx.media_preprocessor = lambda *_a, **_k: types.SimpleNamespace(path=preprocessed, state={}, artifacts={})
        await media_job.execute_media_job(ctx)

    (tmp_path / "scratch").mkdir()
    try:
        asyncio.run(run())
        return job_store.get_job(task_id), transcribed, checkpoints, tmp_path / "artifacts" / task_id
    finally:
        job_store.delete_jobs([task_id])


@needs_ffmpeg
def test_an_encoder_that_cannot_run_fails_the_task_before_the_transcription(tmp_path, monkeypatch):
    encode = _FakeEncode(early=True)
    job, transcribed, _checkpoints, _folder = _pipeline_run(tmp_path, monkeypatch, encode=encode)
    assert job["status"] == "failed"
    assert transcribed == [], "no transcription is spent on a task that fails anyway"


@needs_ffmpeg
def test_an_encode_failing_late_costs_the_playback_take_not_the_transcript(tmp_path, monkeypatch):
    job, transcribed, checkpoints, _folder = _pipeline_run(tmp_path, monkeypatch, encode=_FakeEncode(early=False))
    assert len(transcribed) == 1
    assert job["status"] == "completed", job.get("error_reason")
    assert job["result"]["transcript_text"] == "大家好"
    assert len(checkpoints) == 1, "the transcription was kept for carrying on"
    assert checkpoints[0]["playback_audio_path"] is None
    assert not job["result"].get("playback_audio_available")


@needs_ffmpeg
def test_no_audio_is_kept_for_carrying_on_when_a_prepared_file_was_transcribed(tmp_path, monkeypatch):
    cut = _tone(tmp_path / "cut.wav", 1)
    job, transcribed, _checkpoints, folder = _pipeline_run(tmp_path, monkeypatch, preprocessed=cut)
    assert job["status"] == "completed", job.get("error_reason")
    assert transcribed and not (folder / media_job.RESUME_DIRNAME / "stt.wav").exists()


@needs_ffmpeg
def test_audio_is_kept_for_carrying_on_when_the_file_as_given_was_transcribed(tmp_path, monkeypatch):
    job, _transcribed, _checkpoints, folder = _pipeline_run(tmp_path, monkeypatch)
    assert job["status"] == "completed", job.get("error_reason")
    assert job["result"].get("playback_audio_available") is True
    assert (folder / media_job.RESUME_DIRNAME / "stt.wav").is_file()
