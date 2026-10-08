"""What the note pipeline promises when a stage misbehaves.

A revision step that answers with nothing must not replace the draft with
nothing; a note must stop at the time limit instead of holding the queue; and
when the screenshot planner is shown only part of a long transcript, the
result has to say so.
"""

from __future__ import annotations

import pytest

import backend.core.ai_client as ai_client
import backend.core.ai_summarizer as summ
from backend.core.ai_prompts import _COVERAGE_SYSTEM, _EVIDENCE_SYSTEM

DRAFT = "## 一、开场\n\n" + "这一段是初稿里的正文，讲了课程要解决的问题。\n" * 40
TRANSCRIPT = "讲座内容。" * 400


class _FakeChat:
    """Stands in for ``_chat``: answers by which stage is asking."""

    def __init__(self, revision_reply: str) -> None:
        self.revision_reply = revision_reply
        self.stages: list[str] = []

    def __call__(self, client, model, system, user, *, temperature=0.3):
        if system == _EVIDENCE_SYSTEM:
            self.stages.append("evidence")
            return "- 证据 1：课程要解决的问题"
        if system == _COVERAGE_SYSTEM:
            self.stages.append("coverage")
            return "缺少：结尾的总结"
        if "需要补入的遗漏点" in user:
            self.stages.append("revision")
            return self.revision_reply
        self.stages.append("draft")
        return DRAFT


@pytest.fixture()
def no_network(monkeypatch):
    monkeypatch.setattr(summ, "_get_client", lambda **_k: object())
    monkeypatch.delenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("FLUENTFLOW_NOTE_MODE", raising=False)


@pytest.mark.parametrize("reply", ["", "   ", "好的。"])
def test_a_revision_that_comes_back_empty_or_tiny_keeps_the_draft(no_network, monkeypatch, reply):
    chat = _FakeChat(revision_reply=reply)
    monkeypatch.setattr(summ, "_chat", chat)

    result = summ.summarize_transcript_with_metadata(TRANSCRIPT, note_mode="high_fidelity", provider="deepseek")

    assert "revision" in chat.stages, "the revision was attempted"
    assert result.markdown == DRAFT.strip() or result.markdown == DRAFT
    assert result.coverage_revision_used is True


def test_a_real_revision_is_used(no_network, monkeypatch):
    revised = DRAFT + "\n## 二、结尾\n\n补上了总结。\n"
    monkeypatch.setattr(summ, "_chat", _FakeChat(revision_reply=revised))

    result = summ.summarize_transcript_with_metadata(TRANSCRIPT, note_mode="high_fidelity", provider="deepseek")

    assert "补上了总结" in result.markdown


def test_the_guard_itself_draws_the_line_at_a_fraction_of_the_draft():
    draft = "x" * 1000
    assert summ._accept_revision(draft, "", stage="t") == draft
    assert summ._accept_revision(draft, "y" * 399, stage="t") == draft
    assert summ._accept_revision(draft, "y" * 400, stage="t") == "y" * 400


# ── the whole note has a time limit ─────────────────────────────────────────

def test_a_note_that_runs_past_the_limit_stops_between_stages(no_network, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", "0")
    chat = _FakeChat(revision_reply=DRAFT)
    monkeypatch.setattr(summ, "_chat", chat)

    with pytest.raises(summ.NoteDeadlineExceeded, match="超过了时间上限"):
        summ.summarize_transcript_with_metadata(TRANSCRIPT, note_mode="high_fidelity", provider="deepseek")

    assert chat.stages == ["evidence"], "the stage in flight finishes; the next one does not start"


def test_the_limit_defaults_to_an_hour_and_ignores_nonsense(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", raising=False)
    assert summ.note_deadline_seconds() == 3600.0
    monkeypatch.setenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", "ninety")
    assert summ.note_deadline_seconds() == 3600.0
    monkeypatch.setenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", "90")
    assert summ.note_deadline_seconds() == 90.0


# ── the screenshot planner says how far it read ─────────────────────────────

def _segments(count: int) -> list[dict]:
    return [{"start": i * 10.0, "end": i * 10.0 + 9.0, "text": "这一段讲了一个要点" * 10} for i in range(count)]


def test_a_transcript_that_fits_is_not_reported_as_cut():
    compacted = summ._compact_timestamped_segments(_segments(5))
    assert compacted.truncated is False and len(compacted.items) == 5
    assert compacted.covered_until_seconds == 49.0


def test_a_transcript_that_does_not_fit_says_where_the_planner_stopped_reading():
    compacted = summ._compact_timestamped_segments(_segments(100), max_chars=1_000)
    assert compacted.truncated is True
    assert 0 < len(compacted.items) < 100
    assert compacted.covered_until_seconds == compacted.items[-1]["end_seconds"]


def test_the_plan_result_carries_the_coverage_so_the_task_record_can_show_it(no_network, monkeypatch):
    monkeypatch.setattr(summ, "_chat", lambda *a, **k: '{"requests": []}')
    segments = _segments(3000)  # ~500k chars of transcript, far past the 45k budget

    result = summ.plan_visual_evidence_requests("## 笔记\n\n正文", segments, provider="deepseek")

    assert result.transcript_truncated is True
    assert result.covered_until_seconds is not None
    assert result.covered_until_seconds < segments[-1]["end"]


def test_a_short_transcript_reports_no_truncation(no_network, monkeypatch):
    monkeypatch.setattr(summ, "_chat", lambda *a, **k: '{"requests": []}')
    result = summ.plan_visual_evidence_requests("## 笔记\n\n正文", _segments(3), provider="deepseek")
    assert result.transcript_truncated is False and result.covered_until_seconds is None


# ── the client honours the model the user chose, and does not wait forever ──

def test_the_model_the_user_picked_is_the_model_called():
    assert ai_client._normalize_model("deepseek", "deepseek-chat") == "deepseek-chat"
    assert ai_client._normalize_model("deepseek", "deepseek-reasoner") == "deepseek-reasoner"
    assert ai_client._normalize_model("deepseek", "") == ai_client._provider_default_model("deepseek")


def test_every_request_has_a_timeout_and_one_retry(monkeypatch):
    seen: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(ai_client, "OpenAI", _Recorder)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    ai_client._get_client(provider="deepseek")

    assert seen["max_retries"] == 1
    assert seen["timeout"].connect == 10.0 and seen["timeout"].read == 300.0


# ── past the limit with a draft in hand, the draft is the note ──────────────

class _Clock:
    """A clock the fake model moves: the draft stage is what takes the time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def test_a_note_that_runs_out_of_time_after_its_draft_keeps_the_draft(no_network, monkeypatch):
    """Requirement: when the time limit passes after the draft is written, the
    user gets that draft as the note, the optional polishing stages are
    skipped, and the record says the limit was hit."""
    import types

    clock = _Clock()
    monkeypatch.setattr(summ, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", "60")
    chat = _FakeChat(revision_reply=DRAFT + "\n补上了总结。")

    def slow_draft(client, model, system, user, *, temperature=0.3):
        reply = chat(client, model, system, user, temperature=temperature)
        if chat.stages[-1] == "draft":
            clock.now += 120
        return reply

    monkeypatch.setattr(summ, "_chat", slow_draft)

    result = summ.summarize_transcript_with_metadata(TRANSCRIPT, note_mode="high_fidelity", provider="deepseek")

    assert result.markdown.strip() == DRAFT.strip()
    assert result.deadline_hit is True
    assert "coverage" not in chat.stages and "revision" not in chat.stages
    assert result.coverage_revision_used is False


def test_a_chapter_note_that_runs_out_of_time_keeps_its_chapters(no_network, monkeypatch):
    """Requirement: same promise for the long-recording mode: the chapters
    already written are the note; style and coverage passes are skipped."""
    import json
    import types

    from backend.core.ai_prompts import (
        _CHAPTER_EVIDENCE_SYSTEM,
        _CHAPTER_NOTE_SYSTEM,
        _CHAPTER_OUTLINE_SYSTEM,
    )

    clock = _Clock()
    monkeypatch.setattr(summ, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", "60")
    stages: list[str] = []
    chapter = "## 一、开场\n\n这一章讲了课程要解决的问题。"

    def chat(client, model, system, user, *, temperature=0.3):
        if system.startswith(_CHAPTER_EVIDENCE_SYSTEM[:40]):
            stages.append("evidence")
            seg = json.loads(user)[0]["segment_id"]
            return json.dumps([{"text": "问题", "source_segment_ids": [seg], "importance": 5}])
        if system == _CHAPTER_OUTLINE_SYSTEM:
            stages.append("outline")
            return json.dumps([{"title": "开场", "used_evidence_ids": ["E001"]}])
        if system.startswith(_CHAPTER_NOTE_SYSTEM[:40]):
            stages.append("chapter")
            clock.now += 120
            return chapter
        stages.append("other")
        return "COVERED"

    monkeypatch.setattr(summ, "_chat", chat)

    result = summ.summarize_transcript_with_metadata(TRANSCRIPT, note_mode="chapter_coverage", provider="deepseek")

    assert "问题" in result.markdown
    assert result.deadline_hit is True
    assert "other" not in stages, "style and coverage passes did not run"


def test_a_note_that_runs_out_of_time_before_any_draft_still_fails(no_network, monkeypatch):
    """Requirement: with nothing written yet there is nothing to keep, so the
    user is told the limit was hit."""
    import types

    clock = _Clock()
    monkeypatch.setattr(summ, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", "60")
    chat = _FakeChat(revision_reply=DRAFT)

    def empty_draft(client, model, system, user, *, temperature=0.3):
        reply = chat(client, model, system, user, temperature=temperature)
        if chat.stages[-1] == "draft":
            clock.now += 120
            return ""
        return reply

    monkeypatch.setattr(summ, "_chat", empty_draft)

    with pytest.raises(summ.NoteDeadlineExceeded):
        summ.summarize_transcript_with_metadata(TRANSCRIPT, note_mode="high_fidelity", provider="deepseek")
