"""The project-owned CUDA runtime is reported, never assumed."""

from pathlib import Path

from backend.core.windows_gpu_runtime import inspect_windows_gpu_runtime

RUNTIME_DLLS = (
    ("nvidia/cublas/bin", "cublas64_12.dll"),
    ("nvidia/cudnn/bin", "cudnn64_9.dll"),
)


def _populate(root: Path, entries=RUNTIME_DLLS) -> None:
    for relative, dll in entries:
        directory = root.joinpath(*relative.split("/"))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / dll).touch()


def test_missing_runtime_names_every_dll_that_is_absent(tmp_path: Path) -> None:
    status = inspect_windows_gpu_runtime(site_packages=tmp_path, platform="win32")

    assert not status.ready
    assert status.missing_dlls == ("cublas64_12.dll", "cudnn64_9.dll")


def test_complete_runtime_is_ready_and_hands_back_its_directories(tmp_path: Path) -> None:
    _populate(tmp_path)

    status = inspect_windows_gpu_runtime(site_packages=tmp_path, platform="win32")

    assert status.ready
    assert status.missing_dlls == ()
    assert len(status.directories) == 2


def test_half_an_install_is_not_ready(tmp_path: Path) -> None:
    """cuBLAS alone loads far enough to crash inside CTranslate2 rather than here."""
    _populate(tmp_path, entries=RUNTIME_DLLS[:1])

    status = inspect_windows_gpu_runtime(site_packages=tmp_path, platform="win32")

    assert not status.ready
    assert status.missing_dlls == ("cudnn64_9.dll",)


def test_other_platforms_are_reported_ready_so_they_are_never_downgraded(tmp_path: Path) -> None:
    """A Mac has no DLLs to find, and must not be told its GPU runtime is broken."""
    status = inspect_windows_gpu_runtime(site_packages=tmp_path, platform="darwin")

    assert not status.supported_platform
    assert status.ready
    assert status.missing_dlls == ()
