"""A subtitle file is read in the encoding it was saved in.

Found 2026-11-25 (push review of subtitle files by path): a GB18030 .srt with
an even number of bytes was decoded as UTF-16, which accepts almost any even
run of bytes, and came out as garbage with no timed line; the page's subtitle
upload read it the same way.
"""

from __future__ import annotations

import pytest

from backend.core.transcript_parser import parse_transcript_file

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
