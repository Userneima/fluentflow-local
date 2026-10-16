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
4. Uploads get no folder. A video downloaded from a link gets one under
   ~/Movies/FluentFlow/ named after the video, with the downloaded original in
   it beside the cut and the note (2026-10-15); a second download of the same
   title gets the next free name; a link served as subtitles only gets none.
5. A folder that cannot be written does not fail the task; the reason is kept.
6. A folder scan skips the videos this produces.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.core import job_store, local_folder_intake, note_pdf, output_folder
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir, _source_storage_dir


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


def test_subtitles_saved_later_schedule_a_refresh_and_saves_that_change_nothing_do_not(tmp_path, pdfs, monkeypatch):
    """Review 2026-10-16: subtitles saved in the editor reached the page and
    the download but not the folder beside the recording."""
    source = _recording(tmp_path)
    task = _task(source, note="# 第八周\n\n第一版。\n")
    output_folder.sync(task)
    scheduled: list[str] = []
    monkeypatch.setattr(output_folder, "schedule_sync", scheduled.append)

    job_store.update_job_result(task, {**job_store.get_job(task)["result"], "transcript_text": "改过的字幕"})
    assert scheduled == [task]

    job_store.update_job_result(task, {**job_store.get_job(task)["result"], "stt_elapsed_seconds": 3})
    assert scheduled == [task], "a save that leaves the folder's contents alone does not refresh it"


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

    monkeypatch.setattr(output_folder, "place", refuse)

    record = output_folder.sync(task)

    assert "Operation not permitted" in record["error"]
    assert job_store.get_job(task)["status"] == "completed"


def test_a_folder_scan_skips_the_videos_it_produces(tmp_path):
    source = _recording(tmp_path)
    (source.parent / "week-8_去气口.mp4").write_bytes(b"cut")
    (source.parent / "week-8_压缩.mp4").write_bytes(b"small")

    listing = local_folder_intake.list_media(source.parent)

    assert [p.name for p in listing.files] == ["week-8.mov"]


def _inside_its_own_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "lectures" / "week-8"
    folder.mkdir(parents=True)
    source = folder / "week-8.mov"
    source.write_bytes(b"original")
    return source


def test_a_recording_moved_into_its_folder_gets_no_folder_inside_it(tmp_path, pdfs):
    """Requirement (2026-10-14): when the user has moved the recording into the
    folder named after it, outputs go into that folder, not a nested one."""
    source = _inside_its_own_folder(tmp_path)
    task = _task(source, note=NOTE.format(task="{task}"))
    job_store.update_job_result(task, {**job_store.get_job(task)["result"], "summary_markdown": NOTE.format(task=task)})

    record = output_folder.sync(task)

    assert record["path"] == str(source.parent)
    assert not (source.parent / "week-8").exists(), "no folder nested inside it"
    assert (source.parent / "week-8_笔记.pdf").is_file()
    assert "](截图/ts_0130_1.jpg)" in (source.parent / "过程文件" / "week-8_笔记.md").read_text(encoding="utf-8")


def test_a_second_task_in_that_folder_overwrites_nothing(tmp_path, pdfs):
    """Requirement (2026-10-14): processing it again there keeps the earlier
    results; the new files carry (2), with their own screenshots."""
    source = _inside_its_own_folder(tmp_path)
    first = _task(source, note="# 第一次\n")
    second = _task(source, note="# 第二次\n")
    job_store.update_job_result(second, {**job_store.get_job(second)["result"], "summary_markdown": NOTE.format(task=second)})

    output_folder.sync(first)
    output_folder.sync(second)
    output_folder.sync(first)

    process = source.parent / "过程文件"
    assert "第一次" in (process / "week-8_笔记.md").read_text(encoding="utf-8")
    assert "截图 (2)/ts_0130_1.jpg" in (process / "week-8_笔记 (2).md").read_text(encoding="utf-8")
    assert (source.parent / "week-8_笔记 (2).pdf").is_file()
    assert (process / "week-8 (2).txt").is_file() and (process / "week-8.txt").is_file()
    assert not (source.parent / "week-8").exists()


def test_a_cut_moved_in_with_the_recording_is_not_replaced(tmp_path, pdfs):
    """A user-made folder with no FluentFlow marker but an earlier cut in it:
    the new task does not take that file's name."""
    source = _inside_its_own_folder(tmp_path)
    (source.parent / "week-8_去气口.mp4").write_bytes(b"kept")
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(b"new cut")

    state = output_folder.deliver_media("t-moved", source, cut, output_folder.CUT_LABEL)

    assert (source.parent / "week-8_去气口.mp4").read_bytes() == b"kept"
    assert state["delivered_path"] == str(source.parent / "week-8_去气口 (2).mp4")



def _link_task(title: str, source_name: str = "source.mp4", note: str = "") -> str:
    task_id = _task(None, note)
    stored = _source_storage_dir() / task_id
    stored.mkdir(parents=True, exist_ok=True)
    (stored / source_name).write_bytes(b"downloaded video")
    job = job_store.get_job(task_id)
    job_store.upsert_job(task_id=task_id, status="completed", metadata={
        "video_source_url": "https://youtu.be/abc", "display_title": title,
    })
    return task_id


def test_a_link_download_gets_a_folder_with_its_original(tmp_path, pdfs, monkeypatch):
    monkeypatch.setenv(output_folder.LINK_ROOT_ENV, str(tmp_path / "FluentFlow"))
    task = _link_task("产品发布会: 第一场", note="# 笔记\n\n要点。\n")

    output_folder.sync(task)

    folder = tmp_path / "FluentFlow" / "产品发布会_ 第一场"
    assert (folder / "产品发布会_ 第一场.mp4").read_bytes() == b"downloaded video"
    assert (folder / "产品发布会_ 第一场_笔记.pdf").is_file()
    assert (folder / "过程文件" / "产品发布会_ 第一场.txt").is_file()
    assert job_store.get_job(task)["result"]["output_folder"]["path"] == str(folder)


def test_a_second_download_with_the_same_title_gets_its_own_folder(tmp_path, pdfs, monkeypatch):
    monkeypatch.setenv(output_folder.LINK_ROOT_ENV, str(tmp_path / "FluentFlow"))
    first, second = _link_task("同名视频"), _link_task("同名视频")

    output_folder.sync(first)
    output_folder.sync(second)
    output_folder.sync(first)

    assert (tmp_path / "FluentFlow" / "同名视频" / "同名视频.mp4").is_file()
    assert (tmp_path / "FluentFlow" / "同名视频 (2)" / "同名视频 (2).mp4").is_file()
    assert not (tmp_path / "FluentFlow" / "同名视频 (3)").exists()


def test_a_link_served_as_subtitles_gets_no_folder(tmp_path, pdfs, monkeypatch):
    monkeypatch.setenv(output_folder.LINK_ROOT_ENV, str(tmp_path / "FluentFlow"))
    task = _link_task("只有字幕", source_name="source.srt")

    assert output_folder.sync(task) is None
    assert not (tmp_path / "FluentFlow" / "只有字幕").exists()



def test_a_link_download_keeps_working_after_fluentflows_copy_is_cleaned_up(tmp_path, pdfs, monkeypatch):
    from backend.core import debreath_job
    from backend.core.storage_paths import _source_storage_dir as stored

    monkeypatch.setenv(output_folder.LINK_ROOT_ENV, str(tmp_path / "FluentFlow"))
    task = _link_task("会被清理的视频")
    output_folder.sync(task)
    (stored() / task / "source.mp4").unlink()

    kept = tmp_path / "FluentFlow" / "会被清理的视频" / "会被清理的视频.mp4"
    assert kept.read_bytes() == b"downloaded video"
    assert debreath_job.resolve_source(task) == kept
    assert output_folder.sync(task)["path"] == str(kept.parent)



def test_a_renamed_folder_is_found_and_a_moved_one_is_not_recreated(tmp_path, pdfs):
    source = _recording(tmp_path)
    task = _task(source, note="# 笔记\n")
    first = output_folder.sync(task)
    folder = Path(first["path"])

    renamed = folder.with_name("第八周（改过名）")
    folder.rename(renamed)
    assert output_folder.sync(task)["path"] == str(renamed)

    moved = tmp_path / "elsewhere"
    moved.mkdir()
    renamed.rename(moved / renamed.name)
    record = output_folder.sync(task)
    assert "不在原来的位置" in record["error"]
    assert sorted(p.name for p in source.parent.iterdir()) == [source.name]


def test_link_videos_go_to_the_platforms_own_video_folder(monkeypatch):
    """Owner, 2026-10-16: on Windows the user's video folder is Videos; a
    Movies folder there would be a stray new one."""
    monkeypatch.delenv(output_folder.LINK_ROOT_ENV, raising=False)
    assert output_folder.link_root("darwin").parts[-2:] == ("Movies", "FluentFlow")
    assert output_folder.link_root("win32").parts[-2:] == ("Videos", "FluentFlow")
    monkeypatch.setenv(output_folder.LINK_ROOT_ENV, "/tmp/elsewhere")
    assert output_folder.link_root("win32") == Path("/tmp/elsewhere")
