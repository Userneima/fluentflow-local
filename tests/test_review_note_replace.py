"""An agent changes passages of the review note by their exact text.

Requirement (owner, 2026-11-06): deleting one kind of content from a long
review note must not mean handing the whole note back through
revise_review_note (an agent went round the tools to avoid it). So:

- Each old_text must occur in the review md exactly once; several pairs go in
  one call, and when any one of them does not fit nothing changes at all.
- What the user changed in Obsidian is never written over: an edit elsewhere
  stays, an edit of the named passage refuses the call, and a review_version
  the md no longer has refuses it too.
- A replace keeps the previous md in 过程文件/历史版本, prints the PDF again
  and notes the edit on the task, like a revision; no model is called.
- The MCP tool replace_note_text reaches it with note=review.
"""

from __future__ import annotations

import pytest

from backend.core import job_store, review_note
from scripts import fluentflow_mcp_server as mcp

from tests.test_review_note import _HEADERS, ANSWER, STEM, _client, _folder, _task, home, no_browser  # noqa: F401


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def refuse():
        raise AssertionError("a model was called on a replace")

    monkeypatch.setattr(review_note, "default_chat", refuse)


@pytest.fixture
def review(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "review-token")
    folder = _folder(tmp_path, task_id="t-rr")
    _task("t-rr", folder / f"{STEM}.md")
    path = folder / "课_复习笔记.md"
    path.write_text(ANSWER, encoding="utf-8")
    return path


def _history(path):
    folder = path.parent / "过程文件" / "历史版本"
    return sorted(folder.glob("课_复习笔记_*.md")) if folder.is_dir() else []


def _replace(**body):
    return _client().post("/agent/v1/review-notes/replace", headers=_HEADERS, json={"task_id": "t-rr", **body})


def test_several_passages_change_the_old_one_is_kept_and_the_pdf_follows(review, no_browser):
    response = _replace(replacements=[
        {"old_text": "先拆读，再拆写。", "new_text": "先拆读请求，再拆写请求。"},
        {"old_text": "- 读请求按小时统计，高峰在晚上。\n", "new_text": ""},
    ], reason="去掉转录补充")

    assert response.status_code == 200, response.text
    answer = response.json()
    text = review.read_text(encoding="utf-8")
    assert text == ANSWER.replace("先拆读，再拆写。", "先拆读请求，再拆写请求。").replace("- 读请求按小时统计，高峰在晚上。\n", "")
    assert answer["replaced"] is True and answer["replacements"] == 2
    assert answer["review_version"] == review_note.review_version(text)
    kept = _history(review)
    assert len(kept) == 1 and kept[0].name.endswith("_agent改字前.md")
    assert kept[0].read_text(encoding="utf-8") == ANSWER
    assert no_browser[-1][2] == "课_复习笔记"
    edit = job_store.get_job("t-rr")["result"]["review_note"]["last_edit"]
    assert edit["edited_by"] == "agent_edit" and edit["reason"] == "去掉转录补充"


def test_text_not_in_the_note_changes_nothing(review):
    response = _replace(old_text="复习笔记里没有这句。", new_text="x")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "not_found_in_note"
    assert review.read_text(encoding="utf-8") == ANSWER and not _history(review)


def test_text_that_occurs_twice_says_how_many_times_and_changes_nothing(review):
    response = _replace(old_text="为什么要扩容", new_text="扩容的原因")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "ambiguous"
    assert "2 次" in response.json()["detail"]["message"]
    assert review.read_text(encoding="utf-8") == ANSWER and not _history(review)


def test_one_bad_pair_among_many_changes_nothing(review):
    response = _replace(replacements=[
        {"old_text": "先拆读，再拆写。", "new_text": "改了"},
        {"old_text": "## 术语表", "new_text": "## 名词"},
        {"old_text": "为什么要扩容", "new_text": "x"},
    ])

    assert response.status_code == 409 and response.json()["detail"]["code"] == "ambiguous"
    assert "第 3 组" in response.json()["detail"]["message"]
    assert review.read_text(encoding="utf-8") == ANSWER and not _history(review)


def test_overlapping_pairs_change_nothing(review):
    response = _replace(replacements=[
        {"old_text": "先拆读，再拆写。", "new_text": "a"},
        {"old_text": "再拆写。\n\n## 一", "new_text": "b"},
    ])

    assert response.status_code == 409 and response.json()["detail"]["code"] == "overlapping"
    assert review.read_text(encoding="utf-8") == ANSWER


def test_a_version_the_md_no_longer_has_is_refused(review):
    read_at = review_note.review_version(ANSWER)
    edited = ANSWER.replace("读太多。", "读太多，用户在 Obsidian 里补了一句。")
    review.write_text(edited, encoding="utf-8")

    response = _replace(old_text="先拆读，再拆写。", new_text="改了", base_version=read_at)

    assert response.status_code == 409 and response.json()["detail"]["code"] == "stale"
    assert review.read_text(encoding="utf-8") == edited


def test_an_edit_the_user_made_elsewhere_stays_and_one_of_the_passage_refuses(review):
    review.write_text(ANSWER.replace("读太多。", "读太多，用户补的一句。"), encoding="utf-8")

    ok = _replace(old_text="先拆读，再拆写。", new_text="改了")
    assert ok.status_code == 200, ok.text
    assert "用户补的一句" in review.read_text(encoding="utf-8")

    review.write_text(review.read_text(encoding="utf-8").replace("改了", "用户又改了这句"), encoding="utf-8")
    refused = _replace(old_text="改了\n", new_text="agent 的版本\n")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_found_in_note"
    assert "用户又改了这句" in review.read_text(encoding="utf-8")


def test_an_obsidian_save_landing_during_the_write_is_not_overwritten(review, monkeypatch):
    keep = review_note._keep_previous

    def save_meanwhile(target, label="重新生成前"):
        keep(target, label)
        target.write_text(ANSWER + "\nObsidian 刚存的一句。\n", encoding="utf-8")

    monkeypatch.setattr(review_note, "_keep_previous", save_meanwhile)
    response = _replace(old_text="先拆读，再拆写。", new_text="改了")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "stale"
    assert review.read_text(encoding="utf-8") == ANSWER + "\nObsidian 刚存的一句。\n"


def test_the_tool_reaches_the_review_route_with_note_review(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(mcp, "_agent_request",
                        lambda method, path, **kw: calls.append((method, path, kw.get("payload"))) or {"ok": True})

    mcp.replace_note_text("t1", note="review", replacements=[{"old_text": "a", "new_text": ""}])

    assert calls == [("POST", "/agent/v1/review-notes/replace", {
        "base_version": None, "replacements": [{"old_text": "a", "new_text": ""}],
        "task_id": "t1", "note_path": None,
    })]


def test_two_replaces_in_one_second_keep_both_earlier_versions(review, monkeypatch):
    from datetime import datetime as real

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 11, 6, 18, 0, 0, tzinfo=tz)

    monkeypatch.setattr(review_note, "datetime", Frozen)
    assert _replace(old_text="先拆读，再拆写。", new_text="第一次").status_code == 200
    assert _replace(old_text="第一次", new_text="第二次").status_code == 200

    kept = [path.read_text(encoding="utf-8") for path in _history(review)]
    assert len(kept) == 2 and ANSWER in kept
    assert any("第一次" in text for text in kept)
