"""Every output of a recording from the user's folder lands in one folder beside it.

Requirements (owner, 2026-10-14):
1. A recording the user pointed at gets a folder named after it, beside it; the
   original is not moved. Outside: the cut video and the PDF note. In 过程文件:
   the note as markdown with its screenshots, the transcript, the subtitles and
   the cut list.
2. Filled as the work goes; the note's md and PDF are refreshed when the note
   changes afterwards, and only then.
3. Processing the same recording again never overwrites the earlier folder: the
   new task gets the next free name.
4. Uploads and link downloads get no folder.
5. A folder that cannot be written does not fail the task; the reason is kept.
6. A folder scan skips the videos this produces.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.core import job_store, local_folder_intake, note_pdf, output_folder
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir


@pytest.fixture
def pdfs(monkeypatch):
    """Stands in for the browser: writes a file and records each export."""
    calls: list[dict] = []

    def export(task_id, *, client_id=None, output_dir=None, filename=None):
        calls.append({"task_id": task_id, "output_dir": output_dir, "filename": filename})
        path = Path(output_dir) / f"{filename}.pdf"
        path.write_bytes(b"%PDF fake")
        return {"task_id": task_id, "pdf_path": str(path), "html_path": None}

    monkeypatch.setattr(note_pdf, "export_pdf", export)
    return calls


def _recording(tmp_path: Path, name: str = "week-8.mov") -> Path:
    folder = tmp_path / "lectures"
    folder.mkdir(exist_ok=True)
    source = folder / name
    source.write_bytes(b"original")
    return source


def _task(source: Path | None, note: str = "") -> str:
    task_id = f"of-{uuid.uuid4().hex[:8]}"
    frames = _artifact_storage_dir() / task_id / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "ts_0130_1.jpg").write_bytes(b"jpeg")
    result = _attach_result_artifacts(task_id, {
        "filename": source.name if source else "upload.mov",
        "display_title": source.stem if source else "upload",
        "transcript_text": "第一句。第二句。",
        "raw_segments": [{"start": 0.0, "end": 2.0, "text": "第一句。"}, {"start": 2.0, "end": 4.0, "text": "第二句。"}],
        "summary_markdown": note,
        "summary_status": "completed" if note else "skipped",
    })
    cut_list = _artifact_storage_dir() / task_id / "debreath" / "x_cut_list.json"
    cut_list.parent.mkdir(parents=True, exist_ok=True)
    cut_list.write_text("{}", encoding="utf-8")
    result["artifacts"]["debreath_cut_list"] = {"kind": "debreath_cut_list", "filename": "debreath/x_cut_list.json"}
    metadata = {"folder_intake": {"chosen_with": "folder_path", "original_path": str(source)}} if source else {}
    job_store.upsert_job(task_id=task_id, status="completed", result=result, metadata=metadata)
    return task_id


NOTE = f"# 第八周\n\n要点。\n\n![画面](/jobs/{{task}}/artifacts/frame?file=ts_0130_1.jpg)\n"


def test_everything_lands_in_one_folder_beside_the_recording(tmp_path, pdfs):
    source = _recording(tmp_path)
    task = _task(source)
    job_store.update_job_result(task, {**job_store.get_job(task)["result"], "summary_markdown": NOTE.format(task=task)})

    record = output_folder.sync(task)

    folder = source.parent / "week-8"
    process = folder / "过程文件"
    assert record["path"] == str(folder) and record["error"] is None
    assert (folder / "week-8_笔记.pdf").is_file()
    assert (process / "week-8.txt").read_text(encoding="utf-8").startswith("第一句")
    assert (process / "week-8.srt").is_file()
    assert (process / "week-8_剪辑表.json").is_file()
    note = (process / "week-8_笔记.md").read_text(encoding="utf-8")
    assert "](截图/ts_0130_1.jpg)" in note, "the markdown shows its screenshot from beside it"
    assert (process / "截图" / "ts_0130_1.jpg").read_bytes() == b"jpeg"
    assert source.read_bytes() == b"original" and source.parent == tmp_path / "lectures"
    assert job_store.get_job(task)["result"]["output_folder"]["path"] == str(folder)


def test_the_note_is_refreshed_when_it_changes_and_only_then(tmp_path, pdfs):
    source = _recording(tmp_path)
    task = _task(source, note="# 第八周\n\n第一版。\n")
    output_folder.sync(task)
    output_folder.sync(task)
    assert len(pdfs) == 1, "an unchanged note is not exported again"

    job_store.update_job_result(task, {**job_store.get_job(task)["result"], "summary_markdown": "# 第八周\n\n改过的版本。\n"})
    output_folder.sync(task)

    assert len(pdfs) == 2
    md = source.parent / "week-8" / "过程文件" / "week-8_笔记.md"
    assert "改过的版本" in md.read_text(encoding="utf-8")


def test_a_note_saved_later_schedules_a_refresh(tmp_path, pdfs, monkeypatch):
    source = _recording(tmp_path)
    task = _task(source, note="# 第八周\n\n第一版。\n")
    output_folder.sync(task)
    scheduled: list[str] = []
    monkeypatch.setattr(output_folder, "schedule_sync", scheduled.append)

    job_store.update_job_result(task, {**job_store.get_job(task)["result"], "summary_markdown": "# 第八周\n\n编辑器里改的。\n"})

    assert scheduled == [task]


def test_a_second_task_on_the_same_recording_gets_its_own_folder(tmp_path, pdfs):
    source = _recording(tmp_path)
    first = _task(source, note="# 第一次\n")
    second = _task(source, note="# 第二次\n")

    output_folder.sync(first)
    output_folder.sync(second)
    output_folder.sync(first)

    assert "第一次" in (source.parent / "week-8" / "过程文件" / "week-8_笔记.md").read_text(encoding="utf-8")
    assert "第二次" in (source.parent / "week-8 (2)" / "过程文件" / "week-8_笔记.md").read_text(encoding="utf-8")
    assert not (source.parent / "week-8 (3)").exists(), "a task keeps finding its own folder"


def test_a_folder_the_user_made_is_not_taken_over(tmp_path, pdfs):
    source = _recording(tmp_path)
    mine = source.parent / "week-8"
    mine.mkdir()
    (mine / "我的笔记.txt").write_text("别动", encoding="utf-8")
    task = _task(source, note="# 第八周\n")

    record = output_folder.sync(task)

    assert record["path"] == str(source.parent / "week-8 (2)")
    assert sorted(p.name for p in mine.iterdir()) == ["我的笔记.txt"]


def test_an_upload_gets_no_folder(tmp_path, pdfs):
    task = _task(None, note="# 上传的\n")

    assert output_folder.sync(task) is None
    assert pdfs == []


def test_a_folder_that_cannot_be_written_is_reported_not_raised(tmp_path, pdfs, monkeypatch):
    source = _recording(tmp_path)
    task = _task(source, note="# 第八周\n")

    def refuse(*_a, **_k):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(output_folder, "claim", refuse)

    record = output_folder.sync(task)

    assert "Operation not permitted" in record["error"]
    assert job_store.get_job(task)["status"] == "completed"


def test_a_folder_scan_skips_the_videos_it_produces(tmp_path):
    source = _recording(tmp_path)
    (source.parent / "week-8_去气口.mp4").write_bytes(b"cut")
    (source.parent / "week-8_压缩.mp4").write_bytes(b"small")

    listing = local_folder_intake.list_media(source.parent)

    assert [p.name for p in listing.files] == ["week-8.mov"]
