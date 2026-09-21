"""What happens to a lecture that is longer than one request can hold.

The overflow used to be dropped in silence: the writer took the first 60,000
characters, sent them, and nothing anywhere recorded that the rest existed. No
error, no warning, nothing on the note. A 5.7-hour recording came back as a note
that reads like a finished one and covers the first half — which is worse than a
failure, because a failure is visible and this is not.

So the guard here is not that the limit moved. It has not: what fills the
request is the pictures, and moving the ceiling is a measurement rather than an
edit. The guard is that going over it is now said out loud, in the free preview
where the reader can still act on it, and on the finished note where somebody
reading it a week later has no preview to go back to.
"""

from __future__ import annotations

import backend.core.claude_vision as cv


def _talk(lines: int, words: str = "这是一句讲课内容" * 4) -> str:
    return "\n".join(f"[{index // 60:02d}:{index % 60:02d}] {words}" for index in range(lines))


def test_a_recording_that_fits_goes_out_whole() -> None:
    text = _talk(10)

    fitted = cv.transcript_for_request(text)

    assert fitted.text == text
    assert fitted.chars_dropped == 0
    # Nothing to point at, so nothing is claimed.
    assert fitted.covered_until == ""


def test_what_did_not_fit_is_counted_rather_than_dropped_quietly() -> None:
    text = _talk(4000)
    assert len(text) > cv.MAX_TRANSCRIPT_CHARS, "the fixture has to overflow to test this"

    fitted = cv.transcript_for_request(text)

    assert fitted.chars_sent <= cv.MAX_TRANSCRIPT_CHARS
    assert fitted.chars_sent + fitted.chars_dropped == len(text)
    assert fitted.chars_dropped > 0


def test_it_stops_at_a_line_rather_than_mid_sentence() -> None:
    """Half a sentence is not something to hand a model, and the line carries the
    timestamp that makes the cut point reportable at all."""
    fitted = cv.transcript_for_request(_talk(4000))

    assert not fitted.text.endswith(" ")
    assert fitted.text.splitlines()[-1].startswith("[")
    # Every line that made it is a whole one.
    assert all(line.startswith("[") for line in fitted.text.splitlines())


def test_it_says_where_the_note_stops_covering() -> None:
    """The character count says something was lost; the timestamp says where to
    split the recording, which is the only thing the reader can act on."""
    fitted = cv.transcript_for_request(_talk(4000))

    assert fitted.covered_until
    assert fitted.text.splitlines()[-1].startswith(f"[{fitted.covered_until}]")


def test_an_hours_long_recording_keeps_its_own_timestamp_format() -> None:
    """Minutes run past 99 on a long lecture, which is exactly the case here."""
    text = "\n".join(f"[{index}:00] {'讲课' * 200}" for index in range(400))

    fitted = cv.transcript_for_request(text)

    assert fitted.chars_dropped > 0
    assert ":" in fitted.covered_until
    assert int(fitted.covered_until.split(":")[0]) > 99
