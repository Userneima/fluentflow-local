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
import backend.core.lark_cli_run as lark_cli_run
from backend.core import job_store, note_spacing, review_note_feishu as rnf
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
    comments = 0

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
        if cmd[1:3] == ["drive", "+list-comments"]:
            self.calls.append(list(cmd))
            return _ok({"count": self.comments, "items": [{"comment_id": "c1"}] * self.comments, "has_more": False})
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
    monkeypatch.setattr(lark_cli_run, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
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
    monkeypatch.setattr(lark_cli_run, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
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


def test_in_feishu_a_colon_line_runs_into_its_list_and_a_check_stays_in_its_list(wiki, tmp_path):
    """Owner, 2026-11-16: the review note in Feishu reads as it does in
    Obsidian, with no empty paragraph under 「例子：」 nor around a 核对 in a list."""
    review = REVIEW.replace("## 术语表", "**例子**：\n- 第一条。\n> **核对（2026-11-05）**：补一句。\n- 第二条。\n\n## 术语表")
    task_id, _ = _lesson(tmp_path, review=review)

    rnf.push(task_id, runner=wiki)

    texts = [b for b in wiki.blocks]
    at = next(i for i, b in enumerate(texts) if b.get("text", "").startswith("**例子**") or b.get("text", "").startswith("<b>例子"))
    following = [b for b in texts[at + 1:at + 4]]
    assert all(b.get("text") for b in following), [b.get("text") for b in following]


def test_a_document_laid_out_the_old_way_is_written_again_once(wiki, tmp_path):
    task_id, _ = _lesson(tmp_path)
    rnf.push(task_id, runner=wiki)
    record = job_store.get_job(task_id)["result"]
    job_store.update_job_result(task_id, {**record, rnf.RESULT_KEY: {**record[rnf.RESULT_KEY], "layout": None}})

    assert rnf.pending(job_store.get_job(task_id)["result"])
    rnf.push(task_id, runner=wiki)

    assert not rnf.pending(job_store.get_job(task_id)["result"]) and wiki.creates() == 1


def test_a_document_with_comments_is_not_laid_out_again(wiki, tmp_path):
    task_id, _ = _lesson(tmp_path)
    rnf.push(task_id, runner=wiki)
    record = job_store.get_job(task_id)["result"]
    job_store.update_job_result(task_id, {**record, rnf.RESULT_KEY: {**record[rnf.RESULT_KEY], "layout": None}})
    wiki.comments = 1
    writes = len(wiki.writes)

    rnf.push(task_id, runner=wiki)

    assert len(wiki.writes) == writes, "nothing deleted or written: the comments stay"


def test_a_third_level_heading_has_a_line_above_it_in_feishu():
    from backend.core import feishu_note

    md = note_spacing.for_renderers("### （一）甲\n- 一条。\n\n### （二）乙\n**结论**：这一段要够长才算正文段落。\n\n**为什么**：\n- 因为。\n")
    laid = feishu_note.to_feishu(md, tight=True).markdown
    assert "- 一条。\n\n<p></p>\n\n### （二）乙" in laid
    assert "段落。\n\n<p></p>\n\n**为什么**：\n\n- 因为。" in laid, "the heading's first paragraph is a block of its own"


def test_between_chapters_a_third_level_heading_has_a_line_above_it(wiki, tmp_path):
    review = REVIEW.replace("## 主线\n\n一句话。\n", "## 主线\n\n### （一）甲\n- 一条。\n\n### （二）乙\n- 两条。\n")
    task_id, _ = _lesson(tmp_path, review=review)

    rnf.push(task_id, runner=wiki)

    at = next(i for i, b in enumerate(wiki.blocks) if b["tag"] == "h3" and "乙" in b["text"])
    assert wiki.blocks[at - 1]["tag"] == "p" and not wiki.blocks[at - 1]["text"], [b["text"] for b in wiki.blocks[at - 3:at + 1]]


def test_feishu_keeps_the_md_spacing_after_a_quoted_colon_line_and_before_a_table():
    """The md (note_spacing.tidy) and Feishu answer the same question,
    note_spacing.joins: a colon line that is itself a quote keeps its blank
    line, and a table always has one above it."""
    from backend.core import feishu_note, note_spacing

    md = "**依据**：\n\n| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |\n\n> 老师说的原话是这样：\n\n下一段正文。\n"
    assert note_spacing.tidy(md) == md
    laid = feishu_note.to_feishu(note_spacing.for_renderers(md), tight=True).markdown
    assert "**依据**：\n\n<p></p>\n\n<table>" in laid
    assert "</callout>\n\n<p></p>\n\n下一段正文。" in laid or "这样：\n\n<p></p>\n\n下一段正文。" in laid, laid


def test_a_fence_inside_a_longer_fence_stays_code_in_feishu():
    """The same fence rule as the md and PDF (note_spacing.in_code): a ```
    inside a ```` block does not close it."""
    from backend.core import feishu_note

    md = "````md\n```python\n# 不是标题\nprint(1)\n```\n````\n\n## 真标题\n"
    laid = feishu_note.to_feishu(md).markdown
    assert "````md\n```python\n# 不是标题\nprint(1)\n```\n````" in laid, laid
    assert [s.heading for s in feishu_note.sections(md) if s.heading] == ["真标题"]


def test_an_image_line_inside_a_tilde_fence_is_not_taken_for_a_screenshot(tmp_path):
    """Code blocks fenced with ~~~ are code for the screenshot upload too
    (note_spacing.in_code): an example image line inside one stays text."""
    from backend.core import lark_cli_images

    md = "~~~md\n![例子](/jobs/t/artifacts/frame?file=a.jpg)\n~~~\n"
    written, pending, missing = lark_cli_images._prepare_images(md, task_id="t", artifact_root=tmp_path)
    assert written == md and pending == [] and missing == []


def test_two_code_blocks_next_to_each_other_stay_two_blocks_in_feishu():
    from backend.core import feishu_note

    laid = feishu_note.to_feishu("```python\nprint(1)\n```\n```js\nlog(2)\n```\n").markdown
    assert "```\n\n<p></p>\n\n```js" in laid, laid


def test_a_chapter_written_alone_keeps_the_whole_note_figure_numbers(tmp_path):
    from backend.core import lark_cli_images

    first = "## 一\n~~~md\n![例子](/jobs/t/artifacts/frame?file=a.jpg)\n~~~\n"
    assert lark_cli_images._local_images_in(first) == 0
    assert lark_cli_images._local_images_in("## 二\n![画面](/jobs/t/artifacts/frame?file=b.jpg)\n") == 1
