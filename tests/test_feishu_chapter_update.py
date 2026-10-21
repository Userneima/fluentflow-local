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
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import uuid

import pytest

import backend.core.lark_cli_exporter as cli
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
    """A Feishu document as lark-cli shows it: top-level blocks with ids."""

    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.revision = 1
        self.writes: list[str] = []
        self.calls: list[list[str]] = []
        self.fail_markdown_fetch = False
        self._next = 0

    def _id(self) -> str:
        self._next += 1
        return f"blk{self._next}"

    def _parse(self, markdown: str) -> list[dict]:
        blocks = []
        for chunk in re.split(r"\n\s*\n", markdown.strip()):
            chunk = chunk.strip()
            if not chunk:
                continue
            heading = re.match(r"^(#{1,9})\s+(.*)$", chunk)
            if heading:
                blocks.append({"tag": f"h{len(heading.group(1))}", "id": self._id(), "text": heading.group(2)})
            else:
                blocks.append({"tag": "p", "id": self._id(), "text": chunk})
        return blocks

    def texts(self) -> list[str]:
        return [b["text"] for b in self.blocks if b["tag"] != "img"]

    def _xml(self) -> str:
        return f'<title id="{DOC}">t</title>' + "".join(
            f'<{b["tag"]} id="{b["id"]}">{html.escape(b["text"])}</{b["tag"]}>' for b in self.blocks
        )

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        verb = cmd[2]
        if verb == "+create":
            self.blocks = self._parse(kwargs.get("input") or "")
            self.revision += 1
            self.writes.append("create")
            return _ok({"document": {"document_id": DOC, "url": URL, "revision_id": self.revision}})
        if verb == "+fetch":
            if "--doc-format" in cmd and _flag(cmd, "--doc-format") == "markdown":
                if self.fail_markdown_fetch:
                    return _fail("cannot read")
                return _ok({"document": {"content": "\n\n".join(self.texts()), "revision_id": self.revision}})
            return _ok({"document": {"document_id": DOC, "content": self._xml(), "revision_id": self.revision}})
        if verb == "+update":
            command = _flag(cmd, "--command")
            self.writes.append(command)
            self.revision += 1
            if command == "block_delete":
                gone = set(_flag(cmd, "--block-id").split(","))
                self.blocks = [b for b in self.blocks if b["id"] not in gone]
            elif command == "block_insert_after":
                anchor = _flag(cmd, "--block-id")
                new = self._parse(kwargs.get("input") or "")
                at = 0 if anchor == DOC else next(i for i, b in enumerate(self.blocks) if b["id"] == anchor) + 1
                self.blocks[at:at] = new
            elif command == "str_replace":
                pattern, content = _flag(cmd, "--pattern"), _flag(cmd, "--content")
                hits = [b for b in self.blocks if pattern in b["text"]]
                if len(hits) != 1:
                    return _ok({"document": {"revision_id": self.revision}, "result": "failed"})
                hits[0]["text"] = hits[0]["text"].replace(pattern, content)
            return _ok({"document": {"revision_id": self.revision}, "result": "success"})
        raise AssertionError(f"unexpected command {cmd}")


@pytest.fixture(autouse=True)
def _cli_on_path(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")


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


def test_a_push_updates_the_document_records_it_and_keeps_the_feishu_version(monkeypatch, tmp_path):
    doc = FakeDoc()
    task_id = _task(_first(doc))
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    real = cli.export_markdown_via_lark_cli
    monkeypatch.setattr(cli, "export_markdown_via_lark_cli", lambda *a, **k: real(*a, **{**k, "runner": doc}))
    md = tmp_path / "访谈" / "访谈_笔记.md"
    md.parent.mkdir()
    result = job_store.get_job(task_id)["result"]
    job_store.save_result_changes(task_id, result, {**result, "output_folder": {"md_path": str(md)}})
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda task_id, delay=None: None)
    result = job_store.get_job(task_id)["result"]
    job_store.update_job_result(task_id, {**result, "summary_markdown": NOTE.replace("结尾的话", "新结尾")})

    response = feishu_sync.push(task_id)

    assert response and response["url"] == URL
    stored = job_store.get_job(task_id)["result"]
    assert stored["lark_response"]["sync"]["revision_id"] == doc.revision
    assert stored["lark_response"]["sync"]["note_digest"] == cli.note_digest(stored["summary_markdown"])
    assert any("新结尾" in t for t in doc.texts())
    kept = note_versions.versions(md)
    assert kept and note_versions.FEISHU in kept[-1].name and "结尾的话" in kept[-1].read_text(encoding="utf-8")
    assert feishu_sync.push(task_id) is None, "nothing left to push"
