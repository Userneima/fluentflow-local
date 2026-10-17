"""Every note is checked for knowledge points it never mentions, whoever wrote it.

What has to hold, written from the requirement rather than the code:

- A point counts as covered only when the finished note says it. Being handed to
  a chapter whose writer then left it out is a miss.
- What the check finds missing is written back into the note by the writer that
  wrote it (the text model, or Claude for the frame note).
- The user's own agent gets the missing points back when it saves, by part, and
  its note is saved either way.
- A check that could not run, or could not finish, never reads as a pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.core.agent_note as an
import backend.core.ai_summarizer as summ
import backend.core.visual_note_job as vn
from backend.core import note_coverage, note_skills
from backend.core.ai_prompts import _CHAPTER_EVIDENCE_SYSTEM, _CHAPTER_NOTE_SYSTEM, _CHAPTER_OUTLINE_SYSTEM
from backend.core.claude_vision import VisualNoteDraft
from test_visual_note_job import TASK, _fake_extractor, job_store, no_ambient_key  # noqa: F401


def _points_reply(*points: tuple[str, bool]) -> str:
    return json.dumps({"points": [
        {"point": text, "in_note": found, **({} if found else {"source": f"原稿讲了{text}"})}
        for text, found in points
    ]}, ensure_ascii=False)


# ── the check itself ────────────────────────────────────────────────────────

def test_points_the_note_never_says_are_reported_with_what_the_transcript_said():
    chat = lambda system, user: _points_reply(("最小二乘法的定义", True), ("残差平方和最小", False))  # noqa: E731

    check = note_coverage.check_against_transcript(chat, "转录", "# 笔记\n\n最小二乘法是……")

    assert check.checked is True
    assert check.point_count == 2
    assert [item["point"] for item in check.missing] == ["残差平方和最小"]
    assert "残差平方和最小" in check.missing[0]["source"]


def test_a_long_transcript_is_checked_piece_by_piece_with_the_whole_note_each_time():
    seen: list[str] = []

    def chat(system, user):
        seen.append(user)
        return _points_reply(("一点", True))

    transcript = "\n".join(f"第 {i} 行讲了一件事。" for i in range(400))
    note = "# 笔记\n\n整篇笔记的正文。"
    check = note_coverage.check_against_transcript(chat, transcript, note, chunk_chars=1000)

    assert len(seen) > 1
    assert all(note in user for user in seen), "every piece is compared with the whole note"
    assert any("第 0 行" in user for user in seen) and any("第 399 行" in user for user in seen), "every piece is asked, in any order"
    assert check.point_count == len(seen)


def test_an_answer_that_is_not_json_twice_is_not_a_pass():
    check = note_coverage.check_against_transcript(lambda s, u: "看起来都写到了", "转录", "# 笔记")

    assert check.checked is False
    assert check.failed_requests == 1


def test_a_checker_that_errors_is_not_a_pass():
    def boom(system, user):
        raise RuntimeError("rate limited")

    assert note_coverage.check_against_transcript(boom, "转录", "# 笔记").checked is False


def test_a_checklist_item_whose_request_failed_is_neither_found_nor_missing():
    calls = {"n": 0}

    def chat(system, user):
        calls["n"] += 1
        return '{"missing_ids": ["E002"]}' if calls["n"] == 1 else "不是 JSON"

    points = [{"id": f"E{i:03d}", "text": "一条证据" * 10} for i in range(1, 7)]
    check = note_coverage.check_against_points(chat, points, "# 笔记", batch_chars=150)

    assert [item["id"] for item in check.missing] == ["E002"]
    assert check.unchecked_ids, "the failed batch is recorded as unchecked"
    assert check.failed_requests >= 1


def test_missing_is_judged_by_the_rules_the_note_was_written_under():
    """A point the writing rules say to leave out is not a gap: the check is
    handed those rules, and never counts course logistics or chat as knowledge."""
    seen: list[str] = []

    def chat(system, user):
        seen.append(system)
        return _points_reply(("一点", True))

    note_coverage.check_against_transcript(chat, "转录", "# 笔记", rules="讲者没讨论的文档内容留给读者看原文。")
    note_coverage.check_against_points(chat, [{"id": "E001", "text": "一点"}], "# 笔记", rules="只写结论。")

    assert "讲者没讨论的文档内容留给读者看原文。" in seen[0]
    assert "只写结论。" in seen[1]
    assert all("考勤作业" in system for system in seen)


def test_the_revision_is_told_to_weave_points_in_and_not_repeat_them():
    request = note_coverage.revision_request("# 笔记", [{"point": "公式", "source": "原稿"}])

    assert "和前后文接上" in request
    assert "已经写过" in request and "不再写" in request
    assert "只改说法，不改意思" in request, "polishing the whole note is asked for, within the facts"


def test_a_revision_that_shrinks_the_note_is_not_used():
    note = "x" * 1000
    assert note_coverage.accept_revision(note, "y" * 500) is None
    assert note_coverage.accept_revision(note, note + "补上的一段") == note + "补上的一段"


# ── the text model's notes ──────────────────────────────────────────────────

@pytest.fixture()
def no_network(monkeypatch):
    monkeypatch.setattr(summ, "_get_client", lambda **_k: object())
    monkeypatch.setattr(summ, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("FLUENTFLOW_NOTE_MODE", raising=False)


def test_a_short_note_that_missed_a_point_gets_it_written_back_in(no_network, monkeypatch):
    draft = "# 最小二乘法\n\n## 一、定义\n\n" + "最小二乘法是一种拟合方法。\n" * 20
    asked: list[str] = []

    def chat(client, model, system, user, *, temperature=0.3):
        if system.startswith(note_coverage._CHECK_TRANSCRIPT_SYSTEM):
            return _points_reply(("定义", True), ("前提条件：误差独立同分布", False))
        if "没写到的知识点" in user:
            asked.append(user)
            return draft + "\n## 二、前提\n\n误差独立同分布。\n"
        return draft

    monkeypatch.setattr(summ, "_chat", chat)
    result = summ.summarize_transcript_with_metadata("讲座。" * 100, provider="deepseek")

    assert result.resolved_mode == "direct"
    assert "误差独立同分布" in result.markdown
    assert "前提条件：误差独立同分布" in asked[0], "the revision is told exactly what is missing"
    assert result.coverage_missing_count == 1
    assert result.coverage == {
        "checked": True, "point_count": 2, "missing_count": 1,
        "missing_points": ["前提条件：误差独立同分布"], "failed_requests": 0, "revised": True,
    }


def test_a_chapter_note_counts_a_point_only_when_the_final_text_says_it(no_network, monkeypatch):
    """E002 is assigned to the chapter, the chapter's writer leaves it out: that
    is a miss, and it is written back in."""
    checked_note: list[str] = []
    revised_with: list[str] = []
    chapter = "## 一、开场\n\n" + "这一章讲了最小二乘法的定义。\n" * 10

    def chat(client, model, system, user, *, temperature=0.3):
        if system.startswith(_CHAPTER_EVIDENCE_SYSTEM[:40]):
            seg = json.loads(user)[0]["segment_id"]
            if seg != "S001":
                return "[]"
            return json.dumps([
                {"text": "最小二乘法的定义", "source_segment_ids": [seg], "importance": 5},
                {"text": "前提是误差独立同分布", "source_segment_ids": [seg], "importance": 5},
            ], ensure_ascii=False)
        if system == _CHAPTER_OUTLINE_SYSTEM:
            return json.dumps([{"title": "开场", "used_evidence_ids": ["E001", "E002"]}])
        if system.startswith(_CHAPTER_NOTE_SYSTEM[:40]):
            return chapter
        if system.startswith(note_coverage._CHECK_POINTS_SYSTEM):
            checked_note.append(user)
            return '{"missing_ids": ["E002"]}'
        if "没写到的知识点" in user:
            revised_with.append(user)
            return chapter + "\n前提是误差独立同分布。\n"
        return chapter  # style pass

    monkeypatch.setattr(summ, "_chat", chat)
    result = summ.summarize_transcript_with_metadata(
        "讲座内容。" * 5000, note_mode="chapter_coverage", provider="deepseek",
    )

    assert chapter.strip() in checked_note[0], "the check reads the note as written, after the style pass"
    assert "前提是误差独立同分布" in revised_with[0]
    assert "前提是误差独立同分布" in result.markdown
    assert result.important_evidence_count == 2
    assert result.covered_important_evidence_count == 1
    assert result.coverage_missing_count == 1
    by_id = {item["evidence_id"]: item for item in result.chapter_coverage["evidence"]}
    assert by_id["E002"]["covered"] is False
    assert by_id["E002"]["covered_by_chapter_ids"] == ["CH01"], "the plan still says where it was assigned"


def test_a_chapter_note_whose_check_failed_reports_no_coverage_number(no_network, monkeypatch):
    def chat(client, model, system, user, *, temperature=0.3):
        if system.startswith(_CHAPTER_EVIDENCE_SYSTEM[:40]):
            seg = json.loads(user)[0]["segment_id"]
            return json.dumps([{"text": "定义", "source_segment_ids": [seg], "importance": 5}])
        if system == _CHAPTER_OUTLINE_SYSTEM:
            return json.dumps([{"title": "开场", "used_evidence_ids": ["E001"]}])
        if system.startswith(note_coverage._CHECK_POINTS_SYSTEM):
            return "出错了"
        return "## 一、开场\n\n" + "正文。\n" * 50

    monkeypatch.setattr(summ, "_chat", chat)
    result = summ.summarize_transcript_with_metadata("讲座内容。" * 5000, note_mode="chapter_coverage", provider="deepseek")

    assert result.coverage_checked is False
    assert result.covered_important_evidence_count is None
    assert result.coverage_missing_count is None


# ── Claude's frame note ─────────────────────────────────────────────────────

NOTE = "# 最小二乘法\n\n## 一、定义\n\n" + "最小二乘法是一种拟合方法。\n" * 20


def _writer():
    def write(transcript, frames, **kwargs):
        return VisualNoteDraft(markdown=NOTE, basis_note="", model="claude-opus-5", frames_sent=list(frames),
                               transcript_chars=len(transcript))
    return write


def _run(tmp_path, **kwargs):
    return vn.run_visual_note(
        TASK, client_id="anonymous", api_key="sk-ant-test-not-a-real-key",
        keyframe_extractor=_fake_extractor(tmp_path), writer=_writer(), **kwargs,
    )


def test_a_frame_note_that_missed_a_point_is_completed_by_claude_before_it_is_saved(job_store, tmp_path):
    checked: list[str] = []
    revisions: list[tuple[str, str]] = []

    checked_systems: list[str] = []

    def checker(system, user):
        checked.append(user)
        checked_systems.append(system)
        return _points_reply(("定义", True), ("幻灯片上的公式", False))

    def reviser(system, user):
        revisions.append((system, user))
        return NOTE + "\n## 二、公式\n\n幻灯片上的公式是残差平方和。\n"

    updated = _run(tmp_path, checker=(checker, "text_model"), reviser=reviser,
                   note_skill=note_skills.NoteSkill(
                       text="---\nname: mine\n---\n我的写法：只写结论。\n", path=Path("SKILL.md"), is_default=False))

    result = updated["result"]
    assert "幻灯片上的公式是残差平方和" in result["summary_markdown"]
    assert "看这张幻灯片上的公式" in checked[0], "checked against the transcript the note was written from"
    assert "我的写法：只写结论。" in checked_systems[0], "judged by the skill the note was written under"
    system, user = revisions[0]
    assert "幻灯片上的公式" in user
    assert "固定要求" not in system, "the revision is plain markdown, not the JSON contract"
    coverage = vn.visual_note_state(result)["coverage"]
    assert coverage["checker"] == "text_model"
    assert coverage["parts"][0]["missing_points"] == ["幻灯片上的公式"]
    assert coverage["parts"][0]["revised"] is True


def test_a_revision_that_would_lose_the_note_is_dropped_and_the_note_stands(job_store, tmp_path):
    updated = _run(
        tmp_path,
        checker=(lambda s, u: _points_reply(("公式", False)), "text_model"),
        reviser=lambda s, u: "好的。",
    )

    assert updated["result"]["summary_markdown"].startswith("# 最小二乘法")
    assert vn.visual_note_state(updated["result"])["coverage"]["parts"][0]["revised"] is False


def test_with_nothing_to_check_with_the_note_is_saved_and_says_it_was_not_checked(job_store, tmp_path):
    updated = _run(tmp_path)

    part = vn.visual_note_state(updated["result"])["coverage"]["parts"][0]
    assert part["checked"] is False and part["reason"]


@pytest.mark.real_coverage_checker
def test_the_check_prefers_the_text_model_and_falls_back_to_claude(monkeypatch):
    from backend.core import ai_summarizer, claude_text

    text = lambda s, u: "text"  # noqa: E731
    claude = (lambda s, u: "claude", "anthropic_api_key")
    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **_k: text)
    monkeypatch.setattr(ai_summarizer, "text_model_name", lambda **_k: "deepseek/deepseek-flash")
    monkeypatch.setattr(claude_text, "claude_chat", lambda *_a, **_k: claude)
    # Named down to the model (2026-10-16): an agent told its note was checked
    # could not say which model checked it, so nobody knew what it cost.
    assert vn.coverage_checker() == (text, "text_model:deepseek/deepseek-flash")

    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **_k: None)
    assert vn.coverage_checker() == claude


# ── the user's own agent ────────────────────────────────────────────────────

AGENT_NOTE = "# 最小二乘法\n\n正文写了定义。"


def test_the_agent_is_told_to_check_its_note_before_it_hands_it_back(job_store, tmp_path):
    material = an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))

    assert "逐条查一遍" in material["instructions"]
    assert "coverage.missing" in material["instructions"]


def test_saving_returns_what_the_note_never_says_and_saves_it_anyway(job_store, tmp_path):
    an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))
    checker = (lambda s, u: _points_reply(("定义", True), ("幻灯片上的公式", False)), "text_model")

    updated = an.save(TASK, note_markdown=AGENT_NOTE, client_id="anonymous", checker=checker)

    assert updated["result"]["summary_markdown"].startswith("# 最小二乘法"), "saved even with a gap"
    coverage = vn.visual_note_state(updated["result"])["coverage"]
    assert [(item["part"], item["point"]) for item in coverage["missing"]] == [(1, "幻灯片上的公式")]
    assert coverage["missing"][0]["source"]


def test_the_save_route_hands_the_missing_points_back(job_store, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.local_main import create_local_app

    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "local-secret-token")
    an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))
    monkeypatch.setattr(vn, "coverage_checker", lambda *_a, **_k: (
        lambda s, u: _points_reply(("幻灯片上的公式", False)), "text_model:deepseek/deepseek-flash",
    ))

    response = TestClient(create_local_app()).post(
        f"/agent/v1/tasks/{TASK}/agent-note", json={"note_markdown": AGENT_NOTE},
        headers={"x-fluentflow-access-token": "local-secret-token"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coverage"]["checked"] is True
    assert body["coverage"]["checked_by"] == "text_model:deepseek/deepseek-flash"
    assert [item["point"] for item in body["coverage"]["missing"]] == ["幻灯片上的公式"]


def test_with_nothing_to_check_with_the_agent_is_told_the_note_was_not_checked(job_store, tmp_path):
    an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))

    updated = an.save(TASK, note_markdown=AGENT_NOTE, client_id="anonymous", checker=None)

    coverage = vn.visual_note_state(updated["result"])["coverage"]
    assert coverage["missing"] == []
    assert coverage["parts"][0]["checked"] is False


def test_a_request_that_never_finishes_does_not_hold_the_note(monkeypatch):
    """Requirement (2026-10-15): a provider that keeps a coverage request open
    without ever answering (DeepSeek sends blank lines while it queues) must not
    keep the note in "checking". Past the deadline the check counts as not run
    and the note is saved as written."""
    import threading
    import time

    from backend.core import note_coverage as nc

    monkeypatch.setattr(nc, "REQUEST_DEADLINE_SECONDS", 0.2)
    never = threading.Event()

    def stuck(system, user):
        never.wait(30)
        return "{}"

    started = time.monotonic()
    check = nc.check_against_transcript(stuck, "第一句。\n第二句。", "# 笔记\n\n第一句。")
    elapsed = time.monotonic() - started
    never.set()

    assert elapsed < 5, "the note is not held for the request"
    assert check.checked is False and check.failed_requests == 1
