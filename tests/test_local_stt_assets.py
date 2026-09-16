"""Tests for "which model will this machine load, and is it already here".

The installer, the readiness report and the uninstaller all read these
answers, and each one has a way of being wrong that is silent: downloading a
model no run will open, calling a machine ready while the weights are missing,
or reporting a clean uninstall with gigabytes left behind.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from backend.core import local_stt, local_stt_assets, local_stt_mlx
from backend.core.local_stt_policy import DEFAULT_LOCAL_STT_MODEL

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestDefaultModelAgreement(unittest.TestCase):
    def test_engine_default_is_the_policy_default(self) -> None:
        self.assertEqual(local_stt.DEFAULT_MODEL_SIZE, DEFAULT_LOCAL_STT_MODEL)

    def test_frontend_default_matches_the_backend(self) -> None:
        """The frontend sends a size on every submit, so its default is the real one.

        This is the shape of a bug that already shipped: the backend default
        moved to large-v3 and the frontend kept sending medium, so the setting
        that actually decided every transcription was the one nobody had
        updated, and the product quietly ran a model it had stopped tuning for.
        """
        source = (PROJECT_ROOT / "frontend" / "src" / "lib" / "settingsModel.js").read_text(encoding="utf-8")
        match = re.search(r"DEFAULT_STT_MODEL\s*=\s*'([^']+)'", source)
        self.assertIsNotNone(match, "settingsModel.js 里找不到 DEFAULT_STT_MODEL")
        self.assertEqual(match.group(1), DEFAULT_LOCAL_STT_MODEL)

    def test_submit_route_has_no_model_literal_of_its_own(self) -> None:
        """The submit route's fallback must not re-declare a default next to it."""
        source = (PROJECT_ROOT / "backend" / "routers" / "local_processing.py").read_text(encoding="utf-8")
        self.assertIn('or DEFAULT_LOCAL_STT_MODEL', source)
        self.assertNotIn('stt_model") or "").strip() or "medium"', source)


class TestRepositoryNames(unittest.TestCase):
    def test_faster_whisper_prefix_matches_the_librarys_own_table(self) -> None:
        """Pinned here so an upstream rename fails in CI, not in an installer.

        `local_stt` builds the repository id from a prefix in order to name the
        download before fetching it; faster-whisper resolves the same size
        through its own table. If those two ever disagree the installer
        downloads one thing and the engine looks for another.
        """
        from faster_whisper.utils import _MODELS

        for size in ("medium", "large-v3"):
            self.assertEqual(
                f"{local_stt._FASTER_WHISPER_REPO_PREFIX}{size}",
                _MODELS[size],
            )

    def test_known_repos_cover_both_lanes(self) -> None:
        """The uninstaller works from this list, not from the current machine.

        A Mac that ran the Apple lane and later fell back to the CPU one has
        both downloaded; a list built from `planned_model` would leave the
        other one on disk and still report success.
        """
        repos = local_stt_assets.known_repos()
        self.assertTrue(any(repo.startswith("mlx-community/") for repo in repos))
        self.assertTrue(any(repo.startswith("Systran/") for repo in repos))
        for size in local_stt_mlx.supported_sizes():
            self.assertIn(local_stt_mlx.resolve_repo(size), repos)


class TestPlannedModel(unittest.TestCase):
    def test_cpu_plan_names_the_model_the_run_will_really_load(self) -> None:
        """A CPU machine transcribes on medium, so it must not fetch large-v3."""
        with mock.patch.object(local_stt, "resolve_local_stt_engine", return_value=("faster_whisper", None)), \
                mock.patch.object(local_stt, "_resolve_stt_device", return_value=("cpu", None)):
            plan = local_stt.planned_model("large-v3")
        self.assertEqual(plan.engine, "faster_whisper")
        self.assertEqual(plan.model_size, "medium")
        self.assertEqual(plan.repo_id, "Systran/faster-whisper-medium")
        self.assertTrue(plan.downgraded)

    def test_gpu_plan_keeps_the_requested_model(self) -> None:
        with mock.patch.object(local_stt, "resolve_local_stt_engine", return_value=("faster_whisper", None)), \
                mock.patch.object(local_stt, "_resolve_stt_device", return_value=("cuda", None)):
            plan = local_stt.planned_model("large-v3")
        self.assertEqual(plan.model_size, "large-v3")
        self.assertFalse(plan.downgraded)

    def test_mlx_plan_reports_the_size_it_will_actually_fetch(self) -> None:
        """An unknown size collapses onto the default repo; the size must follow it.

        Otherwise the report says one model and the download brings another.
        """
        with mock.patch.object(local_stt, "resolve_local_stt_engine", return_value=("mlx", None)):
            plan = local_stt.planned_model("tiny")
        self.assertEqual(plan.engine, "mlx")
        self.assertEqual(plan.repo_id, local_stt_mlx.resolve_repo("tiny"))
        self.assertEqual(plan.repo_id, local_stt_mlx.resolve_repo(plan.model_size))

    def test_lane_fallback_reason_is_carried_into_the_plan(self) -> None:
        with mock.patch.object(local_stt, "resolve_local_stt_engine", return_value=("faster_whisper", "没装 mlx")), \
                mock.patch.object(local_stt, "_resolve_stt_device", return_value=("cpu", None)):
            plan = local_stt.planned_model()
        self.assertIn("没装 mlx", plan.note or "")


class TestCachedWeights(unittest.TestCase):
    def _plan(self, local_dir: Path | None = None) -> local_stt.PlannedModel:
        return local_stt.PlannedModel(
            engine="faster_whisper",
            model_size="medium",
            repo_id="Systran/faster-whisper-medium",
            weights_filename="model.bin",
            requested_size="large-v3",
            local_dir=local_dir,
        )

    def test_hub_sentinel_is_not_mistaken_for_a_file(self) -> None:
        """`try_to_load_from_cache` returns an object, not a path, for "not there".

        Treating that object as a hit would report every missing model as
        downloaded — the readiness check would go green and the installer would
        skip the fetch.
        """
        sentinel = object()
        with mock.patch("huggingface_hub.try_to_load_from_cache", return_value=sentinel):
            self.assertIsNone(local_stt_assets.cached_weights_path(self._plan()))

    def test_absent_weights_are_absent(self) -> None:
        with mock.patch("huggingface_hub.try_to_load_from_cache", return_value=None):
            self.assertFalse(local_stt_assets.is_downloaded(self._plan()))

    def test_a_hand_placed_directory_wins_over_the_hub_cache(self) -> None:
        """`local_stt._resolve_model` prefers it, so this check has to as well."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            local_dir = Path(tmp) / "medium"
            local_dir.mkdir()
            (local_dir / "model.bin").write_bytes(b"not a real model")
            with mock.patch("huggingface_hub.try_to_load_from_cache", return_value=None):
                found = local_stt_assets.cached_weights_path(self._plan(local_dir))
            self.assertEqual(found, local_dir / "model.bin")

    def test_size_lookup_failure_is_not_an_error(self) -> None:
        """Offline is a reason to say nothing about the size, never to fail."""
        with mock.patch("huggingface_hub.HfApi", side_effect=OSError("offline")):
            self.assertIsNone(local_stt_assets.download_size_bytes(self._plan()))


if __name__ == "__main__":
    unittest.main()
