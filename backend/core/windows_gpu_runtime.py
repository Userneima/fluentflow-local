"""Project-managed NVIDIA runtime discovery for Windows local transcription.

The CTranslate2 Windows wheel loads CUDA libraries dynamically.  Installing
the matching NVIDIA Python packages keeps those DLLs inside FluentFlow's
virtual environment, but Windows does not search package ``bin`` directories
by default.  This module makes that relationship explicit before
``faster_whisper`` imports CTranslate2.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path


_REQUIRED_DLLS = {
    "nvidia/cublas/bin": "cublas64_12.dll",
    "nvidia/cudnn/bin": "cudnn64_8.dll",
}
_DLL_DIRECTORY_HANDLES: list[object] = []
_REGISTERED_DLL_DIRECTORIES: set[str] = set()


@dataclass(frozen=True)
class WindowsGpuRuntimeStatus:
    """Availability of the CUDA runtime bundled in this Python environment."""

    supported_platform: bool
    ready: bool
    directories: tuple[Path, ...]
    missing_dlls: tuple[str, ...]

    @property
    def install_hint(self) -> str:
        return "python -m pip install -r requirements-windows-gpu.txt"


def _site_packages_dir() -> Path:
    return Path(sysconfig.get_paths()["purelib"])


def inspect_windows_gpu_runtime(
    *,
    site_packages: Path | None = None,
    platform: str | None = None,
) -> WindowsGpuRuntimeStatus:
    """Inspect the project venv without importing CUDA or loading a model."""
    is_windows = (platform or sys.platform).startswith("win")
    if not is_windows:
        return WindowsGpuRuntimeStatus(False, True, (), ())

    root = site_packages or _site_packages_dir()
    directories: list[Path] = []
    missing: list[str] = []
    for relative_dir, dll_name in _REQUIRED_DLLS.items():
        directory = root.joinpath(*relative_dir.split("/"))
        if directory.is_dir():
            directories.append(directory)
        if not (directory / dll_name).is_file():
            missing.append(dll_name)

    return WindowsGpuRuntimeStatus(
        supported_platform=True,
        ready=not missing,
        directories=tuple(directories),
        missing_dlls=tuple(missing),
    )


def configure_windows_gpu_runtime() -> WindowsGpuRuntimeStatus:
    """Make venv-owned CUDA DLL directories visible to the current process.

    The handles returned by :func:`os.add_dll_directory` must remain alive for
    as long as CTranslate2 can load dependencies, hence the module-level list.
    This function is deliberately idempotent and never installs packages.
    """
    status = inspect_windows_gpu_runtime()
    if not status.supported_platform or not status.ready:
        return status

    existing_path = os.environ.get("PATH", "")
    existing_parts = {part.casefold() for part in existing_path.split(os.pathsep) if part}
    prepend: list[str] = []
    for directory in status.directories:
        value = str(directory)
        if value.casefold() not in existing_parts:
            prepend.append(value)
        if value.casefold() in _REGISTERED_DLL_DIRECTORIES:
            continue
        try:
            handle = os.add_dll_directory(value)
        except (AttributeError, OSError):
            # PATH remains a useful fallback for older Python / unusual hosts.
            continue
        _DLL_DIRECTORY_HANDLES.append(handle)
        _REGISTERED_DLL_DIRECTORIES.add(value.casefold())

    if prepend:
        os.environ["PATH"] = os.pathsep.join([*prepend, existing_path])
    return status


__all__ = [
    "WindowsGpuRuntimeStatus",
    "configure_windows_gpu_runtime",
    "inspect_windows_gpu_runtime",
]
