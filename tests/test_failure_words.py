"""Every failure the user sees says what happened and what to do, in words.

Requirement (owner, 2026-10-18, after reading every failure message on record):
internal codes (``user_cancelled``, ``client_disconnect``), raw system errors
(``[Errno 1] Operation not permitted``) and the export tool's own output
(``render batch failed … hevc_videotoolbox``) reached the user as they were.
Each now comes out in Chinese with a next step; the raw text is kept for
whoever investigates.
"""

from __future__ import annotations

import re

import pytest

from backend.core import debreath_job
from backend.core.local_error_diagnostics import diagnose_error

RAW = [
    "user_cancelled",
    "client_disconnect",
    "[Errno 1] Operation not permitted: '/Users/x/Downloads/a.mp4'",
    "render batch failed:  frame: Generic error in an external library "
    "[vost#0:0/hevc_videotoolbox @ 0x77a2ca4000] Task finished with error code: -542398533",
]
_CJK = re.compile(r"[一-鿿]")


@pytest.mark.parametrize("raw", RAW)
def test_a_raw_failure_comes_out_in_words_with_a_next_step(raw):
    found = diagnose_error(raw)
    assert _CJK.search(found["detail"]) and _CJK.search(found["next_action"])
    for leftover in ("Errno", "render batch", "vost#", "_cancelled", "client_disconnect"):
        assert leftover not in found["detail"] + found["next_action"], leftover


def test_the_permission_message_names_the_file():
    assert "/Users/x/Downloads/a.mp4" in diagnose_error(RAW[2])["detail"]


def test_a_failed_export_is_stored_in_words_with_the_raw_text_kept():
    words = debreath_job._user_words(RuntimeError(RAW[3]))
    assert "原片没有动" in words and "硬件编码器" in words and "render batch" not in words
    other = RuntimeError("something new")
    assert debreath_job._user_words(other) == "something new", "unknown errors pass through unchanged"
