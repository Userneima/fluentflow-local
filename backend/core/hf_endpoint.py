"""Which Hugging Face endpoint this machine can actually download models from.

The models are several gigabytes and they all come from Hugging Face. That is
fine on some networks and unusable on others, and the unusable case does not
look like an error: on 2026-09-03 this project measured the Hugging Face API
answering in about a second while the file CDN returned 206 and then zero
bytes, with no timeout of its own, so a download sat there for over an hour
saying nothing. `scripts/fetch_diarization_models.py` already carries a mirror
for the speaker models because of that day; this is the same answer for the
transcription models.

So the probe fetches a real file rather than asking the API whether the
repository exists. An API that answers while the CDN does not is precisely the
failure being guarded against.

The decision has to be made before `huggingface_hub` is imported: that library
reads the endpoint into a module constant at import time, so setting the
environment variable afterwards changes nothing. Everything here uses the
standard library only, so a caller can import this module, choose, and then
import the heavy ones.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

ENDPOINT_ENV = "HF_ENDPOINT"

DEFAULT_ENDPOINT = "https://huggingface.co"
# The mirror most widely used where the main host is not reachable. It serves
# the same repository paths, so nothing else in the download path changes.
MIRROR_ENDPOINT = "https://hf-mirror.com"

# A small file that exists in one of the repositories this product downloads.
# Any of them would do: the point is to make the CDN hand over bytes, not to
# check this particular repository.
_PROBE_PATH = "/mlx-community/whisper-large-v3-mlx/resolve/main/config.json"

_PROBE_TIMEOUT_SECONDS = 8.0


def probe(endpoint: str, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """Whether this endpoint hands over actual file bytes within the timeout."""
    url = endpoint.rstrip("/") + _PROBE_PATH
    request = urllib.request.Request(url, headers={"User-Agent": "FluentFlow-Local"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                return False
            # Reading one byte is what separates "the server answered" from
            # "the server is sending the file".
            return bool(response.read(1))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("%s is not usable for model downloads: %s", endpoint, exc)
        return False


def select_endpoint(timeout: float = _PROBE_TIMEOUT_SECONDS) -> tuple[str, str]:
    """Return the endpoint to download from and a line explaining the choice.

    An endpoint the user or the launcher already set is honoured without a
    probe: they know something about this network that a probe cannot discover,
    and spending eight seconds to second-guess them would be rude.
    """
    configured = (os.environ.get(ENDPOINT_ENV) or "").strip()
    if configured:
        return configured, f"使用已配置的下载源：{configured}"

    if probe(DEFAULT_ENDPOINT, timeout):
        return DEFAULT_ENDPOINT, f"下载源：{DEFAULT_ENDPOINT}"

    if probe(MIRROR_ENDPOINT, timeout):
        return MIRROR_ENDPOINT, (
            f"连不上 {DEFAULT_ENDPOINT}，改用镜像 {MIRROR_ENDPOINT}"
        )

    # Both unreachable: hand back the default so the failure comes from the
    # download itself, with its own message, rather than from a guess made here.
    return DEFAULT_ENDPOINT, (
        f"{DEFAULT_ENDPOINT} 和镜像 {MIRROR_ENDPOINT} 都连不上，"
        "仍然按默认源尝试。如果失败，请检查网络或代理。"
    )


def apply_to_environment(timeout: float = _PROBE_TIMEOUT_SECONDS) -> tuple[str, str]:
    """Choose an endpoint and put it where `huggingface_hub` will read it.

    Call this before importing anything that imports `huggingface_hub`.
    """
    endpoint, explanation = select_endpoint(timeout)
    os.environ[ENDPOINT_ENV] = endpoint
    return endpoint, explanation


__all__ = [
    "DEFAULT_ENDPOINT",
    "ENDPOINT_ENV",
    "MIRROR_ENDPOINT",
    "apply_to_environment",
    "probe",
    "select_endpoint",
]
