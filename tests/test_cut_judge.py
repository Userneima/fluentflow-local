"""Who decides the cut, and that it is shown the picture when it can see.

Requirements (owner, 2026-10-16):
1. Claude through the machine's subscription or the user's Anthropic key comes
   first; then a picture-reading model the user named (OpenAI-compatible: Kimi,
   MiMo, a relay); the text model last.
2. A judge that can see is shown screenshots of the long gaps whose picture
   moved, two each, labelled with the gap's number; gaps with a still picture
   get none. A text-only judge gets the timeline only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import cut_judge, silence_cuts, survey_cut, visual_note_channel
from backend.core.silence_cuts import CutPlan, TimeRange

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _channel(available: bool, name: str = visual_note_channel.CHANNEL_SUBSCRIPTION):
    return SimpleNamespace(available=available, name=name, model="claude-x")


def test_claude_comes_first(monkeypatch):
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda key: _channel(True))
    monkeypatch.setenv(cut_judge.VISION_BASE_URL_ENV, "http://relay/v1")
    monkeypatch.setenv(cut_judge.VISION_API_KEY_ENV, "k")
    monkeypatch.setenv(cut_judge.VISION_MODEL_ENV, "kimi-k2.6")

    judge = cut_judge.find(api_key="")

    assert judge.name == visual_note_channel.CHANNEL_SUBSCRIPTION and judge.sees_images


def test_a_named_picture_model_comes_before_the_text_model(monkeypatch):
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda key: _channel(False))
    monkeypatch.setenv(cut_judge.VISION_BASE_URL_ENV, "http://relay/v1")
    monkeypatch.setenv(cut_judge.VISION_API_KEY_ENV, "k")
    monkeypatch.setenv(cut_judge.VISION_MODEL_ENV, "kimi-k2.6")

    judge = cut_judge.find(api_key="")

    assert judge.name == "vision:kimi-k2.6" and judge.sees_images


def test_the_text_model_is_last_and_sees_nothing(monkeypatch):
    from backend.core import ai_summarizer

    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda key: _channel(False))
    for name in (cut_judge.VISION_BASE_URL_ENV, cut_judge.VISION_API_KEY_ENV, cut_judge.VISION_MODEL_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **kw: (lambda system, user: "ok"))

    judge = cut_judge.find(api_key="")

    assert judge.name == "text_model" and not judge.sees_images and judge.text("s", "u") == "ok"


@needs_ffmpeg
def test_a_judge_that_sees_is_shown_the_gaps_whose_picture_moved(tmp_path):
    source = tmp_path / "talk.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:d=40:r=10",
                    "-c:v", "libx264", str(source)], check=True)
    cuts = [TimeRange(10.0, 16.0), TimeRange(24.0, 30.0)]
    plan = CutPlan(source_duration_seconds=40.0, cuts=cuts,
                   keeps=silence_cuts._keeps_between(40.0, cuts, silence_cuts.MIN_KEEP_SECONDS),
                   silences_found=2, noise_db=-30, min_silence_seconds=0.25, padding_seconds=0.1)
    seen = {}

    def look(system, user, images):
        seen.update(system=system, user=user, labels=[label for label, _ in images],
                    sizes=[path.stat().st_size for _, path in images])
        return '{"keep_gaps": [], "remove": []}'

    _, record = survey_cut.revise_plan(
        source, plan, [{"start": 0.5, "end": 9.5, "text": "我来演示一下"}], lambda s, u: "{}",
        pictures=lambda src, gaps: {0: 0.6, 1: 0.0}, look=look,
    )

    assert seen["labels"] == ["空白 #1 的截图 1（原片 12 秒）", "空白 #1 的截图 2（原片 14 秒）"]
    assert all(size > 0 for size in seen["sizes"])
    assert "〔空白 #1 · 10.0–16.0 秒 · 长 6.0 秒 · 画面一直在变 · 附了 2 张截图〕" in seen["user"]
    assert "附了" not in seen["user"].split("空白 #2")[1]
    assert "看截图判断" in seen["system"] and record["screenshots_sent"] == 2


def test_a_text_only_judge_gets_the_timeline_only():
    cuts = [TimeRange(10.0, 16.0)]
    plan = CutPlan(source_duration_seconds=40.0, cuts=cuts, keeps=[TimeRange(0, 10), TimeRange(16, 40)],
                   silences_found=1, noise_db=-30, min_silence_seconds=0.25, padding_seconds=0.1)
    seen = {}

    def ask(system, user):
        seen["system"] = system
        return '{"keep_gaps": [], "remove": []}'

    _, record = survey_cut.revise_plan(Path("x.mp4"), plan, [{"start": 0, "end": 9, "text": "开场"}], ask,
                                       pictures=lambda src, gaps: {0: 0.6})

    assert "看截图判断" not in seen["system"] and record["screenshots_sent"] == 0
