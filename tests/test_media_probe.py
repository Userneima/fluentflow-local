import wave

from backend.core import media_probe


def _write_wav(path, *, frames: int, frame_rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(frame_rate)
        wav.writeframes(b"\x00\x00" * frames)


def test_reads_duration_from_the_wav_header(tmp_path):
    path = tmp_path / "clip.wav"
    _write_wav(path, frames=16000, frame_rate=8000)
    assert media_probe.media_duration_seconds(path) == 2.0


def test_falls_back_to_ffprobe_when_the_file_is_not_a_wav(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not a wav")
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: "/usr/bin/ffprobe")

    class _Result:
        stdout = "12.5\n"

    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **k: _Result())
    assert media_probe.media_duration_seconds(path) == 12.5


def test_ffprobe_call_is_bounded_by_a_timeout(tmp_path, monkeypatch):
    """A stalled probe must not hang the pipeline stage that asked for it."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not a wav")
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    seen = {}

    class _Result:
        stdout = "3.0"

    def _run(*args, **kwargs):
        seen.update(kwargs)
        return _Result()

    monkeypatch.setattr(media_probe.subprocess, "run", _run)
    media_probe.media_duration_seconds(path)
    assert seen.get("timeout") == media_probe.FFPROBE_TIMEOUT_SECONDS


def test_returns_none_when_ffprobe_is_missing(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not a wav")
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: None)
    assert media_probe.media_duration_seconds(path) is None


def test_returns_none_when_ffprobe_prints_something_unparseable(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not a wav")
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: "/usr/bin/ffprobe")

    class _Result:
        stdout = "N/A"

    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **k: _Result())
    assert media_probe.media_duration_seconds(path) is None


def test_the_pipeline_probes_through_the_shared_function():
    """media_job used to carry its own copy of this probe."""
    from backend.core import media_job

    assert media_job._media_duration_seconds is media_probe.media_duration_seconds
