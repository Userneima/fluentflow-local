"""Feedback the user gives on a note, and the rules their agent writes from it.

From the requirement:

- A reported problem is kept as a test case; a writing problem must say how a
  checker tells it is fixed, and the stretch of transcript it came from is frozen.
- A rule goes into the user's skill only with verification behind it and
  feedback it answers; the skill it replaces stays restorable.
- The settings page can say where each rule came from.
"""

from __future__ import annotations

import pytest

import backend.core.note_feedback as nf
from backend.core import note_skills
from test_visual_note_job import TASK, job_store, no_ambient_key  # noqa: F401


@pytest.fixture(autouse=True)
def skill_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_NOTE_SKILL_DIR", str(tmp_path / "mine"))
    return tmp_path / "mine"


def test_a_writing_problem_needs_a_pass_criterion():
    with pytest.raises(nf.NoteFeedbackError, match="criterion"):
        nf.record_feedback(task_id="", problem="每节都加了总结段", kind="pattern")


def test_a_one_off_error_is_kept_without_one():
    item = nf.record_feedback(task_id="", problem="讲者名字写错了", kind="one_off")

    assert nf.list_feedback()[0]["id"] == item["id"]
    assert item["rule_id"] is None


def test_the_transcript_the_problem_came_from_is_frozen(job_store):  # noqa: F811
    item = nf.record_feedback(
        task_id=TASK, problem="「往往」写成了「只」", kind="pattern", criterion="限定词与原话一致",
    )

    assert "[00:55] 看这张幻灯片上的公式" in item["transcript_excerpt"]


def test_a_rule_needs_verification_and_the_feedback_it_answers():
    fid = nf.record_feedback(task_id="", problem="结尾有总结段", kind="pattern", criterion="结尾没有总结段")["id"]

    with pytest.raises(nf.NoteFeedbackError, match="rounds"):
        nf.apply_rule(skill_text="新写法", rule="结尾不写总结", feedback_ids=[fid], rounds=0, evidence="")
    with pytest.raises(nf.NoteFeedbackError, match="找不到"):
        nf.apply_rule(skill_text="新写法", rule="结尾不写总结", feedback_ids=["nope"], rounds=3, evidence="")
    assert note_skills.load_note_skill().is_default, "nothing was written"


def test_applying_a_rule_keeps_the_old_skill_and_says_where_the_rule_came_from():
    fid = nf.record_feedback(task_id="", problem="结尾有总结段", kind="pattern", criterion="结尾没有总结段")["id"]
    default_version = note_skills.load_note_skill().version

    applied = nf.apply_rule(
        skill_text="# 我的写法\n\n- 结尾不写总结段。", rule="结尾不写总结段",
        feedback_ids=[fid], rounds=3, evidence="同一段重写三次，检查者三次都判没有总结段",
    )

    skill = note_skills.load_note_skill()
    assert not skill.is_default and "结尾不写总结段" in skill.rules
    assert note_skills.skill_history(), "the replaced skill is kept"
    log = nf.rules_log()
    assert log[0]["rule"] == "结尾不写总结段"
    assert log[0]["skill_version_before"] == default_version
    assert log[0]["feedback"][0]["problem"] == "结尾有总结段"
    assert nf.list_feedback()[0]["rule_id"] == applied["rule"]["id"]


def test_mcp_exposes_the_feedback_tools():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import fluentflow_mcp_server as mcp

    names = {tool["name"] for tool in mcp.TOOL_DEFINITIONS}
    assert {"get_note_skill", "record_note_feedback", "list_note_feedback", "draft_note_part", "apply_note_rule"} <= names


def test_the_shipped_agent_skill_is_named_like_its_folder():
    """Hosts other than Claude Code drop a skill whose name is not kebab-case or not its folder name."""
    import re
    from pathlib import Path

    skill = Path(__file__).resolve().parents[1] / "agent" / "skills" / "fluentflow" / "SKILL.md"
    name = re.search(r"^name:\s*(\S+)", skill.read_text(encoding="utf-8"), re.M).group(1)
    assert name == skill.parent.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)


def test_list_tasks_finds_a_note_by_the_title_shown_in_a_screenshot(monkeypatch):
    """A screenshot shows the note's own heading, which is often not the file name."""
    from types import SimpleNamespace

    import backend.routers.local_agent as la

    jobs = [
        {"task_id": "t1", "status": "completed", "created_at": "2026-09-20", "source_filename": "2.1 什么是科学.mp4",
         "result": {"display_title": "2.1 什么是科学", "summary_markdown": "# 科学与科学研究的结构：什么是科学\n\n正文"}},
        {"task_id": "t2", "status": "completed", "created_at": "2026-09-21", "source_filename": "b.mp4",
         "result": {"display_title": "别的课", "summary_markdown": ""}},
    ]
    monkeypatch.setattr(la, "list_jobs", lambda **_: jobs)
    monkeypatch.setattr(la, "_local_client_scope", lambda request: "anonymous")

    found = la.list_agent_tasks(SimpleNamespace(), query="科学研究的结构")["tasks"]
    assert [t["task_id"] for t in found] == ["t1"]
    assert found[0]["note_title"] == "科学与科学研究的结构：什么是科学"
    assert len(la.list_agent_tasks(SimpleNamespace(), query="")["tasks"]) == 2
