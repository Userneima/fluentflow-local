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
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

import backend.core.lark_cli_exporter as cli
from backend.core import feishu_sync, job_store, note_versions
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


@pytest.fixture
def doc(monkeypatch):
    doc = FakeDoc()
    real = cli.export_markdown_via_lark_cli
    monkeypatch.setattr(cli, "export_markdown_via_lark_cli", lambda *a, **k: real(*a, **{**k, "runner": doc}))
    return doc


def _task(doc: FakeDoc, tmp_path: Path) -> tuple[str, Path]:
    task_id = f"fp-{uuid.uuid4().hex[:8]}"
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


def test_both_changed_and_feishu_later_takes_feishus_and_keeps_the_note_beside(doc, tmp_path):
    task, md = _task(doc, tmp_path)
    result = job_store.get_job(task)["result"]
    job_store.update_job_result(task, {**result, "summary_markdown": _note(task).replace("开场的话", "本地改的开场"),
                                       "summary_edited_at": "2020-01-01T00:00:00+00:00"})
    feishu_sync._NOTE_CHANGED_AT[task] = 0.0
    doc.edit("结尾的话", "飞书里改的结尾")

    _pull(task, doc)

    assert "飞书里改的结尾" in _note(task) and "本地改的开场" not in _note(task)
    beside = list(md.parent.glob("访谈_笔记_本地修改-*.md"))
    assert beside and "本地改的开场" in beside[0].read_text(encoding="utf-8")


def test_both_changed_and_the_note_later_keeps_the_note_everywhere(doc, tmp_path):
    task, md = _task(doc, tmp_path)
    doc.edit("结尾的话", "飞书里改的结尾")
    result = job_store.get_job(task)["result"]
    job_store.update_job_result(task, {**result, "summary_markdown": _note(task).replace("开场的话", "本地改的开场")})
    feishu_sync._NOTE_CHANGED_AT[task] = time.time() + 10_000

    assert _pull(task, doc) == "kept_note"

    assert "本地改的开场" in _note(task) and "飞书里改的结尾" not in _note(task)
    texts = doc.texts()
    assert any("本地改的开场" in t for t in texts)
    assert not any("飞书里改的结尾" in t for t in texts), "the chapter edited in Feishu gets the note's text"
    assert any("结尾的话" in t for t in texts)
    beside = list(md.parent.glob("访谈_笔记_飞书修改-*.md"))
    assert beside and "飞书里改的结尾" in beside[0].read_text(encoding="utf-8")


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
