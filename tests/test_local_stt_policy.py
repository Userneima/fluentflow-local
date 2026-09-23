from backend.core.local_stt_policy import (
    allowed_stt_providers,
    default_stt_provider,
    normalize_stt_provider,
    stt_provider_label,
)


def test_local_edition_exposes_only_local_stt():
    assert allowed_stt_providers() == ("local",)
    assert default_stt_provider() == "local"


def test_local_edition_normalizes_every_provider_request_to_local_stt():
    assert normalize_stt_provider(None) == "local"
    assert normalize_stt_provider("local") == "local"
    assert normalize_stt_provider("hosted-provider") == "local"


def test_local_edition_labels_its_stt_engine():
    assert stt_provider_label("local") == "faster-whisper"


def test_an_old_cloud_engine_request_is_still_run_locally(tmp_path):
    """Old clients and stored jobs may still name a cloud engine or send its key.

    Both are accepted: the engine name normalizes to local transcription and the
    key never reaches the pipeline.
    """
    import asyncio

    from backend.core.queue_options import _queue_options_from_mapping
    from backend.routers.local_processing import _local_media_job_context

    for legacy in ("elevenlabs", "dashscope"):
        assert normalize_stt_provider(legacy) == "local"

    options = _queue_options_from_mapping(
        {"stt_provider": "elevenlabs", "elevenlabs_api_key": "old-key", "stt_speed": "fast"}
    )
    assert "elevenlabs_api_key" not in options

    async def build():
        return _local_media_job_context(
            task_id="t-legacy",
            client_id=None,
            source_type="upload",
            source_filename="old.mp4",
            raw_title="old",
            display_title="old",
            suffix=".mp4",
            td=str(tmp_path),
            in_path=tmp_path / "old.mp4",
            content=b"",
            source_file_size_mb=None,
            duration_preflight_sec=None,
            options=options,
        )

    ctx = asyncio.run(build())
    assert ctx.stt_provider_value == "local"
    assert ctx.speed_profile == "fast"
    assert not hasattr(ctx, "remote_stt_policy")
