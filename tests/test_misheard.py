"""Lines the transcriber heard out of noise are told apart from speech.

Requirement (owner, 2026-10-15): on a Chinese recording, the cut judge is told
which lines are in another writing system (most likely noise heard as words),
and the report does not quote them as what was said.
"""

from backend.core import cut_report, misheard

CHINESE = ["今天我们讲一下这个活动", "大家可以看一下这个 Demo 页面", "好的谢谢大家"]


def test_lines_in_another_script_are_told_apart_on_a_chinese_recording():
    foreign = misheard.tagger([{"text": t} for t in CHINESE])

    assert foreign("ご视聴ありがとうございました")
    assert foreign("영상편집 및 자막이 도움이 되셨다면")
    assert foreign("Субтитры подогнал «Симон»")
    assert foreign("We'll do them in a minute please.")
    assert not foreign("大家可以看一下这个 Demo 页面")
    assert not foreign("好 三 二 一")


def test_an_english_recording_is_left_alone():
    foreign = misheard.tagger([{"text": "We will start the session now"}])

    assert not foreign("We'll do them in a minute please.")


def test_the_report_does_not_quote_them_as_said():
    segments = [{"start": 0, "end": 5, "text": t} for t in CHINESE] + [
        {"start": 10, "end": 12, "text": "ご视聴ありがとうございました"},
        {"start": 12, "end": 14, "text": "你穿长裤"},
    ]

    assert cut_report._text_within(segments, 9, 15) == "你穿长裤"
