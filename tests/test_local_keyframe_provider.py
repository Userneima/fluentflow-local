from backend.core import local_keyframe_provider


def test_local_keyframe_provider_defaults_to_ffmpeg(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_KEYFRAME_EXTRACTION", raising=False)
    monkeypatch.delenv("FLUENTFLOW_KEYFRAME_PROVIDER", raising=False)

    assert local_keyframe_provider.configured_keyframe_provider() == "local_ffmpeg"


def test_local_keyframe_provider_disables_unknown_provider(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_KEYFRAME_PROVIDER", "remote-worker")

    assert local_keyframe_provider.configured_keyframe_provider() == "disabled"


def test_local_keyframe_provider_tags_extracted_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(
        local_keyframe_provider,
        "extract_candidate_frames",
        lambda *args, **kwargs: [
            {
                "path": str(tmp_path / "frame.jpg"),
                "timestamp_seconds": 1.2,
                "source": "timepoint",
            }
        ],
    )

    result = local_keyframe_provider.extract_keyframes(
        "demo.mp4", tmp_path, provider="local_ffmpeg"
    )

    assert result.provider == "local_ffmpeg"
    assert result.frames[0]["provider"] == "local_ffmpeg"
