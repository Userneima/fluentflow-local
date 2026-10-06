"""Tests for the Apple-Silicon transcription lane and the choice between lanes.

None of these require mlx-whisper to be installed: the package only exists on
Apple Silicon, and CI runs on Linux, so availability and failure are both
simulated. What is actually being pinned here is that the product keeps working
when the fast lane is absent or broken, and that the guards the CPU lane has are
not quietly missing from the fast one.
"""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from backend.core import local_stt, local_stt_mlx
from backend.core.local_stt import TranscriptSegment


class TestEngineResolution(unittest.TestCase):
    def test_explicit_cpu_never_consults_mlx(self) -> None:
        with mock.patch.object(local_stt_mlx, "unavailable_reason") as probe:
            self.assertEqual(local_stt.resolve_local_stt_engine("cpu"), ("faster_whisper", None))
            probe.assert_not_called()

    def test_auto_takes_mlx_when_available(self) -> None:
        with mock.patch.object(local_stt_mlx, "unavailable_reason", return_value=None):
            self.assertEqual(local_stt.resolve_local_stt_engine("auto"), ("mlx", None))

    def test_auto_falls_back_and_reports_why(self) -> None:
        with mock.patch.object(local_stt_mlx, "unavailable_reason", return_value="没装"):
            lane, reason = local_stt.resolve_local_stt_engine("auto")
        self.assertEqual(lane, "faster_whisper")
        self.assertEqual(reason, "没装")

    def test_demanding_mlx_falls_back_rather_than_failing(self) -> None:
        """A missing fast lane must cost speed, never the user's transcription."""
        with mock.patch.object(local_stt_mlx, "unavailable_reason", return_value="没装"):
            lane, reason = local_stt.resolve_local_stt_engine("mlx")
        self.assertEqual(lane, "faster_whisper")
        self.assertEqual(reason, "没装")

    def test_environment_sets_the_default_and_the_caller_overrides_it(self) -> None:
        with mock.patch.dict("os.environ", {"FLUENTFLOW_LOCAL_STT_ENGINE": "cpu"}):
            with mock.patch.object(local_stt_mlx, "unavailable_reason", return_value=None):
                self.assertEqual(local_stt.resolve_local_stt_engine()[0], "faster_whisper")
                self.assertEqual(local_stt.resolve_local_stt_engine("mlx")[0], "mlx")

    def test_unknown_engine_name_falls_back_to_auto(self) -> None:
        with mock.patch.object(local_stt_mlx, "unavailable_reason", return_value=None):
            self.assertEqual(local_stt.resolve_local_stt_engine("nonsense")[0], "mlx")


class TestUnavailableReason(unittest.TestCase):
    def test_non_mac_is_rejected_before_importing_anything(self) -> None:
        with mock.patch("platform.system", return_value="Linux"):
            reason = local_stt_mlx.unavailable_reason()
        self.assertIn("macOS", str(reason))

    def test_intel_mac_is_rejected(self) -> None:
        with mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("platform.machine", return_value="x86_64"):
            reason = local_stt_mlx.unavailable_reason()
        self.assertIn("Apple", str(reason))


class TestSpeechRuns(unittest.TestCase):
    """The fast lane's silence handling, which is the reason it is safe to use.

    Whisper invents text on silent windows and, with context carried forward,
    repeats the invention for minutes. faster-whisper avoids this with a VAD
    filter; MLX has none, so this lane finds the speech itself. If this logic
    regresses, the fast lane starts producing the repeated-phrase transcripts
    the CPU lane never produced.
    """

    def _runs(self, stamps, seconds=30.0):
        audio = np.zeros(int(seconds * local_stt_mlx.SAMPLE_RATE), dtype=np.float32)
        with mock.patch("faster_whisper.vad.get_speech_timestamps", return_value=stamps):
            return local_stt_mlx._speech_runs(audio, None)

    def test_silence_between_runs_is_excluded(self) -> None:
        sr = local_stt_mlx.SAMPLE_RATE
        runs = self._runs([
            {"start": 2 * sr, "end": 4 * sr},
            {"start": 20 * sr, "end": 25 * sr},
        ])
        self.assertEqual(len(runs), 2)
        # the 16 silent seconds in the middle never reach the model
        self.assertLess(runs[0][1], runs[1][0])
        self.assertGreater(runs[1][0] - runs[0][1], 10)

    def test_runs_are_padded_but_clamped_to_the_audio(self) -> None:
        sr = local_stt_mlx.SAMPLE_RATE
        runs = self._runs([{"start": 0, "end": 30 * sr}], seconds=30.0)
        self.assertEqual(runs[0][0], 0.0)
        self.assertEqual(runs[0][1], 30.0)

    def test_a_short_pause_does_not_become_a_decode_boundary(self) -> None:
        sr = local_stt_mlx.SAMPLE_RATE
        runs = self._runs([
            {"start": 1 * sr, "end": 3 * sr},
            {"start": int(3.2 * sr), "end": 6 * sr},
        ])
        self.assertEqual(len(runs), 1, "a 0.2s gap inside a sentence must not split it")

    def test_a_click_is_not_a_speech_run(self) -> None:
        sr = local_stt_mlx.SAMPLE_RATE
        runs = self._runs([{"start": sr, "end": sr + int(0.05 * sr)}])
        self.assertEqual(runs, [])

    def test_all_silence_yields_nothing(self) -> None:
        self.assertEqual(self._runs([]), [])


class TestFieldsSurviveNormalization(unittest.TestCase):
    """Word timings and speakers must survive the post-processing steps.

    `_simplify_segments` used to rebuild each segment from three fields, which
    silently dropped both. Word timings are the expensive kind of loss: nothing
    downstream can rebuild them, so losing them means transcribing again.
    """

    def test_simplify_keeps_words_and_speaker(self) -> None:
        segments = (
            TranscriptSegment(
                start=0,
                end=1,
                text="現在",
                speaker="A",
                words=({"start": 0.0, "end": 0.5, "text": "現", "conf": 0.9},),
            ),
        )
        out = local_stt._simplify_segments(segments)
        self.assertEqual(out[0].text, "现在")
        self.assertEqual(out[0].speaker, "A")
        self.assertIsNotNone(out[0].words)
        self.assertEqual(out[0].words[0]["text"], "现")
        self.assertEqual(out[0].words[0]["conf"], 0.9)

    def test_collect_takes_dict_words_and_ignores_other_shapes(self) -> None:
        dict_words = local_stt_mlx.RawSegment(
            start=0, end=1, text="你好",
            words=({"start": 0.0, "end": 0.4, "text": "你"},),
        )
        collected, _ = local_stt._collect_segments([dict_words])
        self.assertIsNotNone(collected[0].words)

        class FasterWhisperish:
            start, end, text = 0.0, 1.0, "你好"
            words = [("not", "a", "dict")]

        collected, _ = local_stt._collect_segments([FasterWhisperish()])
        self.assertIsNone(collected[0].words)


class TestFallbackOnFailure(unittest.TestCase):
    def test_a_broken_fast_lane_returns_none_instead_of_raising(self) -> None:
        """Callers fall back on None; an exception here would fail the job."""
        with mock.patch.object(local_stt_mlx, "transcribe", side_effect=RuntimeError("boom")):
            result = local_stt._transcribe_with_mlx(
                __import__("pathlib").Path(__file__),
                model_size="medium",
                vad_filter=True,
                language="zh",
                speed_profile="balanced",
                hotwords=None,
                initial_prompt=None,
                transcribe_kwargs={},
                word_timestamps=False,
                on_progress=None,
                on_status=None,
            )
        self.assertIsNone(result)


class TestRecordingLanguage(unittest.TestCase):
    """Each run detects its own language; the recording's is the one most speech had."""

    def _run(self, detections, language=None):
        import sys
        import tempfile
        import types

        calls = iter(detections)

        def fake_transcribe(clip, **kwargs):
            run_language, text = next(calls)
            return {"language": run_language, "segments": [{"start": 0.0, "end": 1.0, "text": text}]}

        fake_mlx = types.SimpleNamespace(transcribe=fake_transcribe)
        runs = [(0.0, 20.0), (20.0, 600.0), (600.0, 1200.0)][: len(detections)]
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file, \
                mock.patch.dict(sys.modules, {"mlx_whisper": fake_mlx}), \
                mock.patch("faster_whisper.audio.decode_audio", return_value=np.zeros(16000 * 1200, dtype=np.float32)), \
                mock.patch.object(local_stt_mlx, "_speech_runs", return_value=runs):
            segments, info, _ = local_stt_mlx.transcribe(audio_file.name, language=language)
        return [segment.text for segment in segments], info.language

    def test_an_intro_jingle_does_not_decide_the_language(self) -> None:
        texts, detected = self._run([("en", "Thank you."), ("zh", "同学你好"), ("zh", "欢迎选修")])

        self.assertEqual(detected, "zh")
        self.assertEqual(texts, ["同学你好", "欢迎选修"], "the jingle's stock line goes too")

    def test_real_words_in_another_language_are_kept(self) -> None:
        texts, detected = self._run([("en", "Agent harness"), ("zh", "我们来讲"), ("zh", "自我学习")])

        self.assertEqual(detected, "zh")
        self.assertIn("Agent harness", texts)

    def test_a_thank_you_in_the_recordings_own_language_is_kept(self) -> None:
        texts, detected = self._run([("en", "Hello everyone"), ("en", "Thank you.")])

        self.assertEqual(detected, "en")
        self.assertEqual(texts, ["Hello everyone", "Thank you."])

    def test_a_language_the_caller_chose_is_not_second_guessed(self) -> None:
        texts, detected = self._run([("en", "Thank you."), ("zh", "同学你好")], language="zh")

        self.assertEqual(detected, "zh")
        self.assertIn("Thank you.", texts)



class TestModelLoadTiming(unittest.TestCase):
    """"Model load" is the load, not the load plus the first stretch of decoding.

    It used to be timed around the first transcribe call, which on a recording
    that is one long run of speech is nearly the whole transcription: recorded
    as 187 seconds of "model load" on a 21-minute talk.
    """

    def _run(self, *, already_loaded: bool):
        import sys
        import tempfile
        import time as _time
        import types

        class Holder:
            model = "loaded" if already_loaded else None
            model_path = "mlx-community/whisper-large-v3-mlx" if already_loaded else None

            @classmethod
            def get_model(cls, path, dtype):
                if cls.model is None or cls.model_path != path:
                    _time.sleep(0.2)
                    cls.model, cls.model_path = "loaded", path
                return cls.model

        def fake_transcribe(clip, **kwargs):
            Holder.get_model(kwargs["path_or_hf_repo"], None)
            _time.sleep(0.3)
            return {"language": "zh", "segments": [{"start": 0.0, "end": 1.0, "text": "你好"}]}

        fake_mlx = types.SimpleNamespace(transcribe=fake_transcribe)
        fake_transcribe_module = types.SimpleNamespace(
            ModelHolder=Holder, mx=types.SimpleNamespace(float16="float16"),
        )
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file, \
                mock.patch.dict(sys.modules, {
                    "mlx_whisper": fake_mlx,
                    "mlx_whisper.transcribe": fake_transcribe_module,
                }), \
                mock.patch.object(local_stt_mlx, "resolve_repo", return_value="mlx-community/whisper-large-v3-mlx"), \
                mock.patch("faster_whisper.audio.decode_audio", return_value=np.zeros(16000 * 60, dtype=np.float32)), \
                mock.patch.object(local_stt_mlx, "_speech_runs", return_value=[(0.0, 60.0)]):
            _segments, _info, stats = local_stt_mlx.transcribe(audio_file.name, language="zh")
        return stats

    def test_the_load_is_timed_apart_from_decoding(self) -> None:
        stats = self._run(already_loaded=False)

        self.assertGreaterEqual(stats["model_load_seconds"], 0.15)
        self.assertLess(stats["model_load_seconds"], 0.3, "decoding is not counted as load")
        self.assertGreaterEqual(stats["first_run_seconds"], 0.3)

    def test_a_model_already_in_the_process_reads_as_a_cache_hit(self) -> None:
        stats = self._run(already_loaded=True)

        self.assertEqual(stats["model_load_seconds"], 0.0)

if __name__ == "__main__":
    unittest.main()
