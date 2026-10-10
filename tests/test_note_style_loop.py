"""Building a user's own note skill from their corrections.

Requirements (2026-10-09, docs/plans/2026-10-09-note-skills.md):

1. The first edit of a generated note keeps what was generated; later edits
   keep that same original; a newly written note starts over.
2. Candidate rules come from the differences between generated and edited
   notes, read by the meta skill together with the user's current skill. An
   edit is read once.
3. A candidate is only a suggestion: the skill does not change until the user
   applies a change, and the change is compared on one task first, with the
   current and the new skill on the same task.
4. Applying keeps the replaced version. A comparison made before the skill
   changed again cannot be applied.
5. Notes the user likes can seed candidates too.
6. The user's own skill gets its own name, not the product default's.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.core import job_store, note_preview, note_skills, note_style
from backend.local_main import create_local_app

OWNER = "local-single-user"
GENERATED = "# 课程笔记\n\n## 词汇表\n\n- 回归：一种方法\n\n## 正文\n\n这节讲回归。"
EDITED = "# 课程笔记\n\n## 正文\n\n这节讲回归（regression，用已知点拟合一条线）。"


@pytest.fixture(autouse=True)
def own_skill_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_NOTE_SKILL_DIR", str(tmp_path / "mine"))


@pytest.fixture()
def client():
    return TestClient(create_local_app(), headers={"X-FluentFlow-Client-Id": OWNER})


@pytest.fixture()
def task():
    task_id = "style-" + uuid.uuid4().hex
    job_store.upsert_job(task_id=task_id, status="completed", client_id=OWNER, stage="done",
                         result={"task_id": task_id, "summary_markdown": GENERATED, "display_title": "回归"})
    yield task_id
    job_store.delete_jobs([task_id], client_id=OWNER)


def _edit(client, task_id, text):
    response = client.patch(f"/jobs/{task_id}/summary", json={"summary_markdown": text})
    assert response.status_code == 200
    return response.json()["result"]


def test_the_generated_note_is_kept_at_the_first_edit_only(client, task):
    _edit(client, task, EDITED)
    result = _edit(client, task, EDITED + "\n\n再改一次。")
    assert result["summary_original_markdown"] == GENERATED


def test_a_newly_written_note_starts_over(client, task):
    from backend.core.visual_note_job import _promotion

    _edit(client, task, EDITED)
    current = job_store.get_job(task, client_id=OWNER)["result"]
    fields, _ = _promotion(current, "# 新写的笔记")
    assert fields["summary_edited"] is False and fields["summary_original_markdown"] is None


def test_candidates_come_from_the_differences_and_an_edit_is_read_once(client, task):
    _edit(client, task, EDITED)
    sent = []

    def chat(system, user):
        sent.append((system, user))
        return json.dumps([{"rule": "术语在正文第一次出现时用括号解释", "evidence": "删掉了词汇表", "replaces": ""}])

    added = note_style.propose_from_edits(client_id=OWNER, chat=chat)

    system, user = sent[0]
    assert system == note_skills.meta_skill().rules
    assert GENERATED in user and EDITED in user
    assert note_skills.default_skill().rules in user, "the meta skill sees the current skill"
    assert [item["rule"] for item in added] == ["术语在正文第一次出现时用括号解释"]
    assert note_style.propose_from_edits(client_id=OWNER, chat=chat) == []
    assert len(sent) == 1
    assert note_skills.load_note_skill().is_default, "a candidate changes nothing"


def _ready_proposal(monkeypatch, rule="术语在正文第一次出现时用括号解释"):
    note_style._store_candidates([{"rule": rule}], source="edits", task_ids=[])
    compared = []

    def fake_compare(task_id, old_rules, new_rules, **kwargs):
        compared.append((task_id, old_rules, new_rules))
        return (note_preview.SkillDraft("现在的版本", "# 旧", "m", [], {}),
                note_preview.SkillDraft("改动后的版本", "# 新", "m", [], {}))

    monkeypatch.setattr(note_preview, "compare", fake_compare)

    def merge(system, user):
        return "---\nname: fluentflow-note-default\ndescription: x\n---\n\n" + rule

    record = note_style.start_proposal(
        candidate_ids=[note_style.candidates()[0]["id"]], task_id="t1", chat=merge, run=lambda fn: fn(),
    )
    return record, compared


def test_a_change_is_compared_before_it_applies_and_keeps_the_old_version(monkeypatch):
    record, compared = _ready_proposal(monkeypatch)

    assert note_style.proposal()["status"] == "ready"
    assert compared == [("t1", note_skills.default_skill().rules, "术语在正文第一次出现时用括号解释")]
    assert note_skills.load_note_skill().is_default, "not in effect before apply"

    skill = note_style.apply_proposal()

    assert not skill.is_default and skill.rules == "术语在正文第一次出现时用括号解释"
    assert "name: my-note-style" in skill.text
    assert note_skills.skill_history()[0].read_text(encoding="utf-8").strip() == note_skills.default_skill().text
    assert note_style.candidates() == []
    assert note_style.proposal() is None


def test_a_comparison_made_before_another_change_cannot_apply(monkeypatch):
    _ready_proposal(monkeypatch)
    note_skills.save_user_skill("别处改过的版本")
    with pytest.raises(note_style.NoteStyleError):
        note_style.apply_proposal()


def test_liked_notes_seed_candidates():
    def chat(system, user):
        assert "满意的笔记" in user and "我喜欢的笔记" in user
        return '好的：[{"rule": "每节先用一句话给出答案", "evidence": "两份都这样"}]'

    added = note_style.propose_from_examples(["# 我喜欢的笔记\n\n一句话回答：……"], chat=chat)
    assert added[0]["rule"] == "每节先用一句话给出答案"


def test_the_page_reads_the_state(client, monkeypatch):
    note_style._store_candidates([{"rule": "结尾回顾直接列条目"}], source="edits", task_ids=[])
    body = client.get("/note-style").json()
    assert body["skill"]["is_default"] is True
    assert [item["rule"] for item in body["candidates"]] == ["结尾回顾直接列条目"]
    candidate_id = body["candidates"][0]["id"]
    assert client.post(f"/note-style/candidates/{candidate_id}/dismiss").json()["candidates"] == []
