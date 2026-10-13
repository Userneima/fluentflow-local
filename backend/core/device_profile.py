"""What this machine can do, and the processing choices that follow from it.

Several limits in the pipeline were fixed numbers tuned on one 16GB Apple
silicon Mac: three encoders side by side in a cut, one task at a time, the
software encoder only. On a larger machine they leave it idle; on a smaller one
they can run it out of memory. The owner's rule (2026-10-13): a limit that comes
from one machine's capacity is one path among several, chosen for the machine
it runs on, not a rule of the product.

Two layers:

- ``current()`` reads the machine: cores (and performance cores on Apple
  silicon), memory, and whether ffmpeg has a hardware video encoder. Facts the
  system reports, read once per process; nothing here benchmarks.
- ``preset()`` is the user's priority, set with
  ``FLUENTFLOW_PROCESSING_PRESET``: ``quality`` (default), ``speed`` or
  ``size``. What each one changes was measured on 2026-10-13 (see
  ``docs/maintenance.md``); a preset only selects among paths that exist.

Every setting derived here can still be set directly by its own variable, which
wins.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

PRESET_QUALITY = "quality"
PRESET_SPEED = "speed"
PRESET_SIZE = "size"
PRESETS = (PRESET_QUALITY, PRESET_SPEED, PRESET_SIZE)

# One cut encoder per this much memory: the render is a job's memory peak, and
# three was what kept a 16GB machine clear of memory pressure.
MEMORY_GB_PER_RENDER_WORKER = 5
MAX_RENDER_WORKERS = 6


@dataclass(frozen=True)
class DeviceProfile:
    cpu_cores: int
    performance_cores: int
    memory_gb: float
    apple_silicon: bool
    hardware_video_encoder: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sysctl_int(name: str) -> int:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _memory_bytes() -> int:
    if platform.system() == "Darwin":
        return _sysctl_int("hw.memsize")
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def _has_hardware_video_encoder() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        listed = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return "h264_videotoolbox" in (listed.stdout or "")


@lru_cache(maxsize=1)
def current() -> DeviceProfile:
    cores = os.cpu_count() or 1
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    performance = _sysctl_int("hw.perflevel0.physicalcpu") if apple else 0
    return DeviceProfile(
        cpu_cores=cores,
        performance_cores=performance or cores,
        memory_gb=round(_memory_bytes() / 1024 ** 3, 1),
        apple_silicon=apple,
        hardware_video_encoder=_has_hardware_video_encoder(),
    )


def preset() -> str:
    chosen = (os.environ.get("FLUENTFLOW_PROCESSING_PRESET") or "").strip().lower()
    return chosen if chosen in PRESETS else PRESET_QUALITY


def _env_int(name: str) -> int | None:
    try:
        value = int((os.environ.get(name) or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _env_flag(name: str) -> bool | None:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def render_workers(profile: DeviceProfile | None = None) -> int:
    """Cut encoders side by side: by memory, and no more than half the
    performance cores, since each encoder is itself multi-threaded."""
    override = _env_int("FLUENTFLOW_RENDER_WORKERS")
    if override:
        return override
    profile = profile or current()
    by_memory = int(profile.memory_gb // MEMORY_GB_PER_RENDER_WORKER) if profile.memory_gb else 3
    by_cores = max(1, profile.performance_cores // 2)
    return max(1, min(by_memory, by_cores, MAX_RENDER_WORKERS))


def queue_concurrency() -> int:
    """Tasks processed at once. One unless set: running two side by side was
    measured slower on a 10-core 16GB machine, and nothing has been measured on
    a larger one yet."""
    return _env_int("FLUENTFLOW_QUEUE_CONCURRENCY") or 1


def hardware_encode() -> bool:
    explicit = _env_flag("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE")
    if explicit is not None:
        return explicit
    return preset() == PRESET_SPEED and current().hardware_video_encoder


def hardware_bitrate_factor() -> float:
    try:
        raw = (os.environ.get("FLUENTFLOW_DEBREATH_HARDWARE_BITRATE_FACTOR") or "").strip()
        if raw:
            return max(float(raw), 0.1)
    except ValueError:
        pass
    # The speed preset's hardware encode at the source's own rate smeared text
    # on a low-bitrate recording; twice the rate read like the software encode.
    return 2.0 if preset() == PRESET_SPEED else 1.0


def cap_video_bitrate() -> bool:
    explicit = _env_flag("FLUENTFLOW_DEBREATH_CAP_VIDEO_BITRATE")
    if explicit is not None:
        return explicit
    return preset() == PRESET_SIZE


def audio_first() -> bool:
    explicit = _env_flag("FLUENTFLOW_DEBREATH_AUDIO_FIRST")
    if explicit is not None:
        return explicit
    return preset() == PRESET_SPEED and current().hardware_video_encoder


def summary() -> dict[str, Any]:
    """What a task records about the machine and the choices made on it."""
    return {
        "preset": preset(),
        "device": current().as_dict(),
        "render_workers": render_workers(),
        "hardware_encode": hardware_encode(),
        "cap_video_bitrate": cap_video_bitrate(),
        "audio_first": audio_first(),
    }


__all__ = [
    "DeviceProfile",
    "PRESETS",
    "audio_first",
    "cap_video_bitrate",
    "current",
    "hardware_bitrate_factor",
    "hardware_encode",
    "preset",
    "queue_concurrency",
    "render_workers",
    "summary",
]
