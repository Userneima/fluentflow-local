"""Tests for choosing where the model weights come from.

This decision is the difference between a product that installs and one that
hangs: the failure it exists for is a reachable API in front of a CDN that
sends nothing, which looks like a slow download rather than a broken one.
"""

from __future__ import annotations

import io
import unittest
from unittest import mock

from backend.core import hf_endpoint


class _Response(io.BytesIO):
    """Enough of an HTTP response for the probe: a status and some bytes."""

    def __init__(self, status: int = 200, body: bytes = b"{") -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class TestProbe(unittest.TestCase):
    def test_bytes_mean_usable(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_Response()):
            self.assertTrue(hf_endpoint.probe(hf_endpoint.DEFAULT_ENDPOINT))

    def test_an_answer_with_no_body_is_not_usable(self) -> None:
        """The exact failure this module exists for: 200, then zero bytes."""
        with mock.patch("urllib.request.urlopen", return_value=_Response(body=b"")):
            self.assertFalse(hf_endpoint.probe(hf_endpoint.DEFAULT_ENDPOINT))

    def test_an_error_status_is_not_usable(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_Response(status=503)):
            self.assertFalse(hf_endpoint.probe(hf_endpoint.DEFAULT_ENDPOINT))

    def test_a_timeout_is_not_usable(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            self.assertFalse(hf_endpoint.probe(hf_endpoint.DEFAULT_ENDPOINT))


class TestSelectEndpoint(unittest.TestCase):
    def test_a_configured_endpoint_is_used_without_probing(self) -> None:
        """Someone who set this knows something about the network a probe cannot."""
        with mock.patch.dict("os.environ", {hf_endpoint.ENDPOINT_ENV: "https://example.test"}), \
                mock.patch.object(hf_endpoint, "probe") as probe:
            endpoint, _ = hf_endpoint.select_endpoint()
        self.assertEqual(endpoint, "https://example.test")
        probe.assert_not_called()

    def test_the_main_host_wins_when_it_works(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(hf_endpoint, "probe", return_value=True):
            endpoint, _ = hf_endpoint.select_endpoint()
        self.assertEqual(endpoint, hf_endpoint.DEFAULT_ENDPOINT)

    def test_the_mirror_takes_over_when_the_main_host_does_not_answer(self) -> None:
        def only_the_mirror(endpoint: str, *_args: object, **_kwargs: object) -> bool:
            return endpoint == hf_endpoint.MIRROR_ENDPOINT

        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(hf_endpoint, "probe", side_effect=only_the_mirror):
            endpoint, explanation = hf_endpoint.select_endpoint()
        self.assertEqual(endpoint, hf_endpoint.MIRROR_ENDPOINT)
        self.assertIn(hf_endpoint.MIRROR_ENDPOINT, explanation)

    def test_neither_reachable_still_returns_a_usable_default(self) -> None:
        """The download's own error is better than one guessed here."""
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(hf_endpoint, "probe", return_value=False):
            endpoint, explanation = hf_endpoint.select_endpoint()
        self.assertEqual(endpoint, hf_endpoint.DEFAULT_ENDPOINT)
        self.assertIn("连不上", explanation)

    def test_apply_puts_the_choice_where_the_library_reads_it(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(hf_endpoint, "probe", return_value=True):
            endpoint, _ = hf_endpoint.apply_to_environment()
            import os

            self.assertEqual(os.environ[hf_endpoint.ENDPOINT_ENV], endpoint)


class TestApplyAtStartup(unittest.TestCase):
    """The service's own call: it must never raise and must honour an override."""

    def test_a_configured_endpoint_is_kept_without_probing(self) -> None:
        with mock.patch.dict("os.environ", {hf_endpoint.ENDPOINT_ENV: "https://example.test"}), \
                mock.patch.object(hf_endpoint, "probe") as probe:
            endpoint = hf_endpoint.apply_at_startup()
            import os

            self.assertEqual(os.environ[hf_endpoint.ENDPOINT_ENV], "https://example.test")
        self.assertEqual(endpoint, "https://example.test")
        probe.assert_not_called()

    def test_the_mirror_is_chosen_when_the_main_host_is_down(self) -> None:
        def only_the_mirror(endpoint: str, *_args: object, **_kwargs: object) -> bool:
            return endpoint == hf_endpoint.MIRROR_ENDPOINT

        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(hf_endpoint, "probe", side_effect=only_the_mirror):
            endpoint = hf_endpoint.apply_at_startup()
            import os

            self.assertEqual(os.environ[hf_endpoint.ENDPOINT_ENV], hf_endpoint.MIRROR_ENDPOINT)
        self.assertEqual(endpoint, hf_endpoint.MIRROR_ENDPOINT)

    def test_a_probe_that_blows_up_never_stops_startup(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(hf_endpoint, "probe", side_effect=RuntimeError("no resolver")):
            endpoint = hf_endpoint.apply_at_startup()
        self.assertEqual(endpoint, hf_endpoint.DEFAULT_ENDPOINT)

    def test_the_service_chooses_before_importing_anything_heavy(self) -> None:
        """`huggingface_hub` freezes HF_ENDPOINT at import, so the composition
        root has to choose right after loading .env and before the imports that
        can pull the hub in. Checked on the source because the module has long
        been imported by the time any test runs."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "backend" / "local_main.py").read_text(encoding="utf-8")
        env_at = source.index("load_project_env()\n")
        choice_at = source.index("hf_endpoint.apply_at_startup()")
        first_heavy_import = source.index("from backend.core.app_factory import")
        self.assertLess(env_at, choice_at)
        self.assertLess(choice_at, first_heavy_import)


if __name__ == "__main__":
    unittest.main()
