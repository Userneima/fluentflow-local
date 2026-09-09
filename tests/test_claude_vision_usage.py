"""What the API-key channel's notes cost, as the response reported it.

This channel reports tokens rather than dollars, and tokens are what gets kept:
a price list frozen into this repository would keep producing confident numbers
after Anthropic changed its prices. The client is injected, so nothing here needs
a credential or a network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.core.claude_vision as cv
from backend.core.claude_vision import FrameInput


class _Usage:
    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


class _Message:
    def __init__(self, text, usage=None, stop_reason="end_turn"):
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.usage = usage


class _Stream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get_final_message(self):
        return self._message


class _Client:
    """Just enough Anthropic client to answer one streamed request."""

    def __init__(self, message):
        outer = self

        class _Messages:
            def stream(self, **request):
                outer.request = request
                return _Stream(message)

        self.messages = _Messages()
        self.request: dict | None = None


@pytest.fixture(autouse=True)
def a_configured_key(monkeypatch):
    monkeypatch.setattr(cv, "anthropic", object())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_MODEL", raising=False)


@pytest.fixture()
def frames(tmp_path: Path):
    directory = tmp_path / "frames"
    directory.mkdir()
    path = directory / "note_0001.jpg"
    path.write_bytes(b"\xff\xd8\xff" + b"jpeg" * 4)
    return [FrameInput(filename=path.name, path=path, timestamp_seconds=12.0)]


def _answer(**usage_fields):
    body = json.dumps({"note_markdown": "# 笔记\n\n讲了最小二乘法\n", "basis_note": "看了一张"})
    return _Client(_Message(body, usage=_Usage(**usage_fields) if usage_fields else None))


def test_the_token_counts_are_kept(frames):
    client = _answer(
        input_tokens=41_233,
        output_tokens=6_118,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=1_750_000,
    )

    draft = cv.write_visual_note("[00:12] 今天讲最小二乘法", frames, client=client)

    assert draft.usage == {
        "input_tokens": 41_233,
        "output_tokens": 6_118,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1_750_000,
    }


def test_no_dollar_figure_is_invented_from_them(frames):
    draft = cv.write_visual_note("[00:12] 讲课", frames, client=_answer(input_tokens=10, output_tokens=2))

    assert "total_cost_usd" not in draft.usage, (
        "prices change on Anthropic's side; this repository does not get to guess them"
    )


def test_a_response_that_reports_no_usage_still_returns_the_note(frames):
    draft = cv.write_visual_note("[00:12] 讲课", frames, client=_answer())

    assert draft.markdown.startswith("# 笔记")
    assert draft.usage == {}


def test_a_non_integer_count_is_left_out_rather_than_coerced(frames):
    draft = cv.write_visual_note(
        "[00:12] 讲课", frames, client=_answer(input_tokens="41233", output_tokens=6_118)
    )

    assert draft.usage == {"output_tokens": 6_118}


def test_a_fixed_budget_is_spread_over_the_whole_recording():
    """The regression that arrives the moment the frame pass keeps everything.

    This channel attaches its pictures and cannot go back for more, so it takes a
    fixed number out of whatever it was offered. While that offer was already
    exactly twenty, a prefix was the same as a spread. Once the offer became every
    distinct frame in the recording, a prefix became the opening fifth of the
    lecture — and the note would describe it with nothing to show that the rest
    was never looked at.
    """
    from pathlib import Path as _Path

    from backend.core.claude_vision import FrameInput, spread_across

    frames = [
        FrameInput(filename=f"n{index:03d}.jpg", path=_Path(f"/f/n{index:03d}.jpg"),
                   timestamp_seconds=index * 60.0)
        for index in range(100)
    ]

    picked = spread_across(frames, 20)

    assert len(picked) == 20
    assert picked[0] is frames[0] and picked[-1] is frames[-1], "both ends of the recording"
    gaps = {round((b.timestamp_seconds - a.timestamp_seconds) / 60) for a, b in zip(picked, picked[1:])}
    assert gaps <= {5, 6}, "evenly, not clustered"
    assert spread_across(frames, 200) == frames, "fewer frames than budget is everything"
    assert spread_across([], 20) == [] and spread_across(frames, 0) == []
