"""A text model writes notes by the same rules as Claude and the agent.

Requirements (owner, 2026-11-06, docs/方案/2026-11-06-文字模型和Claude共用一份笔记写法.md;
10-18 review items 1-4, 6, 12, 20, 21), written from what the user gets:

- Every text-model note reads the note skill in effect: the user's own rules,
  meetings' 「还没定的」, 「（存疑）」 in place, with the series' correction
  documents. That holds for each chapter and the stitching of a long
  recording, not only for the coverage check.
- A rewrite from the page or an agent reads the task's transcript with its
  speaker labels, as the first note did, and is marked as written without
  the pictures.
- The md, the PDF and the Feishu copy of such a note open with one line saying
  so; an edit taken back from Obsidian or Feishu does not bring the line in.
- When the cut changes and a text model rewrites the note, the note it
  replaces can be restored; with no model at all, the old note stays and says
  it is about the cut before the change, until the note changes.
- Trying a rule out on a text-model task uses the text model.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import ai_summarizer, correction_notes, job_store, note_prompt, note_skills, note_versions, output_folder
from backend.core.local_request_scope import LOCAL_OWNER_ID
from tests.test_note_history import _quiet, _result, _task, _web_edit  # noqa: F401
from tests.test_output_folder_edits import _edit, pdfs  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
USER_RULE = "- 每节结尾加一句「一句话记住」。"


@pytest.fixture
def user_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_NOTE_SKILL_DIR", str(tmp_path / "skills"))
    default = note_skills.load_note_skill().text
    note_skills.save_user_skill(default.rstrip() + "\n" + USER_RULE + "\n")
    return USER_RULE


def test_the_text_models_rules_are_the_skill_with_the_text_only_part(user_rule, monkeypatch):
    monkeypatch.setattr(correction_notes, "for_task", lambda task_id, client_id=None: "- 魏鑫：转录常听成魏星")
    prompt = note_prompt.for_text_model("t", extra="这篇多写例子")

    assert USER_RULE in prompt
    assert "还没定的" in prompt and "（存疑）" in prompt
    assert note_prompt.TEXT_ONLY in prompt
    assert "魏鑫" in prompt
    assert prompt.index(USER_RULE) < prompt.index("这篇多写例子"), "what was asked comes after the rules"


class _FakeModel:
    """Answers each step of the note pipeline the way a model would, and
    keeps every system prompt it was given."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def __call__(self, client, model, system, user, temperature=0.2, **_kw):
        self.systems.append(system)
        if "证据抽取" in system:
            return json.dumps([{"evidence_id": "E1", "source_segment_ids": ["S001"], "type": "concept",
                                "text": "说话人 A：下周上线", "importance": 5}], ensure_ascii=False)
        if "章节规划" in system:
            return json.dumps([{"chapter_id": "CH01", "title": "上线", "purpose": "定时间",
                                "used_evidence_ids": ["E1"]}], ensure_ascii=False)
        if "要点" in system and "JSON" in system:
            return json.dumps({"missing": []})
        return "# 周会\n\n## 一、上线\n\n说话人 A 说下周上线。"


@pytest.mark.parametrize("mode", ["direct", "chapter_coverage"])
def test_every_step_that_writes_reads_the_rules(user_rule, monkeypatch, mode):
    fake = _FakeModel()
    monkeypatch.setattr(ai_summarizer, "_get_client", lambda **kw: object())
    monkeypatch.setattr(ai_summarizer, "_chat", fake)
    monkeypatch.setattr(correction_notes, "for_task", lambda task_id, client_id=None: "- 魏鑫：转录常听成魏星")

    ai_summarizer.summarize_transcript_with_metadata(
        "说话人 A：下周上线。", provider="deepseek", api_key="sk-test", note_mode=mode,
        speaker_labeled=True, task_id="t",
    )

    writers = [system for system in fake.systems if "证据抽取" not in system and "章节规划" not in system]
    assert writers, "the note was written"
    note_writers = [system for system in writers if "# 写法" in system or system.startswith(note_skills.load_note_skill().rules.strip()[:20])]
    assert note_writers, "a writing step got the skill"
    for system in note_writers:
        assert USER_RULE in system and "魏鑫" in system and "（存疑）" in system
        assert "说话人 A" in system, "the speaker rule rides along"
    if mode == "chapter_coverage":
        chapter_steps = [system for system in fake.systems if "章节笔记写作助手" in system or "长文档编校助手" in system]
        assert len(chapter_steps) == 2 and all(USER_RULE in system for system in chapter_steps)


def test_no_note_prompt_is_put_together_anywhere_else():
    """Every call of the summarizer names its task, so the rules and the
    correction documents come from ``note_prompt``; nothing builds its own."""
    callers = []
    for path in (ROOT / "backend").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "summarize_transcript_with_metadata":
                callers.append((path.relative_to(ROOT).as_posix(), {kw.arg for kw in node.keywords}))
    assert callers
    for where, names in callers:
        assert "task_id" in names, f"{where} writes a note without naming its task"
    assert "FLUENTFLOW_SYSTEM_PROMPT" not in (ROOT / "backend" / "core" / "ai_prompts.py").read_text(encoding="utf-8")


def test_a_rewrite_reads_the_speaker_labels_and_is_marked():
    result = {
        "transcript_text": "下周上线。好的。",
        "raw_segments": [{"start": 0, "end": 1, "text": "下周上线。", "speaker": "A"},
                         {"start": 1, "end": 2, "text": "好的。", "speaker": "B"}],
    }
    text, labeled = note_prompt.transcript_for_note(result)
    assert labeled and "说话人 A" in text and "说话人 B" in text
    # An edit saved in the editor is the newest text.
    edited = {**result, "corrected_transcript_text": "旧的", "transcript_edited": True}
    assert "下周上线" in note_prompt.transcript_for_note(edited)[0]

    assert note_prompt.written_by_text_model({"summary_written_from": "text_regeneration"})
    assert note_prompt.written_by_text_model({"summary_written_from": "text_fallback"})
    # Another writer's stamp wins over a leftover from an earlier text note.
    assert not note_prompt.written_by_text_model({"summary_written_from": "agent_note", "note_written_by": "text_fallback"})
    assert not note_prompt.written_by_text_model({"summary_markdown": "# 字幕文件的笔记"})


def test_the_line_goes_on_copies_and_comes_off_edits():
    note = "# 周会\n\n## 一、上线\n\n正文。"
    marked = note_prompt.with_text_model_line(note, {"summary_written_from": "text_fallback"})
    assert marked.split("\n")[2] == note_prompt.TEXT_MODEL_LINE
    assert note_prompt.with_text_model_line(marked, {"summary_written_from": "text_fallback"}) == marked
    assert note_prompt.with_text_model_line(note, {"summary_written_from": "agent_note"}) == note
    assert note_prompt.without_text_model_line(marked) == note


def _text_note_now(task, monkeypatch, *, key: bool = True) -> None:
    import backend.core.local_entry_guards as guards
    from backend.core import local_intake_flow

    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **kw: {"api_key": "sk-test", "provider": "deepseek"} if key else {})
    monkeypatch.setattr(ai_summarizer, "summarize_transcript_with_metadata", lambda transcript, **kw: SimpleNamespace(
        markdown="# 文本模型写的笔记\n\n正文。", requested_mode="auto", resolved_mode="direct", chunk_count=1,
        deadline_hit=False))
    monkeypatch.setattr(ai_summarizer, "text_model_name", lambda **kw: "deepseek-test")
    local_intake_flow._write_text_note_instead(
        task, LOCAL_OWNER_ID, SimpleNamespace(unavailable_reason="Claude 没登录。", name="subscription"))


def test_a_text_rewrite_after_the_cut_changed_keeps_the_old_note_and_marks_its_copies(tmp_path, monkeypatch, pdfs):  # noqa: F811
    task, md = _task(tmp_path)
    before = _result(task)["summary_markdown"]

    _text_note_now(task, monkeypatch)

    result = _result(task)
    assert result["summary_markdown"].startswith("# 文本模型写的笔记")
    assert result["visual_note"]["replaced_note"]["previous_markdown"] == before, "the old note can be put back"
    output_folder.sync(task)
    on_disk = md.read_text(encoding="utf-8")
    assert note_prompt.TEXT_MODEL_LINE in on_disk

    # Edited in Obsidian and taken back: the line stays out of the note.
    _edit(md, on_disk.replace("正文。", "正文，改过。"))
    output_folder.sync(task)
    note = _result(task)["summary_markdown"]
    assert "正文，改过。" in note and note_prompt.TEXT_MODEL_LINE not in note


def test_with_no_model_the_old_note_stays_and_says_it_is_for_the_old_cut(tmp_path, monkeypatch):
    task, _md = _task(tmp_path)
    before = _result(task)["summary_markdown"]

    _text_note_now(task, monkeypatch, key=False)

    result = _result(task)
    assert result["summary_markdown"] == before and result["summary_status"] == "completed"
    assert "改之前那版剪辑" in result[note_versions.STALE_FIELD]["reason"]

    _web_edit(task, before + "\n补一句。\n")
    assert note_versions.STALE_FIELD not in _result(task), "a changed note is no longer the stale one"


def test_trying_a_rule_on_a_text_model_note_uses_the_text_model(user_rule, monkeypatch):
    from backend.core import note_feedback
    import backend.core.local_entry_guards as guards

    seen: dict = {}
    task_id = "np-rule"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result={
        "transcript_text": "一句话。", "summary_markdown": "# 笔记", "summary_written_from": "text_fallback",
    })
    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **kw: {"api_key": "sk-test", "provider": "deepseek"})
    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **kw: (
        lambda system, user: seen.update(system=system, user=user) or "# 试写"))
    monkeypatch.setattr(ai_summarizer, "text_model_name", lambda **kw: "deepseek-test")

    drafted = note_feedback.draft_part(task_id, rules="## 候选\n- 只写结论。", client_id=LOCAL_OWNER_ID)

    assert drafted["markdown"] == "# 试写" and drafted["model"] == "deepseek-test"
    assert "只写结论" in seen["system"] and note_prompt.TEXT_ONLY in seen["system"]
    assert "一句话" in seen["user"]
