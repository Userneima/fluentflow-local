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
