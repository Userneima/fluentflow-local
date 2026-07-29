"""STT provider policy for the local edition."""

from __future__ import annotations


LOCAL_STT_PROVIDER = "local"


def allowed_stt_providers() -> tuple[str, ...]:
    return (LOCAL_STT_PROVIDER,)


def default_stt_provider() -> str:
    return LOCAL_STT_PROVIDER


def normalize_stt_provider(_value: str | None) -> str:
    return LOCAL_STT_PROVIDER


def stt_provider_label(_provider: str) -> str:
    return "faster-whisper"
