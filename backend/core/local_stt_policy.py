"""STT provider policy for the local edition."""

from __future__ import annotations


LOCAL_STT_PROVIDER = "local"

# The one local model size the product offers. It lives here rather than in
# `local_stt` because the submit route needs it too, and importing `local_stt`
# for a string would pull faster-whisper into every request path. Callers that
# hand a size to the engine still go through `local_stt._model_for_device`,
# which downgrades it to `medium` on a machine that cannot run it.
DEFAULT_LOCAL_STT_MODEL = "large-v3"


def allowed_stt_providers() -> tuple[str, ...]:
    return (LOCAL_STT_PROVIDER,)


def default_stt_provider() -> str:
    return LOCAL_STT_PROVIDER


def normalize_stt_provider(_value: str | None) -> str:
    return LOCAL_STT_PROVIDER


def stt_provider_label(_provider: str) -> str:
    return "faster-whisper"
