"""A note already in Feishu is updated there chapter by chapter.

Requirements (owner, 2026-10-21):
1. Exporting a note that already has a Feishu document updates that document;
   no second document is created.
2. Only the chapters that changed are rewritten. The others keep their blocks
   (and so their screenshots and any comments on them); an edited chapter keeps
   its heading, so links to it keep working.
3. Adding, removing or renaming a chapter leaves the document in the note's
   order with the note's words.
4. Before anything in the document is replaced, the document as it is is kept;
   if it cannot be read, nothing is written.
5. Nothing changed, nothing written.
6. A note kept in Feishu is pushed when it changes; a note that is not, is not.
7. (owner, 2026-10-24) A push whose chapters cannot be read back afterwards
   does not end the sync: the task says so, and the next change still updates
   the same document chapter by chapter, with no second document. If the
   chapters still cannot be read, nothing is written and the task says why.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import uuid

import pytest
from typing import Callable

import backend.core.lark_cli_exporter as cli
import backend.core.lark_cli_images as lark_cli_images
import backend.core.lark_cli_run as lark_cli_run
from backend.core import feishu_sync, job_store, note_versions

DOC = "doxDOC"
URL = "https://example.feishu.cn/docx/doxDOC"


def _ok(data: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user", "data": data}), "")


def _fail(message: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", json.dumps({"ok": False, "error": {"type": "api_error", "message": message}}))


def _flag(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


class FakeDoc:
    """A Feishu document as lark-cli shows it: top-level blocks with ids. A
    callout (``<callout>`` in the markdown written) is one top-level block
    holding its lines as blocks of their own (``children``)."""

    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.revision = 1
        self.writes: list[str] = []
        self.calls: list[list[str]] = []
        self.fail_markdown_fetch = False
        self.fail_outline = False
        self.fail_insert = False
        self.gone = False
        # Feishu showing a picture block without its file name (only one
        # document was ever checked to carry it).
        self.nameless_pictures = False
        self.modified = 1700000000
        self._next = 0

    def _id(self) -> str:
        self._next += 1
        return f"blk{self._next}"

    def _parse(self, markdown: str) -> list[dict]:
        blocks = []
        for piece in re.split(r"(?ms)(^[ \t]*<callout\b[^>]*>[ \t]*\n.*?^[ \t]*</callout>[ \t]*$)", markdown):
            if piece.lstrip().startswith("<callout"):
                lines = piece.strip().split("\n")
                inside = [line for line in lines[1:-1] if line.strip()]
                blocks.append({"tag": "callout", "id": self._id(), "text": "", "open": lines[0].strip(),
                               "children": self._flat("\n\n".join(inside))})
            else:
                blocks.extend(self._flat(piece))
        return blocks

    def _flat(self, markdown: str) -> list[dict]:
        blocks = []
        for chunk in re.split(r"\n\s*\n", markdown.strip()):
            chunk = chunk.strip()
            if not chunk:
                continue
            heading = re.match(r"^(#{1,9})\s+(.*)$", chunk)
            if chunk == "---":
                blocks.append({"tag": "hr", "id": self._id(), "text": ""})
            elif chunk == "<p></p>":
                blocks.append({"tag": "p", "id": self._id(), "text": ""})
            elif heading:
                blocks.append({"tag": f"h{len(heading.group(1))}", "id": self._id(), "text": heading.group(2)})
            elif re.match(r"^!\[[^\]]*\]\(https?://\S+\)$", chunk):
                # Feishu fetches a web picture itself; it shows no file name.
                block_id = self._id()
                blocks.append({"tag": "img", "id": block_id, "name": "", "token": f"web{block_id}", "text": ""})
            else:
                blocks.append({"tag": "p", "id": self._id(), "text": chunk})
        return blocks

    def walk(self) -> list[dict]:
        """Every block, those inside a callout too, in document order."""
        return [inner for b in self.blocks for inner in [b, *b.get("children", [])]]

    def texts(self) -> list[str]:
        """The words of the document, without its pictures, dividers and empty paragraphs."""
        return [b["text"].lstrip("\u3000") for b in self.walk() if b["tag"] not in ("img", "hr") and b["text"]]

    def markdown(self) -> str:
        """The document as lark-cli's markdown export shows it."""
        def one(b: dict) -> str:
            if b["tag"] == "callout":
                return b["open"] + "\n" + "\n".join(one(c) for c in b["children"]) + "\n</callout>"
            if b["tag"] == "img":
                return f"![](https://feishu.cn/file/{b['token']})"
            if b["tag"] == "hr":
                return "---"
            if b["tag"].startswith("h"):
                return "#" * int(b["tag"][1:]) + " " + b["text"]
            return b["text"]
        return "\n\n".join(["<title>t</title>", *(one(b) for b in self.blocks)]) + "\n"

    def edit(self, old: str, new: str) -> None:
        """An edit made in Feishu."""
        hits = [b for b in self.walk() if old in b.get("text", "")]
        assert len(hits) == 1, old
        hits[0]["text"] = hits[0]["text"].replace(old, new)
        self.revision += 1
        self.modified += 100

    def add_after(self, text: str, markdown: str) -> None:
        at = next(i for i, b in enumerate(self.blocks) if text in b.get("text", "")) + 1
        self.blocks[at:at] = self._parse(markdown)
        self.revision += 1
        self.modified += 100

    def _xml(self) -> str:
        def one(b: dict) -> str:
            if b["tag"] == "callout":
                return f'<callout id="{b["id"]}" emoji="bulb">' + "".join(one(c) for c in b["children"]) + "</callout>"
            if b["tag"] == "img":
                return f'<img id="{b["id"]}" name="{b.get("name", "")}" src="{b["token"]}"/>'
            if b["tag"] == "hr":
                return f'<hr id="{b["id"]}"/>'
            return f'<{b["tag"]} id="{b["id"]}">{html.escape(b["text"])}</{b["tag"]}>'
        return f'<title id="{DOC}">t</title>' + "".join(one(b) for b in self.blocks)

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self.gone:
            return _fail("Document page has been deleted. This page can no longer be edited")
        if cmd[1:3] == ["drive", "metas"]:
            return _ok({"metas": [{"doc_token": DOC, "latest_modify_time": str(self.modified)}]})
        verb = cmd[2]
        if verb == "+media-insert":
            # lark-cli puts the picture above the top-level block holding the
            # selection: above a callout, not inside it (its --help, 1.0.70).
            marker = _flag(cmd, "--selection-with-ellipsis")
            at = next(i for i, b in enumerate(self.blocks)
                      if any(marker in inner.get("text", "") for inner in [b, *b.get("children", [])]))
            block = {"tag": "img", "id": self._id(), "token": f"tok{self._next}",
                     "name": "" if self.nameless_pictures else _flag(cmd, "--file")}
            self.blocks.insert(at, block)
            self.modified += 1
            return _ok({"block_id": block["id"], "file_token": block["token"], "document_id": DOC})
        if verb == "+media-download":
            from pathlib import Path
            name = _flag(cmd, "--output") + ".png"
            (Path(kwargs["cwd"]) / name).write_bytes(b"png")
            return _ok({"saved_path": str(Path(kwargs["cwd"]) / name), "content_type": "image/png"})
        if verb == "+create":
            self.blocks = self._parse(kwargs.get("input") or "")
            self.revision += 1
            self.writes.append("create")
            return _ok({"document": {"document_id": DOC, "url": URL, "revision_id": self.revision}})
        if verb == "+fetch":
            if "--scope" in cmd and self.fail_outline:
                return _fail("request timed out")
            if "--doc-format" in cmd and _flag(cmd, "--doc-format") == "markdown":
                if self.fail_markdown_fetch:
                    return _fail("cannot read")
                return _ok({"document": {"content": self.markdown(), "revision_id": self.revision}})
            return _ok({"document": {"document_id": DOC, "content": self._xml(), "revision_id": self.revision}})
        if verb == "+update":
            command = _flag(cmd, "--command")
            if command == "block_insert_after" and self.fail_insert is True:
                return _fail("request timed out")
            self.writes.append(command)
            self.revision += 1
            self.modified += 1
            if command == "block_delete":
                gone = set(_flag(cmd, "--block-id").split(","))
                self.blocks = [b for b in self.blocks if b["id"] not in gone]
            elif command == "block_insert_after":
                if not (kwargs.get("input") or "").strip():
                    return _fail("--command block_insert_after requires --content")
                anchor = _flag(cmd, "--block-id")
                new = self._parse(kwargs.get("input") or "")
                at = 0 if anchor == DOC else next(i for i, b in enumerate(self.blocks) if b["id"] == anchor) + 1
                self.blocks[at:at] = new
                if self.fail_insert == "after_writing":
                    return _fail("request timed out")
            elif command == "str_replace":
                pattern, content = _flag(cmd, "--pattern"), _flag(cmd, "--content")
                hits = [b for b in self.walk() if pattern in b.get("text", "")]
                if len(hits) != 1:
                    return _ok({"document": {"revision_id": self.revision}, "result": "failed"})
                hits[0]["text"] = hits[0]["text"].replace(pattern, content)
            return _ok({"document": {"revision_id": self.revision}, "result": "success"})
        raise AssertionError(f"unexpected command {cmd}")


@pytest.fixture(autouse=True)
def _cli_on_path(monkeypatch):
    monkeypatch.setattr(lark_cli_run, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")


NOTE = """# 访谈

## 一、开场

开场的话。

## 二、框架

框架的话，见[[#三、结尾|第三章]]。

## 三、结尾

结尾的话。
"""


def _export(doc: FakeDoc, note: str, previous=None, kept=None) -> dict:
    keep = kept.append if kept is not None else None
    return cli.export_markdown_via_lark_cli("访谈", note, previous=previous, keep_feishu=keep, runner=doc)


def _ids(doc: FakeDoc) -> dict[str, str]:
    return {b["text"]: b["id"] for b in doc.blocks}


def _first(doc: FakeDoc) -> dict:
    return _export(doc, NOTE)["sync"]


def test_an_edited_chapter_is_rewritten_and_the_others_keep_their_blocks():
    doc = FakeDoc()
    sync = _first(doc)
    before = _ids(doc)

    result = _export(doc, NOTE.replace("框架的话", "框架的话，加了一句问题"), previous=sync, kept=[])

    assert "create" not in doc.writes[1:], "the same document is updated"
    assert result["url"] == URL and result["updated"] is True and result["sections_rewritten"] == 1
    after = _ids(doc)
    for kept in ("访谈", "一、开场", "开场的话。", "三、结尾", "结尾的话。"):
        assert after[kept] == before[kept], f"{kept} was not touched"
    assert after["二、框架"] == before["二、框架"], "the edited chapter keeps its heading"
    assert any("加了一句问题" in text for text in doc.texts())


def test_the_link_in_a_rewritten_chapter_points_at_its_chapter_again():
    doc = FakeDoc()
    sync = _first(doc)
    heading = _ids(doc)["三、结尾"]

    _export(doc, NOTE.replace("框架的话", "框架改过的话"), previous=sync, kept=[])

    framework = next(t for t in doc.texts() if "框架改过的话" in t)
    assert f"[第三章]({URL}#{heading})" in framework


@pytest.mark.parametrize("edit, expected", [
    (lambda n: n.replace("## 三、结尾", "## 二点五、插一章\n\n新章的话。\n\n## 三、结尾"),
     ["访谈", "一、开场", "开场的话。", "二、框架", None, "二点五、插一章", "新章的话。", "三、结尾", "结尾的话。"]),
    (lambda n: n.replace("## 一、开场\n\n开场的话。\n\n", ""),
     ["访谈", "二、框架", None, "三、结尾", "结尾的话。"]),
    (lambda n: n.replace("## 一、开场", "## 一、改名的开场"),
     ["访谈", "一、改名的开场", "开场的话。", "二、框架", None, "三、结尾", "结尾的话。"]),
    (lambda n: "前言一段。\n\n" + n,
     ["前言一段。", "访谈", "一、开场", "开场的话。", "二、框架", None, "三、结尾", "结尾的话。"]),
])
def test_chapters_added_removed_or_renamed_leave_the_note_s_order(edit, expected):
    doc = FakeDoc()
    sync = _first(doc)

    _export(doc, edit(NOTE), previous=sync, kept=[])

    texts = doc.texts()
    assert len(texts) == len(expected)
    for text, want in zip(texts, expected):
        if want is not None:
            assert text == want


def test_the_document_is_kept_before_anything_in_it_is_replaced():
    doc = FakeDoc()
    sync = _first(doc)
    doc.blocks[2]["text"] = "在飞书里改过的开场。"
    kept: list[str] = []
    writes_before = len(doc.writes)

    _export(doc, NOTE.replace("结尾的话", "新结尾"), previous=sync, kept=kept)

    assert kept and "在飞书里改过的开场。" in kept[0]
    assert len(doc.writes) > writes_before


def test_nothing_is_written_when_the_document_cannot_be_kept_first():
    doc = FakeDoc()
    sync = _first(doc)
    doc.fail_markdown_fetch = True
    writes_before = len(doc.writes)

    with pytest.raises(cli.LarkCliError):
        _export(doc, NOTE.replace("结尾的话", "新结尾"), previous=sync, kept=[])
    assert len(doc.writes) == writes_before


def test_nothing_changed_nothing_written():
    doc = FakeDoc()
    sync = _first(doc)
    writes_before = len(doc.writes)
    kept: list[str] = []

    result = _export(doc, NOTE, previous=sync, kept=kept)

    assert len(doc.writes) == writes_before and not kept
    assert result["sections_rewritten"] == 0


# ── the task ────────────────────────────────────────────────────────────────

def _task(sync: dict | None, note: str = NOTE) -> str:
    task_id = f"fs-{uuid.uuid4().hex[:8]}"
    response = {"url": URL, "via": "lark_cli", "sync": sync} if sync else {"url": URL, "via": "lark_openapi"}
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "summary_markdown": note, "summary_status": "completed", "lark_response": response, "lark_doc_title": "访谈",
    })
    return task_id


def test_a_note_kept_in_feishu_is_pushed_when_it_changes_and_others_are_not(monkeypatch):
    scheduled: list[str] = []
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda task_id, delay=None: scheduled.append(task_id))
    doc = FakeDoc()
    synced = _task(_first(doc))
    plain = _task(None)

    for task_id in (synced, plain):
        result = job_store.get_job(task_id)["result"]
        job_store.update_job_result(task_id, {**result, "summary_markdown": NOTE + "\n补一句。\n"})

    assert scheduled == [synced]


def test_a_push_updates_the_document_and_records_it(monkeypatch, tmp_path):
    doc = FakeDoc()
    task_id = _task(_first(doc))
    monkeypatch.setattr(lark_cli_run, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    real = cli.export_markdown_via_lark_cli
    monkeypatch.setattr(cli, "export_markdown_via_lark_cli", lambda *a, **k: real(*a, **{**k, "runner": doc}))
    md = tmp_path / "访谈" / "访谈_笔记.md"
    md.parent.mkdir()
    result = job_store.get_job(task_id)["result"]
    job_store.save_result_changes(task_id, result, {**result, "output_folder": {"md_path": str(md)}})
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda task_id, delay=None: None)
    result = job_store.get_job(task_id)["result"]
    job_store.update_job_result(task_id, {**result, "summary_markdown": NOTE.replace("结尾的话", "新结尾")})

    response = feishu_sync.push(task_id, runner=doc)

    assert response and response["url"] == URL
    stored = job_store.get_job(task_id)["result"]
    assert stored["lark_response"]["sync"]["revision_id"] == doc.revision
    assert stored["lark_response"]["sync"]["note_digest"] == cli.note_digest(stored["summary_markdown"])
    assert any("新结尾" in t for t in doc.texts())
    # Nobody edited the document in Feishu: it is the note as pushed, and no
    # Feishu-side copy is kept (tests/test_note_changes.py has both cases).
    assert not [path for path in note_versions.versions(md) if note_versions.FEISHU in path.name]
    assert feishu_sync.push(task_id, runner=doc) is None, "nothing left to push"


def test_a_chapter_whose_body_shows_nothing_in_feishu_is_not_written_empty():
    doc = FakeDoc()
    note = "# 访谈\n\n## 一、开场\n\n<!-- 第 1/2 部分 -->\n\n### （一）框架\n\n框架的话。\n"
    sync = _export(doc, note)["sync"]
    forced = {**sync, "sections": [{**s, "digest": None} for s in sync["sections"]]}

    _export(doc, note, previous=forced, kept=[])

    assert "框架的话。" in doc.texts()


# ── chapters not read back after a push ─────────────────────────────────────

def _export_kept(doc: FakeDoc, note: str, root, task_id: str, previous=None) -> dict:
    return cli.export_markdown_via_lark_cli(
        "访谈", note, task_id=task_id, artifact_root=root, previous=previous, keep_feishu=lambda _t: None, runner=doc,
    )


def test_a_push_whose_chapters_were_not_read_back_is_followed_by_an_update_in_place(tmp_path):
    doc = FakeDoc()
    sync = _export_kept(doc, NOTE, tmp_path, "t1")["sync"]
    doc.fail_outline = True
    sync = _export_kept(doc, NOTE.replace("开场的话", "开场改过"), tmp_path, "t1", previous=sync)["sync"]
    assert sync["sections"] is None
    before = _ids(doc)
    doc.fail_outline = False

    result = _export_kept(doc, NOTE.replace("开场的话", "开场改过").replace("结尾的话", "新结尾"), tmp_path, "t1", previous=sync)

    assert doc.writes.count("create") == 1, "the same document is updated"
    assert result["sections_rewritten"] == 1, "only the chapter changed since the last push"
    after = _ids(doc)
    for kept in ("访谈", "一、开场", "开场改过。", "二、框架", "三、结尾"):
        assert after[kept] == before[kept], f"{kept} was not touched"
    assert "新结尾。" in doc.texts()
    assert isinstance(result["sync"]["sections"], list)


def test_chapters_that_still_cannot_be_read_mean_nothing_is_written(tmp_path):
    doc = FakeDoc()
    sync = _export_kept(doc, NOTE, tmp_path, "t2")["sync"]
    stuck = {**sync, "sections": None}
    doc.fail_outline = True
    writes_before = len(doc.writes)

    with pytest.raises(cli.LarkCliError):
        _export_kept(doc, NOTE.replace("结尾的话", "新结尾"), tmp_path, "t2", previous=stuck)
    assert len(doc.writes) == writes_before, "no second document, nothing written"


def _synced_task(monkeypatch, tmp_path, doc: FakeDoc, sync: dict) -> str:
    monkeypatch.setattr(cli, "export_markdown_via_lark_cli",
                        lambda *a, real=cli.export_markdown_via_lark_cli, **k: real(*a, **{**k, "runner": doc}))
    monkeypatch.setattr("backend.core.storage_paths._artifact_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(feishu_sync, "_cli", lambda: (cli, "/usr/local/bin/lark-cli"))
    task_id = _task(sync)
    cli.save_pushed_note(tmp_path, task_id, NOTE)
    return task_id


def test_a_note_whose_chapters_were_not_read_back_stays_in_sync_and_is_pushed(monkeypatch, tmp_path):
    scheduled: list[str] = []
    doc = FakeDoc()
    sync = _export_kept(doc, NOTE, tmp_path, "seed")["sync"]
    task_id = _synced_task(monkeypatch, tmp_path, doc, {**sync, "sections": None})
    assert feishu_sync.recorded_sync(job_store.get_job(task_id)["result"]) is not None
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda t, delay=None: scheduled.append(t))
    result = job_store.get_job(task_id)["result"]
    job_store.update_job_result(task_id, {**result, "summary_markdown": NOTE.replace("结尾的话", "新结尾")})
    assert scheduled == [task_id], "a change is still pushed"

    feishu_sync.push(task_id, runner=doc)

    stored = job_store.get_job(task_id)["result"]
    assert doc.writes.count("create") == 1
    assert "新结尾。" in doc.texts()
    assert isinstance(stored["lark_response"]["sync"]["sections"], list)
    assert not stored.get("lark_sync_error")


def test_a_push_that_cannot_read_its_chapters_back_says_so_on_the_task(monkeypatch, tmp_path):
    doc = FakeDoc()
    sync = _export_kept(doc, NOTE, tmp_path, "seed")["sync"]
    task_id = _synced_task(monkeypatch, tmp_path, doc, sync)
    result = job_store.get_job(task_id)["result"]
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda t, delay=None: None)
    job_store.update_job_result(task_id, {**result, "summary_markdown": NOTE.replace("结尾的话", "新结尾")})
    doc.fail_outline = True

    feishu_sync.push(task_id, runner=doc)

    stored = job_store.get_job(task_id)["result"]
    assert "新结尾。" in doc.texts()
    assert "章节目录" in (stored.get("lark_sync_error") or "")
    assert feishu_sync.recorded_sync(stored) is not None, "still in sync"


def test_a_stopped_document_is_not_in_sync():
    assert feishu_sync.recorded_sync({"lark_response": {"via": "lark_cli", "sync": {
        "doc_id": DOC, "sections": None, "stopped": "飞书那份已被删除"}}}) is None


def test_an_export_cut_off_after_creating_its_document_writes_into_it_again(tmp_path, monkeypatch):
    """(owner, 2026-11-06) A document created by an export that was cut off
    before the task recorded it (the service stopped while screenshots
    uploaded) is written into again by the next export, not made twice."""
    doc = FakeDoc()

    def cut_off(*_args, **_kwargs):
        raise KeyboardInterrupt("the service stopped")

    monkeypatch.setattr(lark_cli_images, "_insert_images", cut_off)
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", NOTE, task_id="t1", artifact_root=tmp_path, runner=doc)
    monkeypatch.undo()
    monkeypatch.setattr(lark_cli_run, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    created = doc.writes.count("create")

    result = cli.export_markdown_via_lark_cli("访谈", NOTE, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert created == 1 and doc.writes.count("create") == 1, "no second document"
    assert result["url"] == URL
    heading = _ids(doc)["三、结尾"]
    framework = next(t for t in doc.texts() if "框架的话" in t)
    assert f"[第三章]({URL}#{heading})" in framework, "the links the cut-off export left are pointed at their chapters"
    assert not (tmp_path / "t1" / cli.CREATED).exists()
    cli.export_markdown_via_lark_cli("访谈", NOTE, task_id="t2", artifact_root=tmp_path, runner=doc)
    assert doc.writes.count("create") == 2, "another task still gets its own document"


PICTURED = """# 访谈

## 一、开场

开场的话。

![讲者打开设置页](/jobs/t1/artifacts/frame?file=a.jpg)

## 二、框架

框架的话。

![点开模型菜单](/jobs/t1/artifacts/frame?file=b.jpg)

中间一句。

![保存后的结果](/jobs/t1/artifacts/frame?file=c.jpg)
"""


def _cut_off_at(doc: FakeDoc, stop: "Callable[[list[str]], bool]"):
    """``doc``, except that the first command ``stop`` picks never runs: the
    service stopped there."""
    def runner(cmd, **kwargs):
        if stop(list(cmd)):
            raise KeyboardInterrupt("the service stopped")
        return doc(cmd, **kwargs)
    return runner


@pytest.mark.parametrize("cut_at", ["before_any_picture", "between_a_picture_and_its_marker"])
def test_screenshots_an_export_was_cut_off_before_go_in_when_it_is_carried_on(tmp_path, cut_at):
    """(owner, 2026-11-25) Carrying on an export cut off before its
    screenshots were all in puts each missing one right above its caption,
    none twice, leaves no marker in the document, and the task knows what
    every screenshot became in Feishu. The document's layout around a picture
    (``<img name=…>`` right above the marker paragraph) is as measured on a
    test document in the owner's Feishu."""
    frames = tmp_path / "t1" / "frames"
    frames.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (frames / name).write_bytes(b"\xff\xd8 jpeg")
    doc = FakeDoc()
    inserted: list[str] = []

    def stop(cmd: list[str]) -> bool:
        if cmd[2] == "+media-insert":
            if cut_at == "before_any_picture":
                return True
            inserted.append(_flag(cmd, "--file"))
        # Picture 2 is in; the service stops before its marker is removed.
        return cut_at != "before_any_picture" and cmd[2] == "+update" and inserted == ["a.jpg", "b.jpg"]

    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, stop))

    result = cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert doc.writes.count("create") == 1
    shown = [(b["tag"], b.get("name") or b["text"]) for b in doc.blocks if b["tag"] == "img" or "图 " in b["text"]]
    assert shown == [
        ("img", "a.jpg"), ("p", "图 1：讲者打开设置页"),
        ("img", "b.jpg"), ("p", "图 2：点开模型菜单"),
        ("img", "c.jpg"), ("p", "图 3：保存后的结果"),
    ], "each picture once, right above its caption"
    assert not any("FFIMG" in b.get("text", "") for b in doc.blocks), "no marker left in the document"
    assert result["image_count"] == 3 and result["image_upload_count"] == 3 and result["image_upload_errors"] == []
    by_name = {image["name"]: image for image in result["sync"]["images"]}
    tokens = {b["name"]: b["token"] for b in doc.blocks if b["tag"] == "img"}
    assert {name: image["file_token"] for name, image in by_name.items()} == tokens
    assert by_name["b.jpg"]["line"] == "![点开模型菜单](/jobs/t1/artifacts/frame?file=b.jpg)"
    assert not (tmp_path / "t1" / cli.CREATED).exists()


def test_a_picture_feishu_shows_without_its_name_is_not_put_in_twice(tmp_path):
    """(independent review, 2026-11-25) Telling that a picture is already in
    by the file name Feishu shows on it was checked on one document only.
    Cut off between inserting picture 2 and removing its marker, with Feishu
    not naming the picture: the picture right above a caption still led by
    its marker is that caption's, and is not inserted a second time."""
    frames = tmp_path / "t1" / "frames"
    frames.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (frames / name).write_bytes(b"\xff\xd8 jpeg")
    doc = FakeDoc()
    doc.nameless_pictures = True
    inserted: list[str] = []

    def stop(cmd: list[str]) -> bool:
        if cmd[2] == "+media-insert":
            inserted.append(_flag(cmd, "--file"))
        return cmd[2] == "+update" and inserted == ["a.jpg", "b.jpg"]

    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, stop))

    result = cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path, runner=doc)

    shown = [b["tag"] if b["tag"] == "img" else b["text"] for b in doc.blocks if b["tag"] == "img" or "图 " in b["text"]]
    assert shown == ["img", "图 1：讲者打开设置页", "img", "图 2：点开模型菜单", "img", "图 3：保存后的结果"]
    assert not any("FFIMG" in b.get("text", "") for b in doc.blocks)
    assert result["image_upload_count"] == 3 and result["image_upload_errors"] == []
    assert [image["file_token"] for image in result["sync"]["images"]] == [
        b["token"] for b in doc.blocks if b["tag"] == "img"
    ]


# Independent review of the resume, 2026-11-25: every picture goes in once, at
# its own caption, also when the note was edited between the cut-off and the
# resume; nothing else in the document is taken for a screenshot; a resume can
# run again; what each screenshot became is never recorded wrong.


def _frames(tmp_path, names) -> None:
    frames = tmp_path / "t1" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for name in names:
        (frames / name).write_bytes(b"\xff\xd8 jpeg")


def _pic(name: str, alt: str = "") -> str:
    return f"![{alt}](/jobs/t1/artifacts/frame?file={name})"


def _cut_before_pictures(doc: FakeDoc, note: str, tmp_path) -> None:
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", note, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, lambda c: c[2] == "+media-insert"))


def _pictures(doc: FakeDoc) -> list[tuple[str, str]]:
    """Each picture in the document with the paragraph right below it."""
    return [(b.get("name"), doc.blocks[i + 1]["text"]) for i, b in enumerate(doc.blocks) if b["tag"] == "img"]


def _shown(doc: FakeDoc) -> list:
    """The document's pictures and words, without dividers and empty paragraphs."""
    return [("img", b.get("name")) if b["tag"] == "img" else b["text"] for b in doc.blocks
            if b["tag"] == "img" or b.get("text")]


def _recorded_as_shown(doc: FakeDoc, result: dict, names: list[str] | None = None) -> None:
    """Each screenshot recorded as the picture the document shows above its
    caption (``names``: the screenshots in document order, when Feishu does
    not name its pictures)."""
    shown = [b for b in doc.blocks if b["tag"] == "img"]
    names = names or [b["name"] for b in shown]
    recorded = {image["name"]: (image["file_token"], image["block_id"]) for image in result["sync"]["images"]}
    assert recorded == {name: (b["token"], b["id"]) for name, b in zip(names, shown)}, \
        "what each screenshot became is what the document shows"


EDITED = f"""# 访谈

## 一、开场

开场的话。

## 二、框架

{_pic('a.jpg')}

中间一句。

{_pic('b.jpg')}
"""


def test_a_note_edited_before_the_resume_gets_each_picture_at_its_own_caption(tmp_path):
    """Pictures without words get the same caption (``图 N：截图``), and a
    picture added to an earlier chapter numbers the later ones again: neither
    may put a picture at another one's caption."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "x.jpg"])
    doc = FakeDoc()
    _cut_before_pictures(doc, EDITED, tmp_path)
    edited = EDITED.replace("开场的话。", f"开场的话。\n\n{_pic('x.jpg')}")

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert _shown(doc) == [
        "访谈", "一、开场", "开场的话。", ("img", "x.jpg"), "图 1：截图",
        "二、框架", ("img", "a.jpg"), "图 1：截图", "中间一句。", ("img", "b.jpg"), "图 2：截图",
    ], "each picture right above its own caption (the chapter left alone keeps its numbers)"
    assert not any("FFIMG" in b.get("text", "") for b in doc.blocks)
    assert result["image_count"] == 3 and result["image_upload_count"] == 3 and result["image_upload_errors"] == []
    _recorded_as_shown(doc, result)


@pytest.mark.parametrize("nameless", [False, True])
def test_a_resume_cut_off_in_turn_finishes_on_the_next_and_then_writes_nothing(tmp_path, nameless):
    """The note edited, the resume cut off after it rewrote a chapter and put
    one picture in: the next export finishes the rest, each picture once, and
    one more export writes nothing. Also when Feishu shows the pictures
    without their names."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "x.jpg"])
    doc = FakeDoc()
    doc.nameless_pictures = nameless
    order = ["x.jpg", "a.jpg", "b.jpg"]
    _cut_before_pictures(doc, EDITED, tmp_path)
    edited = EDITED.replace("开场的话。", f"开场的话。\n\n{_pic('x.jpg')}")
    inserted: list[str] = []

    def stop(cmd: list[str]) -> bool:
        if cmd[2] == "+media-insert":
            inserted.append(_flag(cmd, "--file"))
        return cmd[2] == "+update" and inserted == ["x.jpg", "a.jpg"] and "FFIMG" in _flag(cmd, "--pattern")

    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, stop))
    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert [name for name, _below in _pictures(doc)] == ([""] * 3 if nameless else order)
    assert not any("FFIMG" in b.get("text", "") for b in doc.blocks)
    assert result["image_upload_count"] == 3 and result["image_upload_errors"] == []
    _recorded_as_shown(doc, result, order)
    assert not (tmp_path / "t1" / cli.CREATED).exists()

    calls = len(doc.calls)
    again = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path,
                                             previous=result["sync"], runner=doc)
    assert not any(c[2] == "+media-insert" or (c[2] == "+update" and _flag(c, "--command") != "str_replace")
                   for c in doc.calls[calls:])
    assert [name for name, _below in _pictures(doc)] == ([""] * 3 if nameless else order)
    _recorded_as_shown(doc, again, order)


def test_a_paragraph_reading_like_a_caption_is_not_taken_for_one(tmp_path):
    """A body paragraph whose words are a screenshot's description is not
    that screenshot's caption."""
    _frames(tmp_path, ["a.jpg"])
    note = f"# 访谈\n\n## 一、开场\n\n保存后的结果\n\n图 1：保存后的结果\n\n{_pic('a.jpg', '保存后的结果')}\n"
    doc = FakeDoc()
    _cut_before_pictures(doc, note, tmp_path)

    result = cli.export_markdown_via_lark_cli("访谈", note, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert _shown(doc) == ["访谈", "一、开场", "保存后的结果", "图 1：保存后的结果", ("img", "a.jpg"), "图 1：保存后的结果"]
    assert result["image_upload_count"] == 1 and result["image_upload_errors"] == []
    _recorded_as_shown(doc, result)


def test_a_picture_that_is_not_the_screenshot_is_not_taken_for_it(tmp_path):
    """A picture without a name (the note's web image, or one pasted in
    Feishu) followed by a divider and a heading, then a screenshot's
    caption: the block right above the caption is the heading, so the
    screenshot goes in and the other picture is not recorded as it."""
    _frames(tmp_path, ["a.jpg"])
    note = f"# 访谈\n\n## 一、开场\n\n开场的话。\n\nWEBIMG\n\n## 二、框架\n\n{_pic('a.jpg', '设置页')}\n"
    doc = FakeDoc()
    _cut_before_pictures(doc, note, tmp_path)
    for block in doc.blocks:
        if block["text"] == "WEBIMG":
            block.update({"tag": "img", "name": "", "token": "webtok", "text": ""})

    result = cli.export_markdown_via_lark_cli("访谈", note, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert ("a.jpg", "图 1：设置页") in _pictures(doc)
    assert [image["file_token"] for image in result["sync"]["images"]] != ["webtok"]
    assert result["image_upload_count"] == 1 and result["image_upload_errors"] == []
    (image,) = result["sync"]["images"]
    block = next(b for b in doc.blocks if b.get("name") == "a.jpg")
    assert (image["file_token"], image["block_id"]) == (block["token"], block["id"])


def test_a_screenshot_whose_file_is_gone_since_is_told_apart_from_one_in_feishu(tmp_path):
    """Cut off with picture 2 in and its marker still there; then the files
    of pictures 2 and 3 are deleted. Picture 2 is in Feishu: recorded with
    what it became, not as an error. Picture 3 cannot go in: listed as not
    uploaded, its marker taken out, its caption kept."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    doc = FakeDoc()
    inserted: list[str] = []

    def stop(cmd: list[str]) -> bool:
        if cmd[2] == "+media-insert":
            inserted.append(_flag(cmd, "--file"))
        return cmd[2] == "+update" and inserted == ["a.jpg", "b.jpg"]

    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, stop))
    (tmp_path / "t1" / "frames" / "b.jpg").unlink()
    (tmp_path / "t1" / "frames" / "c.jpg").unlink()

    result = cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert [name for name, _below in _pictures(doc)] == ["a.jpg", "b.jpg"]
    assert "图 3：保存后的结果" in doc.texts()
    assert not any("FFIMG" in b.get("text", "") for b in doc.blocks)
    assert result["image_count"] == 3 and result["image_upload_count"] == 2
    assert result["image_upload_errors"] == ["/jobs/t1/artifacts/frame?file=c.jpg"]
    by_name = {image["name"]: image for image in result["sync"]["images"]}
    assert by_name["c.jpg"]["file_token"] is None
    b = next(blk for blk in doc.blocks if blk.get("name") == "b.jpg")
    assert (by_name["b.jpg"]["file_token"], by_name["b.jpg"]["block_id"]) == (b["token"], b["id"])


def test_a_cut_off_recorded_by_an_older_version_still_gets_its_pictures(tmp_path):
    """A document recorded before the record kept what the export wrote: the
    note as pushed (saved before the record) says which picture each marker
    was made for, also when the note was edited since."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "x.jpg"])
    doc = FakeDoc()
    _cut_before_pictures(doc, EDITED, tmp_path)
    record = tmp_path / "t1" / cli.CREATED
    older = json.loads(record.read_text(encoding="utf-8"))
    older.pop("first_push")
    record.write_text(json.dumps(older), encoding="utf-8")
    edited = EDITED.replace("开场的话。", f"开场的话。\n\n{_pic('x.jpg')}")

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert [name for name, _below in _pictures(doc)] == ["x.jpg", "a.jpg", "b.jpg"]
    assert not any("FFIMG" in b.get("text", "") for b in doc.blocks)
    _recorded_as_shown(doc, result)


def test_a_record_whose_note_was_written_again_since_places_no_picture(tmp_path):
    """A record from a version that kept only the document, whose note was
    saved again by a resume that was cut off before it noted the resume: the
    note as pushed is not the one that export wrote, so its markers are
    matched to no picture. The screenshots it cannot place are reported as
    not uploaded, not put under another one's caption."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "c.jpg", "x.jpg"])
    doc = FakeDoc()
    _cut_before_pictures(doc, PICTURED, tmp_path)
    edited = PICTURED.replace("开场的话。", f"开场的话。\n\n{_pic('x.jpg')}")
    record = tmp_path / "t1" / cli.CREATED
    older = json.loads(record.read_text(encoding="utf-8"))
    older.pop("first_push")
    record.write_text(json.dumps(older), encoding="utf-8")
    cli.save_pushed_note(tmp_path, "t1", edited)
    os.utime(record, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    os.utime(tmp_path / "t1" / cli.PUSHED_NOTE, ns=(1_700_000_009_000_000_000, 1_700_000_009_000_000_000))

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert not [b for b in doc.blocks if b["tag"] == "img"], "no picture under another one's caption"
    assert not any("FFIMG" in b.get("text", "") for b in doc.blocks), "its markers are taken out"
    for caption in ("图 1：讲者打开设置页", "图 2：点开模型菜单", "图 3：保存后的结果"):
        assert caption in doc.texts(), "the caption stays as words"
    assert result["image_upload_count"] == 0
    assert result["image_upload_errors"] == [f"/jobs/t1/artifacts/frame?file={name}"
                                             for name in ("x.jpg", "a.jpg", "b.jpg", "c.jpg")]
    assert {image["file_token"] for image in result["sync"]["images"]} == {None}
    assert not (tmp_path / "t1" / cli.CREATED).exists()


def test_links_in_chapters_a_resume_leaves_alone_point_at_their_chapters(tmp_path):
    """Cut off before the chapter links were pointed, then the first chapter
    edited: the resume rewrites that chapter only, and the link the cut-off
    export left in the second points at its chapter, not at a placeholder."""
    doc = FakeDoc()
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", NOTE, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, lambda c: "--scope" in c))
    edited = NOTE.replace("开场的话。", "开场改过的话。")

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert result["sections_rewritten"] == 1
    framework = next(t for t in doc.texts() if "框架的话" in t)
    assert f"[第三章]({URL}#{_ids(doc)['三、结尾']})" in framework
    assert not any("fluentflow.invalid" in t for t in doc.texts())
    assert result["sync"]["link_errors"] == []


def test_a_resume_numbers_its_links_after_the_ones_the_document_still_holds(tmp_path):
    """Cut off before the chapter links were pointed, then the first chapter
    gains a link to the same heading the second one links to: the resume
    numbers its own placeholder after the first push's, so neither matches two
    places and both point at their chapter."""
    doc = FakeDoc()
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", NOTE, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, lambda c: "--scope" in c))
    edited = NOTE.replace("开场的话。", "开场的话，见[[#三、结尾|第三章]]。")

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    heading = _ids(doc)["三、结尾"]
    linked = [text for text in doc.texts() if "第三章" in text]
    assert len(linked) == 2 and all(f"[第三章]({URL}#{heading})" in text for text in linked), \
        "both links point at their chapter, the rewritten one's and the one left alone"
    assert not any("fluentflow.invalid" in text for text in doc.texts())
    assert result["sync"]["link_errors"] == []


def test_a_record_whose_note_s_time_cannot_be_told_from_the_record_s_places_nothing(tmp_path):
    """Equal file times (a coarse filesystem clock) cannot tell whether the
    note as pushed is the one that export wrote: it is taken as not, so no
    picture goes in and the screenshots are reported as not uploaded, rather
    than placed by a numbering that may be another note's."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "c.jpg", "x.jpg"])
    doc = FakeDoc()
    _cut_before_pictures(doc, PICTURED, tmp_path)
    edited = PICTURED.replace("开场的话。", f"开场的话。\n\n{_pic('x.jpg')}")
    record = tmp_path / "t1" / cli.CREATED
    older = json.loads(record.read_text(encoding="utf-8"))
    older.pop("first_push")
    record.write_text(json.dumps(older), encoding="utf-8")
    cli.save_pushed_note(tmp_path, "t1", edited)
    same = 1_700_000_000_000_000_000
    os.utime(record, ns=(same, same))
    os.utime(tmp_path / "t1" / cli.PUSHED_NOTE, ns=(same, same))

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert not [b for b in doc.blocks if b["tag"] == "img"]
    assert result["image_upload_count"] == 0
    assert result["image_upload_errors"] == [f"/jobs/t1/artifacts/frame?file={name}"
                                             for name in ("x.jpg", "a.jpg", "b.jpg", "c.jpg")]


def test_a_record_whose_note_is_not_the_one_it_wrote_points_no_link(tmp_path):
    """A record from a version that kept only the document, whose note was
    written again since: the placeholders left in the document were numbered
    by another note, so pointing one by this note's numbering can put a link
    on the wrong chapter. None is pointed; they are reported and left in
    sight."""
    doc = FakeDoc()
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", NOTE, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, lambda c: "--scope" in c))
    # The note a later resume saved: its first link is another chapter's, and
    # it reads as the placeholder the document still holds.
    edited = NOTE.replace("开场的话。", "开场的话，见[[#二、框架|第三章]]。")
    record = tmp_path / "t1" / cli.CREATED
    older = json.loads(record.read_text(encoding="utf-8"))
    older.pop("first_push")
    record.write_text(json.dumps(older), encoding="utf-8")
    cli.save_pushed_note(tmp_path, "t1", edited)
    os.utime(record, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    os.utime(tmp_path / "t1" / cli.PUSHED_NOTE, ns=(1_700_000_009_000_000_000, 1_700_000_009_000_000_000))

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=doc)

    framework = next(text for text in doc.texts() if "框架的话" in text)
    assert "fluentflow.invalid/link/1" in framework, "left as it was, in sight"
    assert f"#{_ids(doc)['二、框架']}" not in framework, "not pointed at the chapter this note's link 1 names"
    assert result["sync"]["link_errors"] == ["第三章"]


def test_a_resume_numbers_its_links_after_the_first_push_s_even_when_that_chapter_is_gone(tmp_path):
    """The first push put its second link in a chapter a resume since deleted,
    and that resume saved the note without noting the record: the next resume
    numbers its own link after both what the document still holds and what
    the first push used, so it never takes a number of the first push's."""
    note = """# 访谈

## 一、开场

开场的话。

## 二、框架

框架的话，见[[#三、结尾|第三章]]。

## 三、结尾

结尾的话，也见[[#一、开场|第一章]]。
"""
    doc = FakeDoc()
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", note, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, lambda c: "--scope" in c))
    # That resume deleted the third chapter, which held link/2, and saved the
    # note without it; then the note was edited again: the first chapter gains
    # a link, and this export rewrites that chapter.
    doc.blocks = [b for b in doc.blocks if b["text"] not in ("三、结尾", "结尾的话，也见[第一章](https://fluentflow.invalid/link/2)。")]
    pushed = """# 访谈

## 一、开场

开场的话。

## 二、框架

框架的话，见[[#三、结尾|第三章]]。
"""
    cli.save_pushed_note(tmp_path, "t1", pushed)
    edited = pushed.replace("开场的话。", "开场的话，见[[#二、框架|第二章]]。")
    written: list[str] = []

    def runner(cmd, **kwargs):
        if cmd[2] == "+update" and _flag(cmd, "--command") == "block_insert_after":
            written.append(kwargs.get("input") or "")
        return doc(cmd, **kwargs)

    result = cli.export_markdown_via_lark_cli("访谈", edited, task_id="t1", artifact_root=tmp_path, runner=runner)

    assert len(written) == 1
    assert "https://fluentflow.invalid/link/3" in written[0], "numbered after the first push's link/2"
    assert "https://fluentflow.invalid/link/2" not in written[0]
    assert f"[第二章]({URL}#{_ids(doc)['二、框架']})" in next(t for t in doc.texts() if "开场的话" in t)
    # The second chapter links to the chapter that resume deleted: reported,
    # its words kept, as before.
    assert result["sync"]["link_errors"] == ["第三章"]


# Second independent review of the resume, 2026-11-26: a picture right above
# a caption is the screenshot only when the export was putting that very
# screenshot in when it stopped (it records each one before and after it goes
# in); anything else there (the note's own picture, one the user pasted in
# Feishu) is left alone and never recorded as the screenshot. A caption inside
# a callout is found too.


def _cut_off_after(doc: FakeDoc, stop: "Callable[[list[str]], bool]"):
    """``doc``, except that the service stops right after the first command
    ``stop`` picks has run, before the export learns how it went."""
    def runner(cmd, **kwargs):
        result = doc(cmd, **kwargs)
        if stop(list(cmd)):
            raise KeyboardInterrupt("the service stopped")
        return result
    return runner


def _at_picture(doc: FakeDoc, name: str | None, point: str):
    """``doc`` with the service stopping while picture ``name`` (None: the
    first one) goes in: before its insert, right after its insert (before
    the export records it), or after it is recorded but before its marker is
    taken out."""
    def inserting(cmd: list[str]) -> bool:
        return cmd[2] == "+media-insert" and name in (None, _flag(cmd, "--file"))

    if point == "before_insert":
        return _cut_off_at(doc, inserting)
    if point == "after_insert":
        return _cut_off_after(doc, inserting)
    seen: list[bool] = []

    def stop(cmd: list[str]) -> bool:
        if inserting(cmd):
            seen.append(True)
        return (bool(seen) or name is None) and cmd[2] == "+update" \
            and _flag(cmd, "--command") == "str_replace" and "FFIMG" in _flag(cmd, "--pattern")
    return _cut_off_at(doc, stop)


def _caption_below(doc: FakeDoc, block: dict) -> str:
    """The words right below a picture, past any other pictures, inside the
    next callout when it holds them."""
    at = doc.blocks.index(block)
    for below in doc.blocks[at + 1:]:
        if below["tag"] == "img":
            continue
        return below["text"] or next(c["text"] for c in below.get("children", []) if "图 " in c["text"])
    return ""


def _screenshots_recorded(doc: FakeDoc, result: dict, names: list[str]) -> None:
    """Each screenshot recorded as the picture of its own file, and nothing
    else in the document recorded (``names``: the screenshots in document
    order; the other pictures are the note's or the user's)."""
    ours = [b for b in doc.blocks if b["tag"] == "img" and not b["token"].startswith(("web", "paste"))]
    recorded = {image["name"]: (image["file_token"], image["block_id"]) for image in result["sync"]["images"]}
    assert recorded == {name: (b["token"], b["id"]) for name, b in zip(names, ours)}


@pytest.mark.parametrize("nameless", [False, True])
@pytest.mark.parametrize("first_cut", ["before_insert", "after_insert", "before_marker"])
@pytest.mark.parametrize("resume_cut", [None, "before_insert", "after_insert", "before_marker"])
def test_a_cut_off_at_any_point_of_a_picture_going_in_ends_with_each_in_once(tmp_path, nameless, first_cut, resume_cut):
    """The export stopped while picture 2 went in (before the insert, right
    after it, or before its marker went), the resume in turn stopped at the
    same points of the first picture it put in: the next export finishes,
    each picture once right above its caption, recorded as it is, and one
    more export writes nothing."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    doc = FakeDoc()
    doc.nameless_pictures = nameless
    names = ["a.jpg", "b.jpg", "c.jpg"]
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path,
                                         runner=_at_picture(doc, "b.jpg", first_cut))
    if resume_cut:
        with pytest.raises(KeyboardInterrupt):
            cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path,
                                             runner=_at_picture(doc, None, resume_cut))

    result = cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path, runner=doc)

    shown = [b["tag"] if b["tag"] == "img" else b["text"] for b in doc.blocks if b["tag"] == "img" or "图 " in b["text"]]
    assert shown == ["img", "图 1：讲者打开设置页", "img", "图 2：点开模型菜单", "img", "图 3：保存后的结果"]
    assert [b["name"] for b in doc.blocks if b["tag"] == "img"] == ([""] * 3 if nameless else names)
    assert not any("FFIMG" in t for t in doc.texts())
    assert result["image_count"] == 3 and result["image_upload_count"] == 3 and result["image_upload_errors"] == []
    _recorded_as_shown(doc, result, names)
    assert not (tmp_path / "t1" / cli.CREATED).exists()

    calls = len(doc.calls)
    again = cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path,
                                             previous=result["sync"], runner=doc)
    assert not any(c[2] == "+media-insert" for c in doc.calls[calls:])
    _recorded_as_shown(doc, again, names)


@pytest.mark.parametrize("cut", ["before_insert", "after_insert"])
def test_the_note_s_own_picture_right_above_a_screenshot_is_not_taken_for_it(tmp_path, cut):
    """The note's web picture right above a screenshot shows without a name,
    like a screenshot Feishu does not name. Stopped before or right after
    that screenshot went in: it ends up in once, and the web picture is kept
    and not recorded as it."""
    _frames(tmp_path, ["a.jpg"])
    note = f"# 访谈\n\n## 一、开场\n\n开场的话。\n\n![](https://example.com/web.png)\n{_pic('a.jpg', '设置页')}\n"
    doc = FakeDoc()
    doc.nameless_pictures = True
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", note, task_id="t1", artifact_root=tmp_path,
                                         runner=_at_picture(doc, "a.jpg", cut))

    web = next(i for i, b in enumerate(doc.blocks) if b["tag"] == "img" and b["token"].startswith("web"))
    assert "FFIMG" in doc.blocks[web + 1 + (cut == "after_insert")]["text"], "the web picture right above"

    result = cli.export_markdown_via_lark_cli("访谈", note, task_id="t1", artifact_root=tmp_path, runner=doc)

    pictures = [b for b in doc.blocks if b["tag"] == "img"]
    assert [b["token"].startswith("web") for b in pictures] == [True, False], "the web picture, then the screenshot"
    assert _caption_below(doc, pictures[1]) == "图 1：设置页"
    assert result["image_upload_count"] == 1 and result["image_upload_errors"] == []
    _screenshots_recorded(doc, result, ["a.jpg"])


def _paste_above(doc: FakeDoc, caption: str) -> dict:
    """A picture the user pastes in Feishu right above ``caption`` (pasted
    pictures show no name)."""
    at = next(i for i, b in enumerate(doc.blocks) if caption in b.get("text", ""))
    pasted = {"tag": "img", "id": "pasted", "name": "", "token": "pastetok", "text": ""}
    doc.blocks.insert(at, pasted)
    return pasted


@pytest.mark.parametrize("nameless", [False, True])
@pytest.mark.parametrize("cut", [("a.jpg", "before_insert"), ("b.jpg", "before_marker"), ("c.jpg", "before_insert")])
def test_a_picture_pasted_in_feishu_above_a_caption_is_kept_and_not_taken_for_the_screenshot(tmp_path, nameless, cut):
    """Between the cut-off and the resume the user pastes a picture right
    above 图 2: picture 2 goes in once (or stays as it was), the user's
    picture stays, and only the screenshots are recorded."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    doc = FakeDoc()
    doc.nameless_pictures = nameless
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path,
                                         runner=_at_picture(doc, *cut))
    pasted = _paste_above(doc, "图 2：")

    result = cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert pasted in doc.blocks
    ours = [b for b in doc.blocks if b["tag"] == "img" and b is not pasted]
    assert [_caption_below(doc, b) for b in ours] == ["图 1：讲者打开设置页", "图 2：点开模型菜单", "图 3：保存后的结果"]
    if not nameless:
        assert [b["name"] for b in ours] == ["a.jpg", "b.jpg", "c.jpg"]
    assert not any("FFIMG" in t for t in doc.texts())
    assert result["image_upload_count"] == 3 and result["image_upload_errors"] == []
    _screenshots_recorded(doc, result, ["a.jpg", "b.jpg", "c.jpg"])


def test_a_caption_whose_picture_did_not_go_in_is_not_given_the_picture_above_it(tmp_path):
    """Picture 2 failed to upload (its marker is taken out as it fails), the
    export stopped before picture 3, then the user pasted a picture right
    above 图 2: the resume puts picture 3 in, reports picture 2 as not
    uploaded, and keeps the user's picture without recording it as picture 2."""
    _frames(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    doc = FakeDoc()

    def runner(cmd, **kwargs):
        if cmd[2] == "+media-insert" and _flag(cmd, "--file") == "b.jpg":
            return _fail("upload failed")
        if cmd[2] == "+media-insert" and _flag(cmd, "--file") == "c.jpg":
            raise KeyboardInterrupt("the service stopped")
        return doc(cmd, **kwargs)

    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path, runner=runner)
    pasted = _paste_above(doc, "图 2：")

    result = cli.export_markdown_via_lark_cli("访谈", PICTURED, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert pasted in doc.blocks
    assert [b["name"] for b in doc.blocks if b["tag"] == "img" and b is not pasted] == ["a.jpg", "c.jpg"]
    assert result["image_upload_count"] == 2
    assert result["image_upload_errors"] == ["/jobs/t1/artifacts/frame?file=b.jpg"]
    by_name = {image["name"]: image for image in result["sync"]["images"]}
    assert (by_name["b.jpg"]["file_token"], by_name["b.jpg"]["block_id"]) == (None, None)
    assert "pastetok" not in [image["file_token"] for image in result["sync"]["images"]]


CALLOUT = f"""# 访谈

## 一、开场

开场的话。

> [!note] 提示
> 先看这里。
> {_pic('a.jpg', '设置页')}
> {_pic('b.jpg', '菜单')}

后面的话。
"""


@pytest.mark.parametrize("nameless", [False, True])
@pytest.mark.parametrize("cut", [
    ("a.jpg", "before_insert"), ("a.jpg", "after_insert"), ("b.jpg", "after_insert"), ("b.jpg", "before_marker"),
])
def test_screenshots_in_a_callout_go_in_once_when_the_export_is_carried_on(tmp_path, nameless, cut):
    """A screenshot under a callout has its caption inside the callout;
    lark-cli puts its picture right above the callout (it inserts at the
    top-level block holding the selection). Stopped anywhere while they went
    in, the resume finds the captions inside the callout: each picture once,
    above the callout in order, no marker left, recorded as it is."""
    _frames(tmp_path, ["a.jpg", "b.jpg"])
    doc = FakeDoc()
    doc.nameless_pictures = nameless
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", CALLOUT, task_id="t1", artifact_root=tmp_path,
                                         runner=_at_picture(doc, *cut))

    result = cli.export_markdown_via_lark_cli("访谈", CALLOUT, task_id="t1", artifact_root=tmp_path, runner=doc)

    shown = [b["tag"] for b in doc.blocks if b["tag"] in ("img", "callout")]
    assert shown == ["img", "img", "callout"]
    callout = next(b for b in doc.blocks if b["tag"] == "callout")
    assert [c["text"] for c in callout["children"] if "图 " in c["text"]] == ["图 1：设置页", "图 2：菜单"]
    if not nameless:
        assert [b["name"] for b in doc.blocks if b["tag"] == "img"] == ["a.jpg", "b.jpg"]
    assert not any("FFIMG" in t for t in doc.texts())
    assert result["image_count"] == 2 and result["image_upload_count"] == 2 and result["image_upload_errors"] == []
    _recorded_as_shown(doc, result, ["a.jpg", "b.jpg"])


@pytest.mark.parametrize("older", ["document_only", "no_progress"])
def test_screenshots_in_a_callout_go_in_for_a_cut_off_recorded_by_an_older_version(tmp_path, older):
    """A record from before the export noted each picture going in (only the
    document, or what it wrote without progress): the captions inside a
    callout are still found and their pictures put in."""
    _frames(tmp_path, ["a.jpg", "b.jpg"])
    doc = FakeDoc()
    _cut_before_pictures(doc, CALLOUT, tmp_path)
    record = tmp_path / "t1" / cli.CREATED
    kept = json.loads(record.read_text(encoding="utf-8"))
    if older == "document_only":
        kept.pop("first_push")
    else:
        kept["first_push"].pop("inserting", None)
        kept["first_push"].pop("inserted", None)
        for picture in kept["first_push"]["pictures"]:
            picture.pop("above", None)
    record.write_text(json.dumps(kept), encoding="utf-8")

    result = cli.export_markdown_via_lark_cli("访谈", CALLOUT, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert [b["name"] for b in doc.blocks if b["tag"] == "img"] == ["a.jpg", "b.jpg"]
    assert not any("FFIMG" in t for t in doc.texts())
    _recorded_as_shown(doc, result)


def test_a_marker_inside_a_callout_is_read_from_the_document(tmp_path):
    """The document as Feishu returns it, a caption led by its marker inside
    a callout: the picture goes in at that marker and the marker goes."""
    _frames(tmp_path, ["a.jpg"])
    xml = ('<title id="D">t</title><h1 id="h1">访谈</h1>'
           '<callout id="c1" emoji="bulb"><h4 id="h4">提示</h4>'
           '<p id="p1">⟦FFIMG-abcd1234-1⟧图 1：设置页</p></callout>')
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[2] == "+fetch":
            return _ok({"document": {"content": xml}})
        if cmd[2] == "+media-insert":
            return _ok({"block_id": "new", "file_token": "tok"})
        return _ok({"result": "success"})

    first = {"push": "abcd1234", "pictures": [{"figure": 1, "src": "/jobs/t1/artifacts/frame?file=a.jpg",
             "line": _pic("a.jpg", "设置页"), "caption": "图 1：设置页", "name": "a.jpg"}], "links": []}
    written = f"<callout emoji=\"bulb\">\n#### 提示\n{_pic('a.jpg', '设置页')}\n</callout>"
    images, errors, _pending, _missing, _content = lark_cli_images.resume_pictures(
        runner, "lark-cli", "D", written, first, [], task_id="t1", artifact_root=tmp_path)

    assert [_flag(c, "--selection-with-ellipsis") for c in calls if c[2] == "+media-insert"] == ["⟦FFIMG-abcd1234-1⟧"]
    assert [_flag(c, "--pattern") for c in calls if c[2] == "+update"] == ["⟦FFIMG-abcd1234-1⟧"]
    assert [(image["name"], image["file_token"]) for image in images] == [("a.jpg", "tok")] and errors == []


def test_an_older_record_never_gives_a_callout_s_caption_another_picture(tmp_path):
    """An older record (no progress kept), cut off after both pictures of a
    callout went in and their markers were taken out: lark-cli stacked both
    pictures above the callout, so which is whose cannot be read from the
    document. Neither is recorded as the other's picture; one not told apart
    is reported as not uploaded rather than recorded wrong."""
    _frames(tmp_path, ["a.jpg", "b.jpg"])
    doc = FakeDoc()
    doc.nameless_pictures = True
    with pytest.raises(KeyboardInterrupt):
        cli.export_markdown_via_lark_cli("访谈", CALLOUT, task_id="t1", artifact_root=tmp_path,
                                         runner=_cut_off_at(doc, lambda c: "--scope" in c))
    record = tmp_path / "t1" / cli.CREATED
    kept = json.loads(record.read_text(encoding="utf-8"))
    kept.pop("first_push")
    record.write_text(json.dumps(kept), encoding="utf-8")
    calls = len(doc.calls)

    result = cli.export_markdown_via_lark_cli("访谈", CALLOUT, task_id="t1", artifact_root=tmp_path, runner=doc)

    assert not any(c[2] == "+media-insert" for c in doc.calls[calls:])
    pictures = {b["name"] or f"#{i}": b for i, b in enumerate(b for b in doc.blocks if b["tag"] == "img")}
    truth = dict(zip(["a.jpg", "b.jpg"], pictures.values()))
    for image in result["sync"]["images"]:
        assert image["file_token"] in (None, truth[image["name"]]["token"]), "never another screenshot's picture"
