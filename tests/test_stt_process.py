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

    def test_the_precision_is_left_to_the_device(self) -> None:
        """A fixed int8 ran every NVIDIA transcription at the CPU's precision;
        unset, local_stt picks float16 on the GPU and int8 on the CPU."""
        import backend.core.local_stt as local_stt
        from backend.core import stt_process

        seen: dict = {}

        def fake_transcribe(path, **kwargs):
            seen.update(kwargs)
            return "done"

        original = local_stt.transcribe_audio
        local_stt.transcribe_audio = fake_transcribe
        try:
            out: list = []

            class _Queue:
                def put(self, payload):
                    out.append(payload)

            stt_process._transcribe_worker(_Queue(), "a.wav", {"model_size": "large-v3"})
        finally:
            local_stt.transcribe_audio = original
        self.assertIsNone(seen["compute_type"])
        self.assertEqual(inspect.signature(start_transcription_process).parameters["compute_type"].default, None)
        self.assertEqual(local_stt._compute_type_for("cuda"), "float16")
        self.assertEqual(local_stt._compute_type_for("cpu"), "int8")

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
