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
