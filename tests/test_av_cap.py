"""av 19 removed an argument faster-whisper 1.2.1 passes; every transcription then fails."""

from pathlib import Path

from backend.core import local_readiness


def test_requirements_keep_av_below_19():
    lines = (Path(__file__).resolve().parents[1] / "requirements-local.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.strip().startswith("av") and "<19" in line for line in lines)


def _versions(monkeypatch, av, fw):
    from importlib import metadata

    monkeypatch.setattr(metadata, "version", lambda name: {"av": av, "faster-whisper": fw}[name])


def test_readiness_names_the_fix_when_av_19_meets_faster_whisper_1_2_1(monkeypatch):
    _versions(monkeypatch, "19.0.1", "1.2.1")
    reason = local_readiness._av_conflict()
    assert reason and 'av<19' in reason


def test_readiness_is_quiet_on_a_working_pair(monkeypatch):
    _versions(monkeypatch, "18.1.0", "1.2.1")
    assert local_readiness._av_conflict() is None
