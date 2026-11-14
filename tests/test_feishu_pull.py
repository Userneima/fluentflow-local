"""Edits made in the Feishu document come back into the note.

Requirements (owner, 2026-10-21):
1. A chapter edited in Feishu comes back into the note; every chapter not
   edited there keeps the note's own text, exactly.
2. It comes back the way the note writes it: a Feishu callout as an Obsidian
   callout of the kind its emoji says, a link to a chapter as [[#标题|文字]],
   a screenshot FluentFlow put in as the note's own image line (its caption not
   doubled), a picture added in Feishu fetched into the screenshots, Feishu's
   escapes undone.
3. The document only looked at (no chapter different) leaves the note alone.
4. Before the note is replaced it is kept among its versions; what came back
   is not pushed straight back to Feishu.
5. Both sides changed: the later change is the note, the other is kept beside
   the md. When the note's change is later, the chapters edited in Feishu are
   rewritten there with the note's text.
6. A document deleted in Feishu stops the syncing, with the reason on the task.
7. Feishu is asked when documents changed; a pull is queued only for one
   changed after the last push and left alone for a minute.
8. (owner, 2026-11-11) A push cut off after it began writing is finished on
   the next look, not taken for an edit made in Feishu: the note keeps its
   text and Feishu gets it.
9. (owner, 2026-11-14) A push that fails, for any reason, says so on the
   task and is tried again on the next look until it goes through; without
   knowing whether Feishu changed, nothing is written.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

import backend.core.lark_cli_exporter as cli
from backend.core import feishu_sync, job_store, note_versions, output_folder
from backend.core.storage_paths import _artifact_storage_dir
from tests.test_feishu_chapter_update import URL, FakeDoc

NOTE = """# 访谈

## 一、开场

开场的话。

> [!note]- 补充
> 折叠着的补充。

## 二、框架

框架的话。

![架构图](/jobs/{task}/artifacts/frame?file=note_0001.jpg)

## 三、结尾

结尾的话，见[[#一、开场|第一章]]。
"""


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda task_id, delay=None: None)
    # The output folder's own delayed refresh would fire a minute later, in
    # the middle of other tests, and save these notes again.
    monkeypatch.setattr(output_folder, "schedule_sync_when_quiet", lambda task_id: None)


_CREATED: list[str] = []


@pytest.fixture
def doc(monkeypatch):
    doc = FakeDoc()
    real = cli.export_markdown_via_lark_cli
    monkeypatch.setattr(cli, "export_markdown_via_lark_cli", lambda *a, **k: real(*a, **{**k, "runner": doc}))
    yield doc
    # Notes these tests edit would otherwise count as the user's own edits for
    # every later test that reads them (the note style loop reads eight at a time).
    job_store.delete_jobs(_CREATED)
    _CREATED.clear()


def _task(doc: FakeDoc, tmp_path: Path) -> tuple[str, Path]:
    task_id = f"fp-{uuid.uuid4().hex[:8]}"
    _CREATED.append(task_id)
    frames = _artifact_storage_dir() / task_id / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "note_0001.jpg").write_bytes(b"jpeg")
    note = NOTE.format(task=task_id)
    response = cli.export_markdown_via_lark_cli("访谈", note, task_id=task_id, artifact_root=_artifact_storage_dir())
    md = tmp_path / "访谈" / "访谈_笔记.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "summary_markdown": note, "summary_status": "completed", "lark_doc_title": "访谈",
        "lark_response": response, "output_folder": {"md_path": str(md)},
    })
    return task_id, md


def _note(task_id: str) -> str:
    return job_store.get_job(task_id)["result"]["summary_markdown"]


def _pull(task_id: str, doc: FakeDoc) -> str | None:
    return feishu_sync.pull(task_id, modified_at=doc.modified, runner=doc)


def test_a_chapter_edited_in_feishu_comes_back_and_the_others_keep_their_text(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    before = _note(task)
    doc.edit("框架的话。", "框架的话，在飞书里补了一句。")

    assert _pull(task, doc) == "pulled"

    after = _note(task)
    assert "框架的话，在飞书里补了一句。" in after
    opening = before[before.index("## 一、开场"):before.index("## 二、框架")]
    ending = before[before.index("## 三、结尾"):]
    assert opening in after, "the folded callout and all of chapter one stay as the note wrote them"
    assert ending.strip() in after


def test_what_comes_back_is_written_the_way_the_note_writes_it(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    doc.add_after("框架的话。", '<callout emoji="❓">\n飞书里问的问题  \n回答一行\n</callout>')
    doc.edit("框架的话。", f"框架的话 a\\*b，见[第三章]({URL}#{_heading_id(doc, '三、结尾')})。")

    _pull(task, doc)

    after = _note(task)
    assert "> [!question] 飞书里问的问题\n> 回答一行" in after
    assert "a*b" in after and "\\*" not in after
    assert "[[#三、结尾|第三章]]" in after
    assert f"![架构图](/jobs/{task}/artifacts/frame?file=note_0001.jpg)" in after
    assert "feishu.cn/file" not in after and "图 1：架构图" not in after


def test_a_picture_added_in_feishu_is_fetched_into_the_screenshots(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    at = next(i for i, b in enumerate(doc.blocks) if b.get("text") == "框架的话。") + 1
    doc.blocks.insert(at, {"tag": "img", "id": "blkNEW", "token": "newtok"})
    doc.modified += 100

    _pull(task, doc)

    after = _note(task)
    assert f"](/jobs/{task}/artifacts/frame?file=feishu_newtok.png)" in after
    assert (_artifact_storage_dir() / task / "frames" / "feishu_newtok.png").is_file()


def test_a_document_only_looked_at_leaves_the_note_alone(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    before = job_store.get_job(task)["result"]
    doc.modified += 100

    assert _pull(task, doc) == "nothing"

    after = job_store.get_job(task)["result"]
    assert after["summary_markdown"] == before["summary_markdown"]
    assert after["lark_response"]["sync"]["feishu_modified_at"] == doc.modified


def test_the_note_is_kept_before_it_is_replaced_and_not_pushed_back(doc, tmp_path):
    task, md = _task(doc, tmp_path)
    before = _note(task)
    doc.edit("结尾的话", "飞书里改的结尾")
    writes = len(doc.writes)

    _pull(task, doc)

    kept = [p.read_text(encoding="utf-8") for p in note_versions.versions(md)]
    assert before in kept
    assert feishu_sync.push(task) is None
    assert len(doc.writes) == writes, "nothing written back to Feishu"


def _edit_note(task: str, old: str, new: str) -> None:
    result = job_store.get_job(task)["result"]
    job_store.update_job_result(task, {**result, "summary_markdown": result["summary_markdown"].replace(old, new)})


def test_both_changed_in_different_chapters_keeps_both_everywhere(doc, tmp_path):
    task, md = _task(doc, tmp_path)
    _edit_note(task, "开场的话", "本地改的开场")
    doc.edit("结尾的话", "飞书里改的结尾")

    _pull(task, doc)

    assert "本地改的开场" in _note(task) and "飞书里改的结尾" in _note(task)
    texts = doc.texts()
    assert any("本地改的开场" in t for t in texts) and any("飞书里改的结尾" in t for t in texts)
    assert not list(md.parent.glob("访谈_笔记_*修改-*.md")), "nothing lost, nothing to keep beside"


def test_a_chapter_changed_on_both_sides_and_last_in_feishu_takes_feishus(doc, tmp_path):
    task, md = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    feishu_sync._NOTE_CHANGED_AT[task] = 0.0
    doc.edit("结尾的话", "飞书里改的结尾")

    _pull(task, doc)

    assert "飞书里改的结尾" in _note(task) and "本地改的结尾" not in _note(task)
    beside = list(md.parent.glob("访谈_笔记_本地修改-*.md"))
    assert beside and "本地改的结尾" in beside[0].read_text(encoding="utf-8")


def test_a_chapter_changed_on_both_sides_and_last_in_the_note_keeps_the_notes(doc, tmp_path):
    task, md = _task(doc, tmp_path)
    doc.edit("结尾的话", "飞书里改的结尾")
    _edit_note(task, "结尾的话", "本地改的结尾")
    feishu_sync._NOTE_CHANGED_AT[task] = time.time() + 10_000

    _pull(task, doc)

    assert "本地改的结尾" in _note(task)
    texts = doc.texts()
    assert any("本地改的结尾" in t for t in texts) and not any("飞书里改的结尾" in t for t in texts)
    beside = list(md.parent.glob("访谈_笔记_飞书修改-*.md"))
    assert beside and "飞书里改的结尾" in beside[0].read_text(encoding="utf-8")


def test_a_push_brings_in_feishus_edits_first_instead_of_burying_them(doc, tmp_path):
    """The owner edited in Obsidian while an edit made in Feishu was waiting to
    come back; the push recorded the document as pushed and the Feishu edit
    never reached the note."""
    task, _ = _task(doc, tmp_path)
    doc.edit("结尾的话", "飞书里改的结尾")
    _edit_note(task, "开场的话", "本地改的开场")

    feishu_sync.push(task, runner=doc)

    assert "飞书里改的结尾" in _note(task) and "本地改的开场" in _note(task)
    assert any("本地改的开场" in t for t in doc.texts())


def test_a_deleted_document_stops_the_syncing(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    doc.gone = True

    assert _pull(task, doc) is None

    result = job_store.get_job(task)["result"]
    assert "不再和它同步" in result["lark_sync_error"]
    assert feishu_sync.recorded_sync(result) is None
    assert task not in [row[0] for row in job_store.list_feishu_synced() if not row[3]]


def test_only_documents_changed_since_the_push_and_left_alone_are_pulled(doc, tmp_path, monkeypatch):
    task, _ = _task(doc, tmp_path)
    queued_pulls: list[str] = []
    monkeypatch.setattr(feishu_sync._WORKER, "submit", lambda fn, *a, **k: queued_pulls.append(a[0]))

    assert task not in feishu_sync.check_feishu(now=doc.modified + 1000, runner=doc), "only our own push so far"

    doc.edit("结尾的话", "飞书里改的结尾")
    assert task not in feishu_sync.check_feishu(now=doc.modified + 10, runner=doc), "still being edited"
    assert task in feishu_sync.check_feishu(now=doc.modified + feishu_sync.QUIET_SECONDS + 1, runner=doc)


def _heading_id(doc: FakeDoc, text: str) -> str:
    return next(b["id"] for b in doc.blocks if b["tag"].startswith("h") and b["text"] == text)


def test_a_push_cut_off_after_clearing_a_chapter_is_finished_not_pulled_back(doc, tmp_path):
    """2026-11-11, lesson three: a push deleted a chapter's paragraphs in
    Feishu, the write of the new ones timed out, and the next look took the
    emptied chapter for an edit made in Feishu, later than the note's, and
    emptied the note's chapter too."""
    task, md = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    doc.fail_insert = True

    feishu_sync.push(task, runner=doc)

    assert not any("结尾" in t for t in doc.texts() if "##" not in t and t != "三、结尾")
    assert job_store.get_job(task)["result"]["lark_sync_error"]
    doc.fail_insert = False
    feishu_sync._NOTE_CHANGED_AT[task] = 0.0  # the cut-off write is later than the note's edit
    _pull(task, doc)

    assert "本地改的结尾" in _note(task)
    assert any("本地改的结尾" in t for t in doc.texts())
    assert not list(md.parent.glob("访谈_笔记_本地修改-*.md"))
    result = job_store.get_job(task)["result"]
    assert not result["lark_sync_error"] and not result["lark_response"]["sync"].get("push_unfinished")


def test_a_cut_off_push_is_finished_even_when_the_note_is_back_to_what_was_last_pushed(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    doc.fail_insert = True
    feishu_sync.push(task, runner=doc)
    doc.fail_insert = False
    _edit_note(task, "本地改的结尾", "结尾的话")

    _pull(task, doc)

    assert any("结尾的话" in t for t in doc.texts())
    assert not job_store.get_job(task)["result"]["lark_response"]["sync"].get("push_unfinished")


def test_a_feishu_edit_to_another_chapter_still_comes_back_after_a_cut_off_push(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    doc.fail_insert = True
    feishu_sync.push(task, runner=doc)
    doc.fail_insert = False
    doc.edit("开场的话。", "飞书里改的开场。")

    _pull(task, doc)

    assert "飞书里改的开场。" in _note(task) and "本地改的结尾" in _note(task)
    texts = doc.texts()
    assert any("本地改的结尾" in t for t in texts) and any("飞书里改的开场。" in t for t in texts)


def test_a_chapter_removed_by_a_cut_off_push_comes_back_when_the_note_keeps_it(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    full = _note(task)
    ending = full[full.index("## 三、结尾"):]
    _edit_note(task, ending, "")
    _edit_note(task, "开场的话", "本地改的开场")
    doc.fail_insert = True
    feishu_sync.push(task, runner=doc)  # the ending is deleted, the opening's new words never written
    doc.fail_insert = False
    result = job_store.get_job(task)["result"]
    job_store.update_job_result(task, {**result, "summary_markdown": full})  # the note is back as last pushed

    _pull(task, doc)

    assert "三、结尾" in doc.texts() and any("结尾的话" in t for t in doc.texts())
    assert any(t.startswith("开场的话") for t in doc.texts())
    assert "## 三、结尾" in _note(task)


def test_a_chapter_written_before_the_answer_timed_out_is_not_written_twice(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "## 三、结尾", "## 四、新加的一章\n\n新章的话。\n\n## 三、结尾")
    doc.fail_insert = "after_writing"
    feishu_sync.push(task, runner=doc)
    doc.fail_insert = False

    _pull(task, doc)

    assert doc.texts().count("四、新加的一章") == 1
    assert sum("新章的话。" in t for t in doc.texts()) == 1
    assert _note(task).count("新章的话。") == 1


def test_a_chapter_moved_by_a_cut_off_push_is_not_lost(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    full = _note(task)
    opening = full[full.index("## 一、开场"):full.index("## 二、框架")]
    moved = full.replace(opening, "").rstrip("\n") + "\n\n" + opening
    result = job_store.get_job(task)["result"]
    job_store.update_job_result(task, {**result, "summary_markdown": moved})
    doc.fail_insert = True
    feishu_sync.push(task, runner=doc)
    doc.fail_insert = False

    _pull(task, doc)

    assert "## 一、开场" in _note(task) and "开场的话。" in _note(task)
    assert any("开场的话" in t for t in doc.texts())


def test_a_push_cut_off_before_feishu_changed_is_tried_again_by_the_watch(doc, tmp_path, monkeypatch):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    monkeypatch.setattr(cli, "_run", _cut_before_writing(cli._run))
    feishu_sync.push(task, runner=doc)
    monkeypatch.undo()
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    ran: list = []
    monkeypatch.setattr(feishu_sync._WORKER, "submit", lambda fn, *a, **k: ran.append(fn(*a, **k)))

    assert task in feishu_sync.check_feishu(now=doc.modified + 1000, runner=doc)
    assert any("本地改的结尾" in t for t in doc.texts())
    assert not job_store.get_job(task)["result"]["lark_response"]["sync"].get("push_unfinished")


def _cut_before_writing(real):
    def run(runner, cmd, **kwargs):
        if "block_delete" in cmd or "block_insert_after" in cmd:
            raise cli.LarkCliError("request timed out")
        return real(runner, cmd, **kwargs)
    return run


def test_a_feishu_edit_to_a_chapter_of_the_same_name_still_comes_back(doc, tmp_path):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "## 三、结尾", "## 小结\n\n第一个小结。\n\n## 三、结尾")
    _edit_note(task, "结尾的话", "结尾的话\n\n## 小结\n\n第二个小结。")
    feishu_sync.push(task, runner=doc)
    _edit_note(task, "第一个小结。", "本地改的第一个小结。")
    doc.fail_insert = True
    feishu_sync.push(task, runner=doc)
    doc.fail_insert = False
    doc.edit("第二个小结。", "飞书里改的第二个小结。")

    _pull(task, doc)

    assert "本地改的第一个小结。" in _note(task) and "飞书里改的第二个小结。" in _note(task)


def test_the_retry_mark_stays_until_the_finishing_push_succeeds(doc, tmp_path, monkeypatch):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    doc.fail_insert = True
    feishu_sync.push(task, runner=doc)
    doc.fail_insert = False
    monkeypatch.setattr(feishu_sync, "push", lambda *a, **k: None)  # the finishing push never gets going

    _pull(task, doc)

    assert job_store.get_job(task)["result"]["lark_response"]["sync"].get("push_unfinished")


def test_a_cut_off_push_waits_while_feishu_is_still_being_edited(doc, tmp_path, monkeypatch):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    doc.fail_insert = True
    feishu_sync.push(task, runner=doc)
    doc.fail_insert = False
    doc.edit("开场的话。", "飞书里正在改的开场。")
    queued: list = []
    monkeypatch.setattr(feishu_sync._WORKER, "submit", lambda fn, *a, **k: queued.append(fn))

    assert task not in feishu_sync.check_feishu(now=doc.modified + 5, runner=doc)
    assert not queued


def test_a_push_that_cannot_ask_feishu_when_it_changed_writes_nothing_says_so_and_is_tried_again(doc, tmp_path, monkeypatch):
    """2026-11-11: the Feishu login ran out; the restored chapter of lesson
    three never reached Feishu, the task showed no error, and nothing tried
    again after the login was renewed."""
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    writes = len(doc.writes)
    real = FakeDoc.__call__
    expired = {"on": True}

    def login(self, cmd, **kwargs):
        if expired["on"]:
            from tests.test_feishu_chapter_update import _fail
            return _fail("need_user_authorization")
        return real(self, cmd, **kwargs)

    monkeypatch.setattr(FakeDoc, "__call__", login)
    assert feishu_sync.push(task, runner=doc) is None
    assert len(doc.writes) == writes
    assert "need_user_authorization" in job_store.get_job(task)["result"]["lark_sync_error"]

    expired["on"] = False
    monkeypatch.setattr(feishu_sync._WORKER, "submit", lambda fn, *a, **k: fn(*a, **k))
    assert task in feishu_sync.check_feishu(now=doc.modified + 1000, runner=doc)

    assert any("本地改的结尾" in t for t in doc.texts())
    assert not job_store.get_job(task)["result"]["lark_sync_error"]


def test_a_push_that_breaks_unexpectedly_still_says_so(doc, tmp_path, monkeypatch):
    task, _ = _task(doc, tmp_path)
    _edit_note(task, "结尾的话", "本地改的结尾")
    monkeypatch.setattr(feishu_sync, "_with_sections", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))

    assert feishu_sync.push(task, runner=doc) is None
    assert "boom" in job_store.get_job(task)["result"]["lark_sync_error"]
