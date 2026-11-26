"""A subtitle file is read in the encoding it was saved in.

Found 2026-11-25 (push review of subtitle files by path): a GB18030 .srt with
an even number of bytes was decoded as UTF-16, which accepts almost any even
run of bytes, and came out as garbage with no timed line; the page's subtitle
upload read it the same way.
"""

from __future__ import annotations

import pytest

from backend.core.transcript_parser import parse_transcript_file, utf16_without_mark

SRT = "1\n00:00:01,000 --> 00:00:03,000\n今天讲最小二乘法x\n"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030", "utf-16"])
def test_a_subtitle_file_reads_in_its_own_encoding(encoding):
    raw = SRT.encode(encoding)

    parsed = parse_transcript_file(raw, "lesson.srt")

    assert parsed.text == "今天讲最小二乘法x"
    assert len(parsed.segments) == 1


def test_the_gb18030_case_has_an_even_length():
    # The case that went wrong: an odd length fails as UTF-16 and was read right.
    assert len(SRT.encode("gb18030")) % 2 == 0


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_utf16_without_its_mark_is_told_apart(encoding):
    """(independent review, 2026-11-25) Such a file is not read as UTF-16, so
    it yields no timed line; the user is told it may be the encoding."""
    assert utf16_without_mark((SRT * 3).encode(encoding))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030", "utf-16"])
def test_other_encodings_are_not_taken_for_it(encoding):
    assert not utf16_without_mark((SRT * 3).encode(encoding))


# Review of 643178c: Chinese saved as UTF-16 without its mark has hardly a
# zero byte (only its line breaks, spaces and digits have one), so a .txt or
# .md of it was read as garbage full of NULs and replacement characters and a
# note was written from it. It is told apart too, and no ordinary file is.

ZH_TXT = "今天我们来讲一下人工智能产品经理的工作内容和方法论，首先是需求分析。然后是效果评估，最后是迭代。\n" * 40


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("text", [ZH_TXT, ZH_TXT.replace("\n", ""), "今天讲最小二乘法\n"],
                         ids=["lines", "one line", "short"])
def test_chinese_in_utf16_without_its_mark_is_told_apart(encoding, text):
    assert utf16_without_mark(text.encode(encoding))


ORDINARY = {
    "utf-8": ZH_TXT.encode("utf-8"),
    "utf-8 with mark": ZH_TXT.encode("utf-8-sig"),
    "gbk": ZH_TXT.encode("gbk"),
    "gb18030": ZH_TXT.encode("gb18030"),
    "mostly ascii": ("Today we talk about RAG and agents，以及评估。\n" * 40).encode("utf-8"),
    "markdown": "# 第一讲\n\n- 需求分析：用户要什么\n- 效果评估：准确率 92%\t耗时 3s\r\n".encode("utf-8"),
    # A few bytes broken in transit: read with replacement characters, as before.
    "a few stray bytes": ZH_TXT.encode("utf-8")[:300] + b"\xff\xfe\xff" + ZH_TXT.encode("utf-8")[300:],
}


@pytest.mark.parametrize("raw", ORDINARY.values(), ids=ORDINARY.keys())
def test_ordinary_files_are_not_taken_for_it(raw):
    assert not utf16_without_mark(raw)


# Review of 71b101f: the rule that took one character in ten a replacement or
# control character for UTF-16 refused files that had always loaded, and told
# a Latin-1 file it was UTF-16. UTF-16 is now told apart by reading the start
# of the file as UTF-16; none of these reads as ordinary text that way.

_SRT_ZH = "1\n00:00:01,000 --> 00:00:03,000\n今天我们讲人工智能\n\n2\n00:00:03,000 --> 00:00:05,000\n下面是需求分析\n\n"

LOADED_BEFORE = {
    "srt with a trailing NUL": _SRT_ZH.encode() + b"\x00",
    "srt with NUL padding": _SRT_ZH.encode() + b"\x00" * 4096,
    "short UTF-8 cut mid-character": "今天讲最小二乘法\n".encode()[:-2] + b"\n",
    "one bad byte in eight characters": b"hello\xffab",
    "a lossy earlier conversion": ("今天我们讲人工智能产品经�" * 40).encode(),
    "terminal colour codes": ("\x1b[32m[00:01]\x1b[0m 今天讲\n" * 30).encode(),
    "dense colour codes": ("\x1b[1;32mok\x1b[0m\n" * 30).encode(),
    "backspaces": ("abc\x08\x08\x08def 今天\n" * 20).encode(),
    "form feeds": ("第一页内容\x0c" * 30).encode(),
    "ASS tags": ("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an8}{\\c&H00FF00&}今天讲\\N下面\n" * 20).encode(),
    "VTT with a mark": "﻿WEBVTT\n\n00:01.000 --> 00:03.000\n今天\n".encode(),
    "cp1252 with accents": ("Caf\xe9 is nice. We talk about r\xe9sum\xe9s today.\n" * 20).encode("latin-1"),
    "Latin-1 French": ("\xe9t\xe9 \xe0 l'\xe9cole, d\xe9j\xe0 \xe9t\xe9\n" * 20).encode("latin-1"),
    "big5": ("今天我們講人工智慧產品經理的工作內容\n" * 20).encode("big5"),
    "shift-jis": ("今日は人工知能について話します\n" * 20).encode("shift_jis"),
    "english": ("The quick brown fox jumps over the lazy dog.\n" * 40).encode(),
    "eight ASCII bytes": b"hi there",
}


@pytest.mark.parametrize("raw", LOADED_BEFORE.values(), ids=LOADED_BEFORE.keys())
def test_files_that_loaded_before_are_not_taken_for_it(raw):
    assert not utf16_without_mark(raw)


def test_only_the_start_of_a_file_is_looked_at():
    # UTF-16 past the first 4 KB of an ordinary file does not count.
    raw = ZH_TXT.encode("utf-8")[:4096] + ZH_TXT.encode("utf-16-le") * 4
    assert not utf16_without_mark(raw)
