"""Tests for cancellable STT subprocess helpers."""

from __future__ import annotations

import multiprocessing as mp
import inspect
import time
import unittest

from backend.core.stt_process import start_transcription_process, terminate_process


def _sleep_for_cancel_test() -> None:
    time.sleep(30)


class TestSttProcess(unittest.TestCase):
    def test_default_transcription_mode_is_quality_first_not_chunked(self) -> None:
        signature = inspect.signature(start_transcription_process)
        self.assertEqual(signature.parameters["chunk_seconds"].default, 0)

    def test_terminate_process_kills_running_child(self) -> None:
        ctx = mp.get_context("spawn")
        process = ctx.Process(target=_sleep_for_cancel_test)
        process.start()
        try:
            self.assertTrue(process.is_alive())
            self.assertTrue(terminate_process(process, timeout=0.5))
            self.assertFalse(process.is_alive())
        finally:
            if process.is_alive():
                process.kill()
            process.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
