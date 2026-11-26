"""学完了: marking a note studied, and the study record it leaves for the agent.

From the requirement (owner, 2026-11-25):

- Clicking 学完了 takes the task off the pinned ones and into the 学完的 list,
  grouped by the folder its recording came from; 好用 / 有错 / 有漏 is optional.
- At that click, what he changed in the note since it was last written whole
  is kept as a study record the agent can read; a note studied without changes
  is recorded as needing none.
- Changes made before the note was last written whole are not his changes to
  this note; an agent rewriting it is not an edit of his.
- Taking the mark back deletes the record; marking again keeps its id, so what
  the agent recorded against it still points at it.
- A study record the agent has been through (a later record names it) reads
  as reviewed; made again after that (a new verdict, edits since), it is new
  to the agent again.
- Edits made before this was in place are still his: the first edit after it
  must not hide them.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.note_feedback as nf
from backend.core import note_versions, study_record
from backend.core.job_store import get_job, upsert_job
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_job_mutation import router

_HEADERS = {"x-fluentflow-client-id": "desktop-a"}
WRITTEN = "# 第四课\n\n## 一、调研\n\n讲者说先找三个对手。\n\n## 二、汇报\n\n汇报用了十分钟。\n"
EDITED = "# 第四课\n\n## 一、调研\n\n讲者说先找五个对手。\n\n## 二、汇报\n\n汇报用了十分钟。\n\n我的想法：可以先列清单。\n"


@pytest.fixture(autouse=True)
def skill_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_NOTE_SKILL_DIR", str(tmp_path / "mine"))


def _task(note: str, by: str, *, metadata: dict | None = None, kept: list[tuple] = ()) -> str:
    """A finished task whose note ``by`` wrote, after ``kept`` versions
    (text, what replaced it[, side]), oldest first."""
    task_id = f"study-{uuid.uuid4().hex}"
    result = note_versions.declare({"summary_markdown": note, "display_title": "第四课"}, by)
    md = note_versions.md_for(task_id, result)
    for text, replaced_by, *side in kept:
        note_versions.keep(md, text, side[0] if side else note_versions.TASK, by=replaced_by)
    upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result=result,
               metadata={"display_title": "第四课", "pinned_at": "2026-11-25T00:00:00+00:00", **(metadata or {})})
    return task_id


def _record(task_id: str) -> dict:
    record_id = get_job(task_id)["metadata"]["studied"]["record_id"]
    return next(item for item in nf.list_feedback() if item["id"] == record_id)


def test_studied_unpins_and_files_it_under_its_recordings_folder():
    task_id = _task(WRITTEN, note_versions.GENERATED, metadata={"folder_intake": {
        "original_path": "/Users/x/张和/实战 1-黑客级调研篇/5-第四课.mp4"}})

    study_record.mark(task_id, verdict="good")

    metadata = get_job(task_id)["metadata"]
    assert not metadata.get("pinned_at")
    assert metadata["studied"]["course"] == "实战 1-黑客级调研篇"
    assert metadata["studied"]["verdict"] == "good"


def test_marking_and_taking_back_say_when_the_marks_changed():
    """Studied leaves updated_at alone, as the pin does; the page compares
    metadata.marks_changed_at to keep a fresh mark over an older reply."""
    task_id = _task(WRITTEN, note_versions.GENERATED)
    study_record.mark(task_id)
    first = get_job(task_id)["metadata"]["marks_changed_at"]
    study_record.mark(task_id, studied=False)
    assert get_job(task_id)["metadata"]["marks_changed_at"] > first


def test_a_task_without_a_recording_on_this_machine_is_grouped_as_links():
    task_id = _task(WRITTEN, note_versions.GENERATED)
    study_record.mark(task_id)
    assert get_job(task_id)["metadata"]["studied"]["course"] == study_record.NO_FOLDER


def test_a_note_studied_without_changes_is_recorded_as_needing_none():
    task_id = _task(WRITTEN, note_versions.AGENT_NOTE)

    study_record.mark(task_id)

    record = _record(task_id)
    assert record["kind"] == nf.STUDIED
    assert record["changes_total"] == 0
    assert "没改过笔记" in record["problem"]


def test_his_edits_since_the_note_was_written_are_in_the_record():
    task_id = _task(EDITED, note_versions.WEB_EDIT, kept=[(WRITTEN, note_versions.WEB_EDIT)])

    study_record.mark(task_id, verdict="wrong")

    record = _record(task_id)
    assert record["verdict"] == "wrong"
    assert record["edited_by"] == {"网页编辑": 1}
    befores = [c["before"] for c in record["changes"]]
    afters = [c["after"] for c in record["changes"]]
    assert any("三" in b for b in befores) and any("五" in a for a in afters)
    assert any("我的想法" in a for a in afters)
    view = nf.public_view(record, set())
    assert view["changes_shown"] and view["changes_file"] and view["reviewed"] is False


def test_changes_before_the_last_whole_note_write_are_not_counted():
    # generated OLD -> an agent rewrote it as WRITTEN -> he edited it in Obsidian.
    old = "# 第四课\n\n完全不同的旧笔记。\n"
    task_id = _task(EDITED, note_versions.OBSIDIAN,
                    kept=[(old, note_versions.AGENT_NOTE), (WRITTEN, note_versions.OBSIDIAN)])

    study_record.mark(task_id)

    record = _record(task_id)
    assert record["edited_by"] == {"Obsidian改了md": 1}
    assert all("旧笔记" not in (c["before"] or "") for c in record["changes"])


def test_an_edit_saved_just_now_is_found_without_any_kept_version():
    # The page's edit keeps a version only when the folder catches up a minute
    # later, and a note with no folder keeps none: the note as written is kept
    # when it is saved instead.
    from backend.core.job_store import update_job_result

    task_id = _task(WRITTEN, note_versions.GENERATED)
    update_job_result(task_id, note_versions.declare({"summary_markdown": EDITED, "display_title": "第四课"},
                                                     note_versions.WEB_EDIT))

    study_record.mark(task_id)

    record = _record(task_id)
    assert record["edited_by"] == {"网页编辑": 1}
    assert any("五" in (c["after"] or "") for c in record["changes"])


def test_a_rewrite_starts_the_count_again():
    from backend.core.job_store import update_job_result

    task_id = _task(WRITTEN, note_versions.GENERATED)
    update_job_result(task_id, note_versions.declare({"summary_markdown": EDITED}, note_versions.WEB_EDIT))
    update_job_result(task_id, note_versions.declare({"summary_markdown": EDITED + "\n重写过。\n"}, note_versions.REWRITE))

    study_record.mark(task_id)

    assert _record(task_id)["changes_total"] == 0
    assert _record(task_id)["edited_by"] == {}


def test_an_edit_that_kept_only_the_md_beside_the_recording_is_found():
    # Feishu pulls and an agent's word changes have kept only the md's version.
    task_id = _task(EDITED, note_versions.AGENT_EDIT, kept=[(WRITTEN, note_versions.AGENT_EDIT, note_versions.LOCAL)])

    study_record.mark(task_id)

    assert _record(task_id)["edited_by"] == {"agent改字": 1}
    assert _record(task_id)["changes_total"] >= 2


def test_one_edit_kept_on_both_sides_counts_once_and_screenshot_links_are_not_edits():
    md_form = WRITTEN + "\n![流程图](过程文件/截图/note_0006_crop_d8ecb5da.jpg)\n"
    task_form = EDITED + "\n![流程图](/jobs/t1/artifacts/frame?file=note_0006_crop_d8ecb5da.jpg)\n"
    task_id = _task(task_form, note_versions.OBSIDIAN, kept=[
        (md_form, note_versions.OBSIDIAN, note_versions.TASK), (md_form, note_versions.OBSIDIAN, note_versions.LOCAL)])

    study_record.mark(task_id)

    record = _record(task_id)
    assert record["edited_by"] == {"Obsidian改了md": 1}
    assert not [c for c in record["changes"] if "截图" in (c["before"] or "") + (c["after"] or "")]


def test_an_edit_with_no_version_kept_from_before_says_it_cannot_tell():
    task_id = _task(EDITED, note_versions.FEISHU_PULL)

    study_record.mark(task_id)

    assert "看不出改了什么" in _record(task_id)["problem"]


def test_marking_again_keeps_the_id_and_taking_it_back_deletes_the_record():
    task_id = _task(WRITTEN, note_versions.GENERATED)
    first = study_record.mark(task_id)["studied"]
    found = nf.record_feedback(task_id="", problem="这篇没问题", kind="fine", study_id=first["record_id"])

    again = study_record.mark(task_id, verdict="missing")["studied"]

    assert again["record_id"] == first["record_id"]
    assert again["at"] == first["at"]
    assert [i for i in nf.list_feedback() if i.get("kind") == nf.STUDIED][0]["verdict"] == "missing"
    assert found["study_id"] == first["record_id"]

    assert study_record.mark(task_id)["studied"]["verdict"] == "missing", "no verdict given keeps the one there"

    study_record.mark(task_id, studied=False)
    assert not get_job(task_id)["metadata"].get("studied")
    assert not [i for i in nf.list_feedback() if i.get("kind") == nf.STUDIED]


def test_a_record_made_again_after_the_agent_went_through_it_is_new_to_it(monkeypatch):
    clock = iter(["2026-11-25T10:00:00+08:00", "2026-11-25T10:05:00+08:00", "2026-11-25T10:09:00+08:00"])
    monkeypatch.setattr(nf, "_now", lambda: next(clock))
    task_id = _task(WRITTEN, note_versions.GENERATED)
    record_id = study_record.mark(task_id)["studied"]["record_id"]
    nf.record_feedback(task_id="", problem="这篇没问题", kind="fine", study_id=record_id)
    assert record_id in nf.reviewed_studies()

    study_record.mark(task_id, verdict="wrong")

    assert record_id not in nf.reviewed_studies()


def test_edits_made_before_this_are_not_hidden_by_the_next_one():
    # Written, edited on the page before 写成时的笔记 was kept, edited again.
    from backend.core.job_store import update_job_result

    first_edit = WRITTEN.replace("三个", "五个")
    task_id = _task(first_edit, note_versions.WEB_EDIT, kept=[(WRITTEN, note_versions.WEB_EDIT)])
    update_job_result(task_id, note_versions.declare({"summary_markdown": EDITED, "display_title": "第四课"},
                                                     note_versions.OBSIDIAN))

    study_record.mark(task_id)

    record = _record(task_id)
    assert any("三" in (c["before"] or "") for c in record["changes"]), "the first edit is still there"
    assert any("我的想法" in (c["after"] or "") for c in record["changes"])
    assert record["edited_by"] == {"网页编辑": 1, "Obsidian改了md": 1}


def test_an_edit_made_before_the_folder_was_set_up_is_still_found(tmp_path):
    from backend.core import output_folder
    from backend.core.job_store import update_job_result

    task_id = _task(WRITTEN, note_versions.GENERATED)
    update_job_result(task_id, note_versions.declare({"summary_markdown": EDITED, "display_title": "第四课"},
                                                     note_versions.WEB_EDIT))
    folder = tmp_path / "第四课"
    folder.mkdir()
    result = dict(get_job(task_id)["result"])
    result[output_folder.RESULT_KEY] = {"path": str(folder), "md_path": str(folder / "第四课.md")}
    update_job_result(task_id, result)

    study_record.mark(task_id)

    record = _record(task_id)
    assert record["edited_by"] == {"网页编辑": 1}
    assert any("五" in (c["after"] or "") for c in record["changes"])
    assert (folder / "过程文件" / study_record.WRITTEN_FILE).is_file()


def test_a_subtitle_read_from_a_folder_is_filed_under_that_folder():
    task_id = _task(WRITTEN, note_versions.GENERATED, metadata={"subtitle_intake": {
        "original_path": "/Users/x/张和/实战 2-汇报篇/3-第三课.srt"}})
    study_record.mark(task_id)
    assert get_job(task_id)["metadata"]["studied"]["course"] == "实战 2-汇报篇"


def test_two_clicks_at_once_make_one_record():
    import threading

    task_id = _task(WRITTEN, note_versions.GENERATED)
    threads = [threading.Thread(target=study_record.mark, args=(task_id,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len([i for i in nf.list_feedback() if i.get("kind") == nf.STUDIED and i["task_id"] == task_id]) == 1


def test_a_task_without_a_note_cannot_be_studied():
    task_id = _task("", note_versions.GENERATED)
    with pytest.raises(study_record.StudyError, match="没有笔记"):
        study_record.mark(task_id)


def test_feedback_naming_an_unknown_study_record_is_refused():
    with pytest.raises(nf.NoteFeedbackError, match="学完记录"):
        nf.record_feedback(task_id="", problem="没问题", kind="fine", study_id="nope")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_page_route_marks_and_answers_the_task_row():
    task_id = _task(WRITTEN, note_versions.GENERATED)

    body = _client().post(f"/jobs/{task_id}/studied", json={"verdict": "good"}, headers=_HEADERS).json()

    assert body["metadata"]["studied"]["verdict"] == "good"
    assert "task_snapshot" in body and "activity" in body
    assert _client().post(f"/jobs/{task_id}/studied", json={"studied": "yes"}, headers=_HEADERS).status_code == 422
    assert _client().post(f"/jobs/{task_id}/studied", json={"verdict": "meh"}, headers=_HEADERS).status_code == 409
    assert _client().post("/jobs/nope/studied", json={}, headers=_HEADERS).status_code == 404


def test_mcp_offers_marking_studied():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import fluentflow_mcp_server as mcp

    assert "mark_studied" in {tool["name"] for tool in mcp.TOOL_DEFINITIONS}
