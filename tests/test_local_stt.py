"""Tests for local STT text normalization helpers."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from backend.core.local_stt import (
    TranscriptSegment,
    _MAX_HOTWORDS_CHARS,
    _MAX_INITIAL_PROMPT_CHARS,
    _ZH_INITIAL_PROMPT,
    _build_transcribe_defaults,
    _drop_prompt_echoes,
    _drop_stuck_repetitions,
    _filter_repeated_hallucination_segments,
    _is_stuck_repetition,
    _looks_like_prompt_echo,
    _looks_like_low_confidence_hallucination,
    _normalize_language,
    _resolve_model,
    _simplify_segments,
    _transcribe_profile_defaults,
    _to_simplified_chinese,
    _write_wav_chunks,
)


class TestLocalStt(unittest.TestCase):
    def test_to_simplified_chinese(self) -> None:
        self.assertEqual(
            _to_simplified_chinese("現在我們會篩選創造營同學的作業"),
            "现在我们会筛选创造营同学的作业",
        )

    def test_simplify_segments(self) -> None:
        segments = (
            TranscriptSegment(start=0, end=1, text="現在"),
            TranscriptSegment(start=1, end=2, text="創造營"),
        )
        simplified = _simplify_segments(segments)
        self.assertEqual([s.text for s in simplified], ["现在", "创造营"])

    def test_prompt_echo_is_dropped_including_recombinations(self) -> None:
        """Whisper writes the injected prompt down as if it had been spoken.

        Every string here was taken from one 2h54m lecture, where 95 segments
        came back this way — 53 copies of the first one alone. The last two are
        why substring matching is not enough: the model recombines fragments of
        the prompt into sentences the prompt never contained.
        """
        for echoed in (
            "请使用简体中文输出，保留必要的英文术语。",
            "请使用简体中文输出。",
            "请使用简体中文语音转录。",
            "如果您不确定,请使用简体中文语音转录。",
        ):
            self.assertTrue(
                _looks_like_prompt_echo(echoed, _ZH_INITIAL_PROMPT),
                f"未识别为引导语回声：{echoed}",
            )

    def test_real_speech_survives_the_echo_filter(self) -> None:
        """The filter must not touch speech, including sentences that share words.

        The last three are real lines from the same lecture. They all contain
        「请…使用」 and would be caught by a keyword rule; the measured overlap
        for real speech topped out at 0.20 against 0.62 for the weakest echo,
        which is the gap the threshold sits in.
        """
        for spoken in (
            "对吧",
            "知道吧",
            "请你参考业界软件工程的最佳实践。",
            "同时,请使用记忆中的文字。",
            "请使用新开的对话,并且做任务。",
        ):
            self.assertFalse(
                _looks_like_prompt_echo(spoken, _ZH_INITIAL_PROMPT),
                f"真实语音被误判为回声：{spoken}",
            )

    def test_echo_filter_is_inert_without_a_prompt(self) -> None:
        self.assertFalse(_looks_like_prompt_echo("请使用简体中文输出。", None))
        self.assertFalse(_looks_like_prompt_echo("请使用简体中文输出。", ""))

    def test_drop_prompt_echoes_keeps_order_and_other_fields(self) -> None:
        segments = (
            TranscriptSegment(start=0, end=1, text="今天我们讲逆向工程", speaker="A"),
            TranscriptSegment(start=1, end=2, text="请使用简体中文输出，保留必要的英文术语。"),
            TranscriptSegment(start=2, end=3, text="这个技术很重要", speaker="B"),
        )
        kept = _drop_prompt_echoes(segments, _ZH_INITIAL_PROMPT)
        self.assertEqual([s.text for s in kept], ["今天我们讲逆向工程", "这个技术很重要"])
        self.assertEqual([s.speaker for s in kept], ["A", "B"])

    def test_stuck_repetition_inside_one_segment_is_dropped(self) -> None:
        """The loop that happens within a segment, not across segments.

        All three shapes were produced for real while building this: a meeting
        recording that was 47% digital silence returned one segment holding
        「跟着」 seventy times and another holding 「从此」 past a hundred, and a
        lecture returned a segment of 「语音」 repeated. The across-segments
        filter caught none of them.
        """
        self.assertTrue(_is_stuck_repetition("跟着," * 70))
        self.assertTrue(_is_stuck_repetition("从此" * 100))
        self.assertTrue(_is_stuck_repetition("这个" * 12))

    def test_ordinary_speech_is_not_a_stuck_repetition(self) -> None:
        """Repeating a word is normal; repeating only that word is not."""
        self.assertFalse(_is_stuck_repetition("然后,我们会做一些最初的试用。"))
        self.assertFalse(_is_stuck_repetition("对吧对吧"))
        self.assertFalse(
            _is_stuck_repetition("我们讲一下记忆。记忆是什么呢?有时候会做一个上下文的交接")
        )
        self.assertFalse(
            _is_stuck_repetition("请你参考业界软件工程的最佳实践，请你深度思考，给我几个方案。")
        )

    def test_drop_stuck_repetitions_keeps_the_rest(self) -> None:
        segments = (
            TranscriptSegment(start=0, end=1, text="今天我们讲逆向工程"),
            TranscriptSegment(start=1, end=2, text="跟着," * 70),
            TranscriptSegment(start=2, end=3, text="这个技术很重要"),
        )
        kept = _drop_stuck_repetitions(segments)
        self.assertEqual([s.text for s in kept], ["今天我们讲逆向工程", "这个技术很重要"])

    def test_fast_profile_uses_greedy_decode(self) -> None:
        defaults = _transcribe_profile_defaults("fast")
        self.assertEqual(defaults["beam_size"], 1)
        self.assertEqual(defaults["best_of"], 1)

    def test_unknown_profile_falls_back_to_balanced(self) -> None:
        defaults = _transcribe_profile_defaults("unknown")
        self.assertEqual(defaults["beam_size"], 3)
        self.assertEqual(defaults["best_of"], 3)

    def test_chinese_transcribe_defaults_do_not_inject_hotwords(self) -> None:
        defaults = _build_transcribe_defaults(
            language="zh",
            speed_profile="balanced",
            hotwords=None,
            initial_prompt=None,
        )

        self.assertNotIn("hotwords", defaults)
        self.assertIn("以下是普通话中文语音转录", defaults["initial_prompt"])

    def test_low_level_explicit_hotwords_are_bounded(self) -> None:
        defaults = _build_transcribe_defaults(
            language="zh",
            speed_profile="balanced",
            hotwords=" ".join(f"热词{i}" for i in range(200)),
            initial_prompt="提示词" * 200,
        )

        self.assertLessEqual(len(defaults["initial_prompt"]), _MAX_INITIAL_PROMPT_CHARS)
        self.assertLessEqual(len(defaults["hotwords"]), _MAX_HOTWORDS_CHARS)
        self.assertIn("以下是普通话中文语音转录", defaults["initial_prompt"])

    def test_legacy_low_quality_models_resolve_to_medium(self) -> None:
        self.assertEqual(_resolve_model("tiny"), "medium")
        self.assertEqual(_resolve_model("base"), "medium")
        self.assertEqual(_resolve_model("small"), "medium")
        self.assertEqual(_resolve_model("large-v3"), "medium")
        self.assertEqual(_resolve_model(""), "medium")

    def test_language_defaults_to_auto_detection(self) -> None:
        self.assertIsNone(_normalize_language(None))
        self.assertIsNone(_normalize_language("auto"))
        self.assertEqual(_normalize_language("zh-CN"), "zh")
        self.assertEqual(_normalize_language("English"), "en")

    def test_filter_repeated_short_hallucination_run(self) -> None:
        segments = (
            TranscriptSegment(start=0, end=2, text="真实内容"),
            TranscriptSegment(start=10, end=12, text="大学生 课题"),
            TranscriptSegment(start=12, end=14, text="大学生 课题"),
            TranscriptSegment(start=14, end=16, text="大学生 课题"),
            TranscriptSegment(start=16, end=18, text="大学生 课题"),
            TranscriptSegment(start=20, end=22, text="后续内容"),
        )
        filtered = _filter_repeated_hallucination_segments(segments)

        self.assertEqual([s.text for s in filtered], ["真实内容", "后续内容"])

    def test_keep_short_phrase_when_not_repeated_enough(self) -> None:
        segments = (
            TranscriptSegment(start=0, end=1, text="好"),
            TranscriptSegment(start=1, end=2, text="好"),
            TranscriptSegment(start=2, end=3, text="继续"),
        )
        filtered = _filter_repeated_hallucination_segments(segments)

        self.assertEqual([s.text for s in filtered], ["好", "好", "继续"])

    def test_low_confidence_segment_detection(self) -> None:
        class Segment:
            no_speech_prob = 0.9
            avg_logprob = -0.8
            compression_ratio = 1.0

        self.assertTrue(_looks_like_low_confidence_hallucination(Segment()))

    def test_write_wav_chunks_preserves_offsets_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.wav"
            with wave.open(str(src), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(10)
                writer.writeframes(b"\x00\x00" * 25)

            chunks = _write_wav_chunks(src, Path(tmp) / "chunks", chunk_seconds=1.0)

            self.assertEqual(len(chunks), 3)
            self.assertEqual([round(chunk.start, 2) for chunk in chunks], [0.0, 1.0, 2.0])
            self.assertEqual([round(chunk.duration, 2) for chunk in chunks], [1.0, 1.0, 0.5])
            for chunk in chunks:
                self.assertTrue(chunk.path.is_file())


if __name__ == "__main__":
    unittest.main()
