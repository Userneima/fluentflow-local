"""An agent finds, reads and restores the earlier versions of a task's note.

Requirements (owner, 2026-10-22, after a 40,000-character note was saved over
with its 240-character preview and found again only by hand):
1. Every kept version says what wrote over it: an edit on the page, the md
   edited in Obsidian, an agent's note, a rewrite, Feishu, a restore. A version
   kept before this was recorded, or replaced by a writer that did not say,
   reads as not recorded, never as whoever wrote before.
2. The list comes newest first, with each version's length and the change
   from the version before it; a fall from tens of thousands of characters to
   a few hundred is flagged.
3. Any version can be read whole.
4. Restoring keeps the note it replaces first, then goes through the note's
   ordinary save: the task's note, the md in the folder and the Feishu copy all
   end up as the restored version. It names the note version it started from
   and is refused, with nothing changed, when the note moved on since.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.lark_cli_exporter as cli
import backend.routers.local_agent as local_agent
from backend.core import feishu_sync, job_store, note_versions, output_folder
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir
from backend.routers.local_job_edit import router as edit_router
from tests.test_feishu_chapter_update import FakeDoc
from tests.test_output_folder_edits import _edit, pdfs  # noqa: F401

pytestmark = pytest.mark.usefixtures("pdfs")

TOKEN = "history-token"
AGENT = {"x-fluentflow-access-token": TOKEN, "x-fluentflow-client-id": LOCAL_OWNER_ID}
PAGE = {"x-fluentflow-client-id": LOCAL_OWNER_ID}

NOTE = """# 访谈

## 一、开场

开场的话。

## 二、框架

框架的话。

![架构图](/jobs/{task}/artifacts/frame?file=note_0001.jpg)
"""

_CREATED: list[str] = []


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", TOKEN)
    # Folder refreshes and Feishu pushes are run by the tests themselves, not
    # by timers firing in the middle of other tests.
    pushes: list[str] = []
    monkeypatch.setattr(output_folder, "schedule_sync_when_quiet", lambda task_id: None)
    monkeypatch.setattr(output_folder, "schedule_sync", lambda task_id: None)
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda task_id, delay=None: pushes.append((task_id, delay)))
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")
    yield pushes
    job_store.delete_jobs(_CREATED)
    _CREATED.clear()


def _agent() -> TestClient:
    app = FastAPI()
    app.include_router(local_agent.router)
    return TestClient(app)


def _page() -> TestClient:
    app = FastAPI()
    app.include_router(edit_router)
    return TestClient(app)


def _task(tmp_path: Path, note: str = NOTE) -> tuple[str, Path]:
    """A finished task whose recording sits in a folder of the user's, its
    note written beside it."""
    folder = tmp_path / "lectures"
    folder.mkdir(exist_ok=True)
    source = folder / "访谈.mov"
    source.write_bytes(b"original")
    task_id = f"nh-{uuid.uuid4().hex[:8]}"
    _CREATED.append(task_id)
    frames = _artifact_storage_dir() / task_id / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "note_0001.jpg").write_bytes(b"jpeg")
    result = _attach_result_artifacts(task_id, {
        "filename": source.name, "display_title": source.stem, "transcript_text": "一句。",
        "summary_markdown": note.format(task=task_id), "summary_status": "completed",
    })
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result=result,
                         metadata={"folder_intake": {"chosen_with": "folder_path", "original_path": str(source)}})
    output_folder.sync(task_id)
    return task_id, folder / "访谈" / "访谈_笔记.md"


def _in_feishu(task_id: str, doc: FakeDoc, monkeypatch) -> None:
    """The task's note exported to Feishu with the user's identity, so it is
    kept in step from then on."""
    real = cli.export_markdown_via_lark_cli
    monkeypatch.setattr(cli, "export_markdown_via_lark_cli", lambda *a, **k: real(*a, **{**k, "runner": doc}))
    result = _result(task_id)
    response = cli.export_markdown_via_lark_cli("访谈", result["summary_markdown"], task_id=task_id,
                                                artifact_root=_artifact_storage_dir())
    job_store.save_result_changes(task_id, result, {**result, "lark_doc_title": "访谈", "lark_response": response})


def _result(task_id: str) -> dict:
    return job_store.get_job(task_id)["result"]


def _web_edit(task_id: str, note: str) -> None:
    response = _page().patch(f"/jobs/{task_id}/summary", headers=PAGE, json={"summary_markdown": note})
    assert response.status_code == 200, response.text


def _newest(md: Path) -> note_versions.Version:
    return note_versions.kept(md)[-1]


# ── 1. what replaced each version ──────────────────────────────────────────

def test_an_edit_on_the_page_is_recorded_as_what_replaced_the_md(tmp_path):
    task, md = _task(tmp_path)
    before = md.read_text(encoding="utf-8")

    _web_edit(task, _result(task)["summary_markdown"] + "\n网页上加的一段。\n")
    output_folder.sync(task)

    newest = _newest(md)
    assert newest.path.read_text(encoding="utf-8") == before
    assert (newest.side, newest.by) == (note_versions.LOCAL, note_versions.WEB_EDIT)


def test_an_md_edited_in_obsidian_is_recorded_as_what_replaced_the_task_note(tmp_path):
    task, md = _task(tmp_path)
    before = md.read_text(encoding="utf-8")

    _edit(md, before + "\n在 Obsidian 里写的问题。\n")
    output_folder.sync(task)

    newest = _newest(md)
    assert (newest.side, newest.by) == (note_versions.TASK, note_versions.OBSIDIAN)
    assert newest.path.read_text(encoding="utf-8") == before
    assert note_versions.changed_by(_result(task)) == note_versions.OBSIDIAN


def test_a_rewrite_through_the_agent_is_recorded(tmp_path, monkeypatch):
    task, md = _task(tmp_path)
    monkeypatch.setattr(local_agent, "summarize_transcript_with_metadata", lambda transcript, **kwargs: SimpleNamespace(
        markdown="# 文本模型重写的笔记\n\n重写的正文。", requested_mode="auto", resolved_mode="direct", chunk_count=1))
    monkeypatch.setattr(local_agent.visual_note_job, "describe", lambda *a, **k: {"eligible": False, "reason": "测试"})

    response = _agent().post(f"/agent/v1/tasks/{task}/note/regenerate", headers=AGENT, json={})
    assert response.status_code == 200, response.text
    output_folder.sync(task)

    assert (_newest(md).side, _newest(md).by) == (note_versions.LOCAL, note_versions.REWRITE)


def test_putting_the_previous_note_back_is_recorded_as_a_restore(tmp_path, monkeypatch):
    task, md = _task(tmp_path)
    monkeypatch.setattr(local_agent, "summarize_transcript_with_metadata", lambda transcript, **kwargs: SimpleNamespace(
        markdown="# 重写的\n\n正文。", requested_mode="auto", resolved_mode="direct", chunk_count=1))
    monkeypatch.setattr(local_agent.visual_note_job, "describe", lambda *a, **k: {"eligible": False, "reason": "测试"})
    _agent().post(f"/agent/v1/tasks/{task}/note/regenerate", headers=AGENT, json={})
    output_folder.sync(task)

    response = _agent().post(f"/agent/v1/tasks/{task}/visual-note", headers=AGENT, json={"restore_previous_note": True})
    assert response.status_code == 200, response.text
    output_folder.sync(task)

    assert note_versions.changed_by(_result(task)) == note_versions.RESTORE
    assert (_newest(md).side, _newest(md).by) == (note_versions.LOCAL, note_versions.RESTORE)
    assert "重写的" in _newest(md).path.read_text(encoding="utf-8")


def test_feishu_pulls_and_pushes_record_what_replaced_each_side(tmp_path, monkeypatch):
    task, md = _task(tmp_path)
    doc = FakeDoc()
    _in_feishu(task, doc, monkeypatch)

    # Edited in Feishu and pulled back: the task's note is replaced by Feishu.
    doc.edit("框架的话。", "框架的话，飞书里补的。")
    assert feishu_sync.pull(task, modified_at=doc.modified, runner=doc) == "pulled"
    assert (_newest(md).side, _newest(md).by) == (note_versions.TASK, note_versions.FEISHU_PULL)
    assert note_versions.changed_by(_result(task)) == note_versions.FEISHU_PULL

    # Edited on the page and pushed: the Feishu document is replaced by that edit.
    _web_edit(task, _result(task)["summary_markdown"].replace("开场的话。", "开场的话，网页上改的。"))
    feishu_sync.push(task, runner=doc)
    feishu_side = [v for v in note_versions.kept(md) if v.side == note_versions.FEISHU]
    assert feishu_side and feishu_side[-1].by == note_versions.WEB_EDIT
    assert any("网页上改的" in text for text in doc.texts())


def test_a_change_nobody_declared_reads_as_not_recorded_not_as_the_previous_writer(tmp_path):
    task, md = _task(tmp_path)
    _web_edit(task, _result(task)["summary_markdown"] + "\n网页上加的。\n")
    output_folder.sync(task)

    result = _result(task)
    job_store.save_result_changes(task, result, {**result, "summary_markdown": result["summary_markdown"] + "\n别处写的。\n"})
    output_folder.sync(task)

    assert note_versions.changed_by(_result(task)) is None
    assert _newest(md).by is None
    listed = _agent().get(f"/agent/v1/tasks/{task}/note/versions", headers=AGENT).json()
    assert listed["versions"][0]["replaced_by_label"] == note_versions.NOT_RECORDED
    assert listed["current"]["changed_by_label"] == note_versions.NOT_RECORDED


def test_copies_kept_before_this_was_recorded_are_listed_as_not_recorded(tmp_path):
    task, md = _task(tmp_path)
    folder = note_versions.folder_for(md)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{md.stem}_{note_versions.LOCAL}_20261021-090000.md").write_text("# 旧的\n\n很早的一版。\n", encoding="utf-8")
    # Another note in the same folder whose name starts the same way is not this one's.
    (folder / f"{md.stem}_附录_{note_versions.LOCAL}_20261021-090500.md").write_text("# 别的笔记\n", encoding="utf-8")

    listed = _agent().get(f"/agent/v1/tasks/{task}/note/versions", headers=AGENT).json()

    assert [v["version_id"] for v in listed["versions"]] == [f"{md.stem}_{note_versions.LOCAL}_20261021-090000.md"]
    assert listed["versions"][0]["replaced_by"] is None
    assert listed["versions"][0]["replaced_by_label"] == "未记录"


# ── 2. the list ────────────────────────────────────────────────────────────

def test_the_list_is_newest_first_with_lengths_changes_and_a_sharp_drop_flagged(tmp_path):
    long_note = "# 访谈\n\n" + "长段落。" * 10_000
    task, md = _task(tmp_path, long_note)
    preview = long_note[:240]
    # The 2026-10-22 loss: the page saved the list's 240-character preview as the note.
    result = _result(task)
    job_store.save_result_changes(task, result, note_versions.declare(
        {**result, "summary_markdown": preview}, note_versions.WEB_EDIT))
    output_folder.sync(task)
    _web_edit(task, preview + "\n补了一句。\n")
    output_folder.sync(task)

    listed = _agent().get(f"/agent/v1/tasks/{task}/note/versions", headers=AGENT).json()

    versions = listed["versions"]
    assert [v["replaced_by"] for v in versions] == [note_versions.WEB_EDIT, note_versions.WEB_EDIT]
    newest, oldest = versions
    assert oldest["chars"] > 40_000 and oldest["chars_change"] is None
    assert newest["chars"] < 300
    assert newest["chars_change"] == newest["chars"] - oldest["chars"]
    assert newest["sharp_drop"] is True and oldest["sharp_drop"] is False
    current = listed["current"]
    assert current["chars"] == len(_result(task)["summary_markdown"].strip())
    assert current["chars_change"] == current["chars"] - newest["chars"]
    assert current["sharp_drop"] is False
    assert current["changed_by"] == note_versions.WEB_EDIT
    assert current["note_version"] == _result(task)["note_version"]


def test_a_version_is_read_whole(tmp_path):
    task, md = _task(tmp_path)
    before = md.read_text(encoding="utf-8")
    _web_edit(task, "# 改短了\n")
    output_folder.sync(task)
    version_id = _newest(md).id

    read = _agent().get(f"/agent/v1/tasks/{task}/note/versions/{quote(version_id)}", headers=AGENT).json()

    assert read["markdown"] == before
    assert read["replaced_by"] == note_versions.WEB_EDIT
    missing = _agent().get(f"/agent/v1/tasks/{task}/note/versions/nothing.md", headers=AGENT)
    assert missing.status_code == 404


# ── 4. restoring ───────────────────────────────────────────────────────────

def test_a_restore_brings_all_three_places_back_and_keeps_what_it_replaced(tmp_path, monkeypatch, _quiet):
    task, md = _task(tmp_path)
    doc = FakeDoc()
    _in_feishu(task, doc, monkeypatch)
    original_note = _result(task)["summary_markdown"]
    original_md = md.read_text(encoding="utf-8")
    # The note saved over with a fragment, everywhere.
    _web_edit(task, "# 访谈\n\n只剩一句。\n")
    output_folder.sync(task)
    feishu_sync.push(task, runner=doc)
    assert not any("框架的话" in text for text in doc.texts())

    listed = _agent().get(f"/agent/v1/tasks/{task}/note/versions", headers=AGENT).json()
    target = next(v for v in listed["versions"] if v["side"] == note_versions.LOCAL)
    _quiet.clear()
    response = _agent().post(f"/agent/v1/tasks/{task}/note/versions/{quote(target['version_id'])}/restore",
                             headers=AGENT, json={"base_version": listed["current"]["note_version"]})
    assert response.status_code == 200, response.text
    assert response.json()["restored"] is True
    assert (task, 0) in _quiet, "the Feishu copy is pushed without waiting for more edits"
    output_folder.sync(task)
    feishu_sync.push(task, runner=doc)

    result = _result(task)
    assert result["summary_markdown"].strip() == original_note.strip(), "screenshots point at the task's own frames again"
    assert md.read_text(encoding="utf-8") == original_md
    assert any("框架的话。" in text for text in doc.texts()) and not any("只剩一句" in text for text in doc.texts())
    assert note_versions.changed_by(result) == note_versions.RESTORE
    restore_copies = [v for v in note_versions.kept(md) if v.by == note_versions.RESTORE]
    assert restore_copies and "只剩一句" in restore_copies[0].path.read_text(encoding="utf-8")


def test_a_restore_on_a_note_that_moved_on_is_refused_and_changes_nothing(tmp_path):
    task, md = _task(tmp_path)
    _web_edit(task, "# 改短了\n")
    output_folder.sync(task)
    listed = _agent().get(f"/agent/v1/tasks/{task}/note/versions", headers=AGENT).json()
    # Meanwhile the user goes on editing.
    _web_edit(task, "# 改短了\n\n又在网页上加了一句。\n")
    count = len(note_versions.kept(md))

    response = _agent().post(
        f"/agent/v1/tasks/{task}/note/versions/{quote(listed['versions'][0]['version_id'])}/restore",
        headers=AGENT, json={"base_version": listed["current"]["note_version"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale"
    assert "又在网页上加了一句" in _result(task)["summary_markdown"]
    assert len(note_versions.kept(md)) == count

    no_version = _agent().post(
        f"/agent/v1/tasks/{task}/note/versions/{quote(listed['versions'][0]['version_id'])}/restore",
        headers=AGENT, json={},
    )
    assert no_version.status_code == 422


def test_a_feishu_side_version_is_not_restored(tmp_path, monkeypatch):
    task, md = _task(tmp_path)
    doc = FakeDoc()
    _in_feishu(task, doc, monkeypatch)
    _web_edit(task, _result(task)["summary_markdown"].replace("开场的话。", "改过的开场。"))
    feishu_sync.push(task, runner=doc)
    feishu_version = next(v for v in note_versions.kept(md) if v.side == note_versions.FEISHU)
    before = _result(task)["summary_markdown"]

    response = _agent().post(
        f"/agent/v1/tasks/{task}/note/versions/{quote(feishu_version.id)}/restore",
        headers=AGENT, json={"base_version": _result(task)["note_version"]},
    )

    assert response.status_code == 409 and response.json()["detail"]["code"] == "feishu_side"
    assert _result(task)["summary_markdown"] == before


# ── the agent's tools ──────────────────────────────────────────────────────

def test_the_mcp_tools_reach_these_routes(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import fluentflow_mcp_server as mcp

    calls: list[tuple] = []
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kw: calls.append((method, path, kw.get("payload"))) or {"ok": True})
    name = "访谈_笔记_本地_20261022-101010_网页编辑.md"

    mcp.list_note_versions("t1")
    mcp.read_note_version("t1", name)
    mcp.restore_note_version("t1", name, "abc")

    assert calls == [
        ("GET", "/agent/v1/tasks/t1/note/versions", None),
        ("GET", f"/agent/v1/tasks/t1/note/versions/{quote(name, safe='')}", None),
        ("POST", f"/agent/v1/tasks/t1/note/versions/{quote(name, safe='')}/restore", {"base_version": "abc"}),
    ]
    schema = {tool["name"]: tool for tool in mcp.TOOL_DEFINITIONS}
    assert schema["restore_note_version"]["inputSchema"]["required"] == ["task_id", "version_id", "base_version"]


def test_a_note_the_pipeline_writes_says_it_was_generated():
    from backend.core.job_lifecycle import result_for_summary_success

    result = result_for_summary_success({"task_id": "x", "transcript_text": "一句。"}, "# 生成的笔记\n")

    assert note_versions.changed_by(result) == note_versions.GENERATED
