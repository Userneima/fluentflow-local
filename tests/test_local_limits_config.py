from backend.core.local_limits_config import (
    max_media_duration_seconds,
    max_queue_files,
    max_transcript_upload_mb,
    max_upload_mb,
)


def test_local_limits_use_safe_defaults(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("FLUENTFLOW_MAX_QUEUE_FILES", raising=False)
    monkeypatch.delenv("FLUENTFLOW_MAX_MEDIA_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("FLUENTFLOW_MAX_TRANSCRIPT_UPLOAD_MB", raising=False)

    assert max_upload_mb() == 2048.0
    assert max_queue_files() == 5
    assert max_media_duration_seconds() == 14400.0
    assert max_transcript_upload_mb() == 64.0


def test_local_limits_accept_environment_overrides(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_MAX_UPLOAD_MB", "4096")
    monkeypatch.setenv("FLUENTFLOW_MAX_QUEUE_FILES", "8")
    monkeypatch.setenv("FLUENTFLOW_MAX_MEDIA_DURATION_SECONDS", "21600")
    monkeypatch.setenv("FLUENTFLOW_MAX_TRANSCRIPT_UPLOAD_MB", "32")

    assert max_upload_mb() == 4096.0
    assert max_queue_files() == 8
    assert max_media_duration_seconds() == 21600.0
    assert max_transcript_upload_mb() == 32.0


def test_local_limits_recover_from_invalid_or_unsafe_values(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_MAX_UPLOAD_MB", "invalid")
    monkeypatch.setenv("FLUENTFLOW_MAX_QUEUE_FILES", "0")
    monkeypatch.setenv("FLUENTFLOW_MAX_MEDIA_DURATION_SECONDS", "-1")
    monkeypatch.setenv("FLUENTFLOW_MAX_TRANSCRIPT_UPLOAD_MB", "invalid")

    assert max_upload_mb() == 2048.0
    assert max_queue_files() == 1
    assert max_media_duration_seconds() == 0.0
    assert max_transcript_upload_mb() == 64.0
