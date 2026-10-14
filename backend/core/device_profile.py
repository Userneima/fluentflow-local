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
  ``docs/04-维护手册.md``); a preset only selects among paths that exist.

Every setting derived here can still be set directly by its own variable, which
wins.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Iterator, Sequence

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


def _saved_choice(name: str) -> str:
    """A choice saved on the settings page, or "" when none can be read."""
    try:
        from backend.core.local_config import get_preference

        return str(get_preference(name) or "").strip().lower()
    except Exception:  # noqa: BLE001 - an unreadable config falls back to the default
        return ""


def preset() -> str:
    """``FLUENTFLOW_PROCESSING_PRESET`` if set, else the choice saved on the
    settings page, else ``quality``."""
    chosen = (os.environ.get("FLUENTFLOW_PROCESSING_PRESET") or "").strip().lower()
    if chosen not in PRESETS:
        chosen = _saved_choice("processing_preset")
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


# One cut encoder per this many cores: each encoder is itself multi-threaded.
# Three per encoder keeps the measured three on the 10-core machine they were
# tuned on.
CORES_PER_RENDER_WORKER = 3


def render_workers(profile: DeviceProfile | None = None) -> int:
    """Cut encoders side by side, by memory and by cores, whichever allows fewer,
    and never more than the CPU share allows threads."""
    override = _env_int("FLUENTFLOW_RENDER_WORKERS")
    if override:
        return override
    profile = profile or current()
    by_memory = int(profile.memory_gb // MEMORY_GB_PER_RENDER_WORKER) if profile.memory_gb else 3
    by_cores = max(1, profile.cpu_cores // CORES_PER_RENDER_WORKER)
    budget = cpu_thread_budget(profile) or MAX_RENDER_WORKERS
    return max(1, min(by_memory, by_cores, budget, MAX_RENDER_WORKERS))


# How much of the processor a cut may take. Requirement (owner, 2026-10-13):
# leave the machine room by default, let the user choose, and say plainly that
# at full use a video playing beside the cut may stutter or pop. That stutter is
# the player being starved, not a fault in any file: on 2026-10-13 a fanless
# 10-core Air at load average 44 (three encoders, each with ffmpeg's default
# thread count) made the untouched source play with pops too, and every file
# played cleanly once the cut finished.
#
# Two levers, both on the ffmpeg processes only: a thread budget split across
# the encoders that run side by side, and a lower scheduling priority, so what
# the user is doing in the foreground is served first.
CPU_USAGE_LIGHT = "light"
CPU_USAGE_BALANCED = "balanced"
CPU_USAGE_FULL = "full"
CPU_USAGES = (CPU_USAGE_LIGHT, CPU_USAGE_BALANCED, CPU_USAGE_FULL)
_CPU_SHARE = {CPU_USAGE_LIGHT: 0.5, CPU_USAGE_BALANCED: 0.75}
_CPU_NICENESS = {CPU_USAGE_LIGHT: 15, CPU_USAGE_BALANCED: 10}


def cpu_usage() -> str:
    """``FLUENTFLOW_CPU_USAGE`` if set, else the choice saved on the settings
    page, else ``balanced``."""
    chosen = (os.environ.get("FLUENTFLOW_CPU_USAGE") or "").strip().lower()
    if chosen not in CPU_USAGES:
        chosen = _saved_choice("cpu_usage")
    return chosen if chosen in CPU_USAGES else CPU_USAGE_BALANCED


def cpu_thread_budget(profile: DeviceProfile | None = None) -> int | None:
    """Threads all cut encoders together may use; None at full use (no limit)."""
    share = _CPU_SHARE.get(cpu_usage())
    if share is None:
        return None
    profile = profile or current()
    return max(1, round(profile.cpu_cores * share))


def process_niceness() -> int:
    """How far below normal priority ffmpeg runs (0 = normal)."""
    return _CPU_NICENESS.get(cpu_usage(), 0)


# How many encodes run side by side right now. Set by the one place that runs
# them in parallel (``silence_cuts.render_keeps``) for as long as its pool
# lives; everything else runs one ffmpeg at a time and gets the whole budget.
# Splitting by the configured worker count instead held the single-process
# frame-rate pass to a third of its budget (found 2026-10-14).
_parallel_encodes = 1
_parallel_lock = threading.Lock()


@contextmanager
def parallel_encodes(count: int) -> Iterator[None]:
    """Within this block, each ffmpeg gets ``budget // count`` threads."""
    global _parallel_encodes
    with _parallel_lock:
        previous, _parallel_encodes = _parallel_encodes, max(1, int(count))
    try:
        yield
    finally:
        with _parallel_lock:
            _parallel_encodes = previous


def limit_ffmpeg_threads(argv: Sequence[str], profile: DeviceProfile | None = None) -> list[str]:
    """``argv`` with each decoder, encoder and filter graph of an ffmpeg command
    held to its share of the thread budget. Commands that are not ffmpeg, and
    every command at full use, come back unchanged."""
    argv = [str(part) for part in argv]
    budget = cpu_thread_budget(profile)
    if budget is None or not argv or os.path.basename(argv[0]) != "ffmpeg" or "-threads" in argv:
        return argv
    threads = str(max(1, budget // _parallel_encodes))
    out = [argv[0], "-filter_threads", threads, "-filter_complex_threads", threads]
    index = 1
    while index < len(argv):
        part = argv[index]
        if part == "-i":
            out += ["-threads", threads, part]
        elif part in {"-c:v", "-vcodec", "-codec:v"} and index + 1 < len(argv):
            out += [part, argv[index + 1], "-threads", threads]
            index += 1
        else:
            out.append(part)
        index += 1
    return out


def lower_priority(pid: int) -> None:
    """Move a started process below normal priority; nothing at full use.

    Done from the parent after the start rather than in the child before it
    (``preexec_fn``), which Python documents as unsafe when threads are running
    and can deadlock the child: the cut starts several encoders from a thread
    pool at once. The process runs at normal priority for the instant between
    its start and this call."""
    niceness = process_niceness()
    if niceness <= 0:
        return
    try:
        if os.name == "nt":
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x0200, False, pid)  # PROCESS_SET_INFORMATION
            if handle:
                ctypes.windll.kernel32.SetPriorityClass(handle, 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.setpriority(os.PRIO_PROCESS, pid, niceness)
    except (OSError, AttributeError):
        pass  # the process already ended, or the platform has no such call


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
        "cpu_usage": cpu_usage(),
        "cpu_thread_budget": cpu_thread_budget(),
    }


__all__ = [
    "DeviceProfile",
    "PRESETS",
    "CPU_USAGES",
    "audio_first",
    "cap_video_bitrate",
    "cpu_thread_budget",
    "cpu_usage",
    "limit_ffmpeg_threads",
    "lower_priority",
    "parallel_encodes",
    "process_niceness",
    "current",
    "hardware_bitrate_factor",
    "hardware_encode",
    "preset",
    "queue_concurrency",
    "render_workers",
    "summary",
]
