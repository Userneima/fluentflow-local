"""Whether the local transcription model is on this machine, and how to fetch it.

Kept apart from `local_stt` because the two answer different questions at
different times: that module decides what a run will load, this one decides
whether the install is finished. The installer, the readiness report and the
command-line fetcher all come through here so they cannot disagree about which
file counts as "the model is here".

Nothing in this module loads a model. Asking whether a 3GB file exists by
constructing the engine that reads it would make the readiness check as slow as
a transcription, and on a machine where the file is missing it would download it
as a side effect of being asked a question.
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.core.local_stt import PlannedModel, planned_model

logger = logging.getLogger(__name__)

__all__ = [
    "PlannedModel",
    "planned_model",
    "cached_weights_path",
    "is_downloaded",
    "download_size_bytes",
    "ensure_downloaded",
    "known_repos",
    "cache_dirs",
]


def cached_weights_path(plan: PlannedModel) -> Path | None:
    """The weights file already on disk for this plan, or None.

    A pre-downloaded directory wins: `local_stt._resolve_model` prefers it over
    the Hugging Face cache, so a check that ignored it would report a model as
    missing on exactly the machines that took the trouble to place it by hand.
    """
    if plan.local_dir is not None:
        candidate = plan.local_dir / plan.weights_filename
        if candidate.is_file():
            return candidate

    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception as exc:  # noqa: BLE001 - no hub, no cache to look in
        logger.debug("huggingface_hub unavailable: %s", exc)
        return None

    hit = try_to_load_from_cache(plan.repo_id, plan.weights_filename)
    # The hub returns a sentinel object (not a path) to record "this file is
    # known not to exist upstream". Only a real string is a real file.
    return Path(hit) if isinstance(hit, str) else None


def is_downloaded(plan: PlannedModel) -> bool:
    return cached_weights_path(plan) is not None


def download_size_bytes(plan: PlannedModel) -> int | None:
    """Total size of the repository, or None when it cannot be asked.

    Used to tell someone what a first transcription is about to cost them
    before it starts. It needs the network, so every failure here is silent:
    not knowing the size is a reason to say nothing about it, never a reason to
    fail an install or a readiness check.
    """
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(plan.repo_id, files_metadata=True)
    except Exception as exc:  # noqa: BLE001 - offline, rate-limited, renamed
        logger.debug("Could not read the size of %s: %s", plan.repo_id, exc)
        return None

    total = sum(getattr(sibling, "size", None) or 0 for sibling in (info.siblings or []))
    return total or None


def ensure_downloaded(plan: PlannedModel) -> Path:
    """Download the planned weights if they are not already here; return their path.

    Each lane is fetched through its own library rather than through a repo id
    this module assembles: faster-whisper keeps its own table of conversions,
    and going around it is how an installer ends up with a correctly downloaded
    model the engine then refuses to find.
    """
    existing = cached_weights_path(plan)
    if existing is not None:
        return existing

    if plan.engine == "mlx":
        from huggingface_hub import snapshot_download

        snapshot_download(plan.repo_id)
    else:
        from faster_whisper import download_model

        download_model(plan.model_size)

    fetched = cached_weights_path(plan)
    if fetched is None:
        raise RuntimeError(
            f"{plan.repo_id} 下载后仍找不到 {plan.weights_filename}，"
            "请检查磁盘空间和网络后重试。"
        )
    return fetched


def known_repos() -> tuple[str, ...]:
    """Every model repository this product can download, on any machine.

    `planned_model` answers for this machine; this answers for the product, and
    the uninstaller needs the second one: a Mac that ran one job on the Apple
    lane and later fell back to the CPU lane has both downloaded, and an
    uninstaller that only removed the current plan would leave gigabytes behind
    while reporting success.
    """
    from backend.core import local_stt, local_stt_mlx

    mlx = tuple(local_stt_mlx.resolve_repo(size) for size in local_stt_mlx.supported_sizes())
    faster = tuple(
        f"{local_stt._FASTER_WHISPER_REPO_PREFIX}{size}"
        for size in sorted(set(local_stt._SIZE_ALIASES.values()))
    )
    return mlx + faster


def cache_dirs() -> list[Path]:
    """Directories on this machine holding those models, newest layout first.

    Only directories that exist are returned, so the caller can report and
    delete without checking again. The Hugging Face cache is shared with every
    other tool on the machine, which is why this returns the specific
    repository directories rather than the cache root.
    """
    from backend.core.local_stt import _LOCAL_MODEL_DIR

    found: list[Path] = []

    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        hub_root = Path(HF_HUB_CACHE)
    except Exception as exc:  # noqa: BLE001 - fall back to the documented default
        logger.debug("Could not read the hub cache location: %s", exc)
        hub_root = Path.home() / ".cache" / "huggingface" / "hub"

    for repo in known_repos():
        candidate = hub_root / f"models--{repo.replace('/', '--')}"
        if candidate.is_dir():
            found.append(candidate)

    if _LOCAL_MODEL_DIR.is_dir():
        found.append(_LOCAL_MODEL_DIR)

    return found
