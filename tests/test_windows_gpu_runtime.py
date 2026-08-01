"""Regression tests for project-managed Windows CUDA runtime discovery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.core.windows_gpu_runtime import inspect_windows_gpu_runtime


class WindowsGpuRuntimeTests(unittest.TestCase):
    def test_reports_missing_nvidia_runtime_dlls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = inspect_windows_gpu_runtime(site_packages=Path(tmp), platform="win32")
        self.assertFalse(status.ready)
        self.assertEqual(status.missing_dlls, ("cublas64_12.dll", "cudnn64_8.dll"))

    def test_accepts_complete_project_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative, dll in (
                ("nvidia/cublas/bin", "cublas64_12.dll"),
                ("nvidia/cudnn/bin", "cudnn64_8.dll"),
            ):
                directory = root.joinpath(*relative.split("/"))
                directory.mkdir(parents=True)
                (directory / dll).touch()
            status = inspect_windows_gpu_runtime(site_packages=root, platform="win32")
        self.assertTrue(status.ready)
        self.assertEqual(status.missing_dlls, ())


if __name__ == "__main__":
    unittest.main()
