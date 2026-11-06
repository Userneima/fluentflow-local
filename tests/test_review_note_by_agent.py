"""The agent at hand writes the review note (decision 5.47).

Requirement (owner, 2026-11-05; decision 6.48: the current agent does the
writing by default; most users drive FluentFlow from Codex):

- The agent reads exactly what FluentFlow's model would read: the writing
  rules, the follow-along note and the transcript, in
  parts small enough for Codex to show whole.
- What it hands back goes through the same steps as the model's answer:
  the base version is
  stamped, an earlier review note is kept, a PDF is printed, the task records
  who wrote it, and an incomplete note is refused with nothing written.
- If the follow-along note (or, revising, the review note) changed after the
  material was read, nothing is written: the answer would undo the user's
  edit. The agent is told to read the material again.
- No model is called on this path.
- Revising works the same way; a review note written before sources were
  dropped (decision 5.55) keeps its old links exactly when the answer does.
- Through the MCP tool, the agent's way is the default; writer=fluentflow
  keeps FluentFlow's own model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core import job_store, review_note
from scripts import fluentflow_mcp_server as mcp

from tests.test_review_note import ANSWER, BASE, STEM, FakeChat, _folder, _legacy, _task, home, no_browser  # noqa: F401


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def refuse():
        raise AssertionError("a model was called on the agent's path")

    monkeypatch.setattr(review_note, "default_chat", refuse)


def _whole(task_id=None, note_path=None, instruction=None) -> tuple[str, str]:
    first = review_note.agent_material(task_id, note_path, instruction)
    parts = [first["text"]] + [
        review_note.agent_material(task_id, note_path, instruction, part)["text"]
        for part in range(2, first["total_parts"] + 1)
    ]
    return "".join(parts), first["material_version"]


def test_the_agent_reads_what_the_model_would_and_its_note_is_written_the_same_way(tmp_path, no_browser):
    folder = _folder(tmp_path, task_id="t-agent-1")
    _task("t-agent-1", folder / f"{STEM}.md")
    model = FakeChat()
    review_note.generate("t-agent-1", chat=(model, "x"))
    by_model = (folder / "课_复习笔记.md").read_text(encoding="utf-8")
    system, user = model.calls[0]

    text, version = _whole("t-agent-1")
    assert system.strip() in text and user.strip() in text

    answer = review_note.save_from_agent(ANSWER, version, task_id="t-agent-1", model="gpt-5-codex")

    by_agent = (folder / "课_复习笔记.md").read_text(encoding="utf-8")
    assert by_agent == by_model
    assert by_agent.startswith(ANSWER.rstrip())
    assert list((folder / "过程文件" / "历史版本").glob("课_复习笔记_*.md"))
    assert answer["written_by"] == "agent:gpt-5-codex"
    assert job_store.get_job("t-agent-1")["result"]["review_note"]["written_by"] == "agent:gpt-5-codex"
    assert no_browser[-1][2] == "课_复习笔记"
    assert (folder / f"{STEM}.md").read_text(encoding="utf-8") == BASE


def test_the_material_comes_in_parts_codex_can_show_whole(tmp_path, monkeypatch):
    folder = _folder(tmp_path, task_id="t-agent-2")
    (folder / f"{STEM}.md").write_text(BASE + ("\n读写分离的细节。" * 4000), encoding="utf-8")
    _task("t-agent-2", folder / f"{STEM}.md")

    first = review_note.agent_material("t-agent-2")
    assert first["total_parts"] > 1
    for part in range(1, first["total_parts"] + 1):
        assert len(review_note.agent_material("t-agent-2", part=part)["text"]) <= review_note.AGENT_PART_CHARS
    with pytest.raises(review_note.ReviewNoteError) as refused:
        review_note.agent_material("t-agent-2", part=first["total_parts"] + 1)
    assert refused.value.code == "bad_part"


def test_a_note_changed_after_the_material_was_read_is_not_written_over(tmp_path):
    folder = _folder(tmp_path, task_id="t-agent-3")
    _task("t-agent-3", folder / f"{STEM}.md")
    _text, version = _whole("t-agent-3")
    (folder / f"{STEM}.md").write_text(BASE + "\n用户刚补的一句。\n", encoding="utf-8")

    with pytest.raises(review_note.ReviewNoteError) as refused:
        review_note.save_from_agent(ANSWER, version, task_id="t-agent-3")

    assert refused.value.code == "stale"
    assert not (folder / "课_复习笔记.md").exists()


def test_an_incomplete_note_is_refused_and_nothing_is_written(tmp_path):
    folder = _folder(tmp_path, task_id="t-agent-4")
    _task("t-agent-4", folder / f"{STEM}.md")
    _text, version = _whole("t-agent-4")

    with pytest.raises(review_note.ReviewNoteError) as refused:
        review_note.save_from_agent(ANSWER.split("## 术语表")[0], version, task_id="t-agent-4")

    assert "术语表" in str(refused.value)
    assert not (folder / "课_复习笔记.md").exists()


def test_revising_by_the_agent_keeps_every_link_and_refuses_after_the_review_note_changed(tmp_path):
    folder = _folder(tmp_path, task_id="t-agent-5")
    _task("t-agent-5", folder / f"{STEM}.md")
    review = _legacy(folder)

    text, version = _whole(note_path=str(review), instruction="三级标题改一下")
    assert "三级标题改一下" in text and "〔S1 0:40〕" in text
    draft = text.split("# 现在的复习笔记\n\n", 1)[1]
    revised = draft.replace("### （一）读请求占九成", "### （一）读请求占了九成")
    answer = review_note.save_from_agent(revised, version, note_path=str(review), instruction="三级标题改一下")

    after = review.read_text(encoding="utf-8")
    assert "读请求占了九成" in after
    assert f"[[{STEM}#一、为什么要扩容#（一）读太多|第一章（一）]] 0:20" in after
    assert after.count("- 底稿版本：") == 1
    assert answer["revised_by"] == "agent" and "lost_links" not in answer

    _text, version = _whole(note_path=str(review), instruction="再改")
    review.write_text(after + "\n用户在 Obsidian 里加的一行。\n", encoding="utf-8")
    with pytest.raises(review_note.ReviewNoteError) as refused:
        review_note.save_from_agent(revised, version, note_path=str(review), instruction="再改")
    assert refused.value.code == "stale"
    assert "用户在 Obsidian 里加的一行" in review.read_text(encoding="utf-8")


def test_through_the_tool_the_agent_writes_by_default_and_fluentflow_only_when_asked(monkeypatch):
    asked: list[str] = []

    def request(method, path, *, payload=None, api_base=None, client_id=None, timeout=60):
        asked.append(path)
        return {"ok": True, "part": 1, "total_parts": 2, "material_version": "v", "text": "写法"}

    monkeypatch.setattr(mcp, "_agent_request", request)
    monkeypatch.setattr(mcp, "_runs", {})

    material = mcp.write_review_note(task_id="t")
    assert asked[-1] == "/agent/v1/review-notes/agent-material"
    assert "part=2" in material["next"] and "note_markdown" in material["next"]
    mcp.write_review_note(task_id="t", note_markdown="# 笔记", material_version="v")
    assert asked[-1] == "/agent/v1/review-notes/agent"
    mcp.revise_review_note("改一下", task_id="t")
    assert asked[-1] == "/agent/v1/review-notes/agent-material"
    mcp.write_review_note(task_id="t", writer="fluentflow")
    assert asked[-1] == "/agent/v1/review-notes"


def test_the_routes_hand_the_material_out_and_take_the_note_back(tmp_path, monkeypatch):
    from tests.test_review_note import _HEADERS, _client

    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "review-token")
    folder = _folder(tmp_path, task_id="t-agent-6")
    _task("t-agent-6", folder / f"{STEM}.md")
    client = _client()

    material = client.post("/agent/v1/review-notes/agent-material", headers=_HEADERS, json={"task_id": "t-agent-6"})
    assert material.status_code == 200
    version = material.json()["material_version"]

    stale = client.post("/agent/v1/review-notes/agent", headers=_HEADERS, json={
        "task_id": "t-agent-6", "note_markdown": ANSWER, "material_version": "wrong"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"

    saved = client.post("/agent/v1/review-notes/agent", headers=_HEADERS, json={
        "task_id": "t-agent-6", "note_markdown": ANSWER, "material_version": version, "model": "claude"})
    assert saved.status_code == 200 and saved.json()["written_by"] == "agent:claude"
    assert (folder / "课_复习笔记.md").is_file()
