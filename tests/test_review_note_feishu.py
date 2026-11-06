"""The review note goes to Feishu above its follow-along note.

Requirements (owner, 2026-11-06):
1. A lesson whose follow-along note is kept in Feishu gets its review note as
   a document in the same place, titled after the follow-along note's title
   in Feishu, and the follow-along note moves under it.
2. A follow-along note not kept in Feishu: nothing is sent.
3. A changed review note updates that same document; no second document,
   and the follow-along note is not moved again. Unchanged: nothing is sent.
4. A move that fails leaves the review note in Feishu, says so on the task,
   and never makes a second document.
5. Screenshots beside the review note md go up as the task's frames.
6. Review notes written before this, or edited in Obsidian, are found by the
   watch loop.
"""

from __future__ import annotations

import uuid

import pytest

import backend.core.lark_cli_exporter as cli
from backend.core import job_store, review_note_feishu as rnf
from tests.test_feishu_chapter_update import DOC, FakeDoc, _flag, _ok

FOLLOW_DOC = "doxFOLLOW"
FOLLOW_NODE = "wikFOLLOW"
LESSON_PARENT = "wikLESSONS"
REVIEW_NODE = "wikREVIEW"

REVIEW = """# 第三课：复习笔记

## 主线

一句话。

## 术语表

缓存：从前往后比对。

![缓存打标](过程文件/截图/note_0034.jpg)
"""


class FakeWiki(FakeDoc):
    """lark-cli over a personal library: the follow-along note under a lesson
    folder, and the review note's document (``DOC``) once created."""

    def __init__(self) -> None:
        super().__init__()
        self.parents = {FOLLOW_NODE: LESSON_PARENT}
        self.created_under: list[str | None] = []
        self.titles: list[str] = []
        self.fail_move = False

    def __call__(self, cmd, **kwargs):
        if cmd[1:3] == ["wiki", "+node-get"]:
            self.calls.append(list(cmd))
            token = _flag(cmd, "--node-token")
            if token == FOLLOW_DOC:
                return _ok({"node_token": FOLLOW_NODE, "obj_token": FOLLOW_DOC, "title": "3.第一次汇报-0707",
                            "parent_node_token": self.parents[FOLLOW_NODE], "space_id": "sp1"})
            if token == DOC and self.created_under:
                return _ok({"node_token": REVIEW_NODE, "obj_token": DOC, "title": self.titles[-1],
                            "parent_node_token": self.created_under[-1] or "", "space_id": "sp1"})
            return _ok({})
        if cmd[1:3] == ["wiki", "+node-list"]:
            self.calls.append(list(cmd))
            parent = _flag(cmd, "--parent-node-token") if "--parent-node-token" in cmd else ""
            items = [{"title": t, "obj_token": DOC, "node_token": REVIEW_NODE}
                     for t, under in zip(self.titles, self.created_under) if (under or "") == parent]
            return _ok({"items": items})
        if cmd[1:3] == ["wiki", "+move"]:
            self.calls.append(list(cmd))
            if self.fail_move:
                return subprocess_fail("no permission to move")
            self.parents[_flag(cmd, "--node-token")] = _flag(cmd, "--target-parent-token")
            return _ok({"node": {"node_token": _flag(cmd, "--node-token")}})
        if len(cmd) > 2 and cmd[2] == "+create":
            self.created_under.append(_flag(cmd, "--parent-token") if "--parent-token" in cmd else None)
            self.titles.append(_flag(cmd, "--title"))
        return super().__call__(cmd, **kwargs)

    def creates(self) -> int:
        return sum(1 for c in self.calls if len(c) > 2 and c[2] == "+create")

    def moves(self) -> int:
        return sum(1 for c in self.calls if c[1:3] == ["wiki", "+move"])


def subprocess_fail(message: str):
    from tests.test_feishu_chapter_update import _fail

    return _fail(message)


@pytest.fixture
def wiki(monkeypatch, tmp_path):
    fake = FakeWiki()
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    monkeypatch.setattr("backend.core.storage_paths._artifact_storage_dir", lambda: tmp_path / "artifacts")
    return fake


def _lesson(tmp_path, *, kept_in_feishu: bool = True, review: str = REVIEW) -> tuple[str, object]:
    task_id = f"rv-{uuid.uuid4().hex[:8]}"
    folder = tmp_path / "lesson"
    folder.mkdir(exist_ok=True)
    md = folder / "第三课_复习笔记.md"
    md.write_text(review, encoding="utf-8")
    frames = tmp_path / "artifacts" / task_id / "frames"
    frames.mkdir(parents=True)
    (frames / "note_0034.jpg").write_bytes(b"jpg")
    sync = {"doc_id": FOLLOW_DOC, "url": "https://x/docx/doxFOLLOW", "sections": []}
    response = {"url": sync["url"], "via": "lark_cli", "sync": sync} if kept_in_feishu else {"via": "lark_openapi"}
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "summary_markdown": "# 随看\n", "lark_response": response, "lark_doc_title": "第三课",
        "review_note": {"path": str(md)},
    })
    return task_id, md


def _record(task_id: str) -> dict:
    return job_store.get_job(task_id)["result"].get(rnf.RESULT_KEY) or {}


def test_the_review_note_goes_where_the_follow_along_note_was_and_takes_it_under_it(wiki, tmp_path):
    task_id, _ = _lesson(tmp_path)

    rnf.push(task_id, runner=wiki)

    assert wiki.created_under == [LESSON_PARENT]
    assert wiki.titles == ["3.第一次汇报-0707" + rnf.TITLE_SUFFIX]
    assert wiki.parents[FOLLOW_NODE] == REVIEW_NODE
    record = _record(task_id)
    assert record["moved"] is True and record["error"] is None and record["url"]


def test_a_follow_along_note_not_kept_in_feishu_sends_nothing(wiki, tmp_path):
    task_id, _ = _lesson(tmp_path, kept_in_feishu=False)

    assert rnf.push(task_id, runner=wiki) is None
    assert wiki.calls == []


def test_a_changed_review_note_updates_the_same_document_and_moves_nothing(wiki, tmp_path):
    task_id, md = _lesson(tmp_path)
    rnf.push(task_id, runner=wiki)
    calls_after_first = len(wiki.calls)

    assert rnf.push(task_id, runner=wiki) is None  # unchanged
    assert len(wiki.calls) == calls_after_first

    md.write_text(REVIEW.replace("一句话。", "改过的一句话。"), encoding="utf-8")
    rnf.push(task_id, runner=wiki)

    assert wiki.creates() == 1 and wiki.moves() == 1
    assert "改过的一句话。" in wiki.texts()


def test_a_failed_move_is_reported_and_never_makes_a_second_document(wiki, tmp_path):
    task_id, md = _lesson(tmp_path)
    wiki.fail_move = True

    rnf.push(task_id, runner=wiki)

    record = _record(task_id)
    assert record["moved"] is False and "手动" in record["error"]
    md.write_text(REVIEW + "\n补一句。\n", encoding="utf-8")
    rnf.push(task_id, runner=wiki)
    assert wiki.creates() == 1


def test_screenshots_beside_the_md_go_up_as_the_task_s_frames(wiki, tmp_path):
    task_id, _ = _lesson(tmp_path)

    rnf.push(task_id, runner=wiki)

    assert sum(1 for c in wiki.calls if len(c) > 2 and c[2] == "+media-insert") == 1


def test_the_watch_finds_review_notes_not_in_feishu_yet(wiki, tmp_path, monkeypatch):
    scheduled: list[str] = []
    monkeypatch.setattr(rnf, "schedule", lambda task_id, delay=None: scheduled.append(task_id))
    waiting, _ = _lesson(tmp_path)
    other, _ = _lesson(tmp_path, kept_in_feishu=False)

    rnf.check()

    assert waiting in scheduled and other not in scheduled


def test_a_push_cut_off_before_it_was_recorded_writes_into_the_same_document(wiki, tmp_path, monkeypatch):
    task_id, _ = _lesson(tmp_path)
    real_record = rnf._record
    calls = {"n": 0}

    def cut_off(task, **fields):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemExit("service stopped")
        return real_record(task, **fields)

    monkeypatch.setattr(rnf, "_record", cut_off)
    with pytest.raises(SystemExit):
        rnf.push(task_id, runner=wiki)
    monkeypatch.setattr(rnf, "_record", real_record)

    rnf.push(task_id, runner=wiki)

    assert wiki.creates() == 1
    assert wiki.parents[FOLLOW_NODE] == REVIEW_NODE
    assert _record(task_id)["moved"] is True


def test_a_move_cut_off_is_done_on_a_later_look(wiki, tmp_path, monkeypatch):
    task_id, _ = _lesson(tmp_path)
    monkeypatch.setattr(rnf, "_move_under", lambda *a: (_ for _ in ()).throw(SystemExit("service stopped")))
    with pytest.raises(SystemExit):
        rnf.push(task_id, runner=wiki)
    monkeypatch.undo()
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    monkeypatch.setattr("backend.core.storage_paths._artifact_storage_dir", lambda: tmp_path / "artifacts")

    assert rnf.pending(job_store.get_job(task_id)["result"])
    rnf.push(task_id, runner=wiki)

    assert wiki.parents[FOLLOW_NODE] == REVIEW_NODE and wiki.creates() == 1
    assert not rnf.pending(job_store.get_job(task_id)["result"])


def test_a_document_of_the_same_name_this_task_never_made_is_not_taken_over(wiki, tmp_path):
    task_id, _ = _lesson(tmp_path)
    wiki.titles.append("3.第一次汇报-0707" + rnf.TITLE_SUFFIX)
    wiki.created_under.append(LESSON_PARENT)

    rnf.push(task_id, runner=wiki)

    assert wiki.creates() == 1 and _record(task_id)["error"] is None


def test_a_feishu_edit_is_kept_when_its_time_cannot_be_read(wiki, tmp_path):
    task_id, md = _lesson(tmp_path)
    rnf.push(task_id, runner=wiki)
    wiki.edit("一句话。", "飞书上改的。")
    real = wiki.__call__
    wiki.__class__ = type("NoMetas", (FakeWiki,), {
        "__call__": lambda self, cmd, **k: _ok({"metas": []}) if cmd[1:3] == ["drive", "metas"] else real(cmd, **k)})
    md.write_text(REVIEW.replace("一句话。", "本地改的。"), encoding="utf-8")

    rnf.push(task_id, runner=wiki)

    kept = list((md.parent / "过程文件" / "历史版本").glob("*飞书上改过*"))
    assert kept and "飞书上改的" in kept[0].read_text(encoding="utf-8")
