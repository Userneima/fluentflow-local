"""Processing a folder on this machine, in place, and delivering beside the original.

The owner asked for the cut file to end up next to the recording it came from,
which a browser upload cannot do: the browser hands over bytes, never the folder.
Naming the folder is what makes "beside it" a real location — and it is the first
time this product reads and writes inside a directory the user owns, so the tests
here are mostly about restraint:

- nothing in that folder is overwritten, ever, and the name actually used is
  recorded so a renamed output is not a lost one;
- the original is opened and left alone;
- the entry refuses a path it cannot use, with a sentence, instead of queueing work
  that cannot run;
- files a previous pass produced are skipped, so running the same folder twice does
  not cut the cuts;
- and it stays out of FluentFlow's own storage, where "beside the original" would
  mean inside the store.

The hosted application must not have this route at all. A server taking a
filesystem path reads its own disk on a stranger's request; that assertion is here
so nobody wires the shared factory to it by habit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.core.debreath_job as dj
import backend.core.local_folder_intake as fi
import backend.core.local_intake_flow as flow
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.silence_cuts import CutPlan, RenderReport, TimeRange

TASK = "task-folder-intake"


@pytest.fixture()
def folder(tmp_path):
    """A user's own media folder, outside FluentFlow storage."""
    media = tmp_path / "Movies" / "lectures"
    media.mkdir(parents=True)
    (media / "0811-morning.mov").write_bytes(b"recording one")
    (media / "0812-review.mp4").write_bytes(b"recording two")
    (media / "notes.txt").write_text("not a recording", encoding="utf-8")
    (media / ".DS_Store").write_bytes(b"junk")
    return media


# ── what it will and will not take ─────────────────────────────────────────

def test_it_lists_the_recordings_and_leaves_everything_else(folder):
    listing = fi.list_media(fi.resolve_folder(str(folder)))

    assert [item.name for item in listing.files] == ["0811-morning.mov", "0812-review.mp4"]
    assert listing.skipped_unsupported == 1, "the text file"
    assert listing.total_media == 2


def test_a_second_pass_skips_what_the_first_one_produced(folder):
    (folder / f"0811-morning{fi.CUT_FILE_MARKER}.mov").write_bytes(b"already cut")

    listing = fi.list_media(fi.resolve_folder(str(folder)))

    assert [item.name for item in listing.files] == ["0811-morning.mov", "0812-review.mp4"]
    assert listing.skipped_cut_files == 1, "cutting a cut file is never what was meant"


def test_a_missing_folder_is_refused_by_name(tmp_path):
    with pytest.raises(fi.FolderIntakeError, match="找不到这个文件夹"):
        fi.resolve_folder(str(tmp_path / "nope"))


def test_a_file_is_refused_with_its_folder_named(folder):
    with pytest.raises(fi.FolderIntakeError, match="这是一个文件"):
        fi.resolve_folder(str(folder / "0811-morning.mov"))


def test_a_relative_path_is_refused_rather_than_guessed_at(folder):
    with pytest.raises(fi.FolderIntakeError, match="完整路径"):
        fi.resolve_folder("Movies/lectures")


def test_an_empty_path_asks_for_one(folder):
    with pytest.raises(fi.FolderIntakeError, match="请填"):
        fi.resolve_folder("   ")


def test_quotes_pasted_from_a_terminal_are_tolerated(folder):
    assert fi.resolve_folder(f'"{folder}"') == folder.resolve()


def test_a_folder_with_no_recordings_says_so(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(fi.FolderIntakeError, match="没有可处理的音视频文件"):
        fi.list_media(fi.resolve_folder(str(empty)))


def test_fluentflows_own_storage_is_refused(tmp_path, monkeypatch):
    """Pointing at the store would re-ingest its own outputs."""
    store = tmp_path / "store" / "sources"
    (store / "task-1").mkdir(parents=True)
    monkeypatch.setenv("FLUENTFLOW_SOURCE_DIR", str(store))

    with pytest.raises(fi.FolderIntakeError, match="FluentFlow 自己存文件的目录"):
        fi.resolve_folder(str(store / "task-1"))


def test_a_huge_folder_is_capped_and_says_it_was(tmp_path):
    many = tmp_path / "many"
    many.mkdir()
    for index in range(fi.MAX_FILES_PER_FOLDER + 5):
        (many / f"clip-{index:03d}.mp4").write_bytes(b"x")

    listing = fi.list_media(fi.resolve_folder(str(many)))

    assert len(listing.files) == fi.MAX_FILES_PER_FOLDER
    assert listing.truncated is True
    assert fi.describe(listing)["total_media"] == fi.MAX_FILES_PER_FOLDER + 5


# ── never overwriting anything in that folder ──────────────────────────────

def test_the_cut_file_goes_beside_the_original_under_a_new_name(folder):
    target = fi.cut_file_target(folder / "0811-morning.mov")

    assert target.parent == folder
    assert target.name == "0811-morning_debreath.mov"


def test_a_name_already_taken_is_never_overwritten(folder):
    (folder / "0811-morning_debreath.mov").write_bytes(b"an earlier cut the user may be using")

    target = fi.cut_file_target(folder / "0811-morning.mov")

    assert target.name == "0811-morning_debreath (2).mov"
    assert (folder / "0811-morning_debreath.mov").read_bytes().startswith(b"an earlier cut")


def test_it_keeps_counting_rather_than_giving_up_on_the_second_name(folder):
    (folder / "0811-morning_debreath.mov").write_bytes(b"a")
    (folder / "0811-morning_debreath (2).mov").write_bytes(b"b")

    assert fi.cut_file_target(folder / "0811-morning.mov").name == "0811-morning_debreath (3).mov"


# ── delivery, through the flow that will actually call it ──────────────────

def _fake_engine(monkeypatch, *, separated: bool = True, ok: bool = True):
    plan = CutPlan(
        source_duration_seconds=100.0,
        cuts=[TimeRange(10.0, 20.0)],
        keeps=[TimeRange(0.0, 10.0), TimeRange(20.0, 100.0)],
        silences_found=1,
        noise_db=-30.0,
        min_silence_seconds=0.25,
        padding_seconds=0.1,
    )
    plan.level_separation = {"measured": True, "separated": separated, "sampled_cuts": 10,
                             "narrow_cut_count": 0 if separated else 3, "narrowest_db": 15.0}
    monkeypatch.setattr(dj.silence_cuts, "plan_silence_cuts", lambda *a, **k: plan)

    def render(media, cut_plan, output, **kwargs):
        Path(output).write_bytes(b"the cut version")
        return RenderReport(
            output_path=Path(output), expected_seconds=90.0, actual_seconds=90.0,
            video_seconds=90.0, audio_seconds=90.0, frame_count=2700, batches=1,
            checks={"duration_matches_plan": ok}, source_skew_seconds=0.0,
        )

    monkeypatch.setattr(dj.silence_cuts, "render_cut_plan", render)


def test_a_folder_recording_gets_its_cut_file_delivered_beside_it(folder, monkeypatch):
    _fake_engine(monkeypatch)
    source = folder / "0811-morning.mov"

    prepared = flow.preprocess_media(TASK, source)

    delivered = folder / "0811-morning_debreath.mov"
    assert prepared.state["delivered"] is True
    assert prepared.state["delivered_path"] == str(delivered)
    assert delivered.read_bytes() == b"the cut version"
    assert source.read_bytes() == b"recording one", "the original is only read"


def test_delivery_never_replaces_a_file_already_there(folder, monkeypatch):
    _fake_engine(monkeypatch)
    existing = folder / "0811-morning_debreath.mov"
    existing.write_bytes(b"an earlier cut the user may be using")

    prepared = flow.preprocess_media(TASK, folder / "0811-morning.mov")

    assert existing.read_bytes() == b"an earlier cut the user may be using"
    assert prepared.state["delivered_name"] == "0811-morning_debreath (2).mov"
    assert (folder / "0811-morning_debreath (2).mov").read_bytes() == b"the cut version"


def test_an_upload_gets_no_delivery_because_there_is_nowhere_to_deliver(tmp_path, monkeypatch):
    """The "original" for an upload is FluentFlow's own copy, inside the store."""
    store = tmp_path / "store" / "sources" / TASK
    store.mkdir(parents=True)
    monkeypatch.setenv("FLUENTFLOW_SOURCE_DIR", str(tmp_path / "store" / "sources"))
    source = store / "source.mov"
    source.write_bytes(b"uploaded copy")
    _fake_engine(monkeypatch)

    prepared = flow.preprocess_media(TASK, source)

    assert prepared.used is True
    assert "delivered" not in prepared.state
    assert not list(store.glob("*_debreath*")), "nothing is written into the store's source dir"


def test_a_declined_cut_delivers_nothing(folder, monkeypatch):
    """It fell back to the recording, so there is no cut version to hand over."""
    _fake_engine(monkeypatch, separated=False)

    prepared = flow.preprocess_media(TASK, folder / "0811-morning.mov")

    assert prepared.used is False
    assert "delivered" not in prepared.state
    assert not list(folder.glob("*_debreath*"))


def test_a_delivery_that_cannot_be_written_is_reported_not_raised(folder, monkeypatch):
    """The task keeps its own copy, so a read-only folder costs the convenience."""
    _fake_engine(monkeypatch)

    def refuse(*_a, **_k):
        raise OSError("Read-only file system")

    monkeypatch.setattr(flow.shutil, "copyfile", refuse)

    prepared = flow.preprocess_media(TASK, folder / "0811-morning.mov")

    assert prepared.used is True, "the cut still happened and the task still has it"
    assert prepared.state["delivered"] is False
    assert "可以从页面下载" in prepared.state["delivery_error"]


# ── the route, and where it may not exist ─────────────────────────────────

# Every route that reads this machine's filesystem, named here rather than checked
# one at a time — the earlier version of this test pinned only the first of the
# three, so the other two could have been mounted on the public server with nothing
# failing. A hosted server accepting a filesystem path reads its own disk on a
# stranger's request, and a hosted server opening a file dialog opens it on the
# server's desktop.
LOCAL_ONLY_ROUTES = (
    "/queue/process-folder",
    "/queue/process-local-files",
    "/local/choose-media",
    "/local/choose-folder",
    # Answers "is this dropped file on your disk, and where". Reading someone
    # else's disk to answer that is exactly the shape of the problem above.
    "/local/locate-dropped",
)


def test_these_routes_exist_on_the_local_edition_only():
    from backend.local_main import create_local_app

    local_paths = {route.path for route in create_local_app().routes}

    for path in LOCAL_ONLY_ROUTES:
        assert path in local_paths, f"{path} should be on the local edition"


# ── the system-dialog entry, end to end through the routes ────────────────

@pytest.fixture()
def local_client(monkeypatch, folder):
    """The local app, with the dialog stubbed so no window opens."""
    from fastapi.testclient import TestClient
    from backend.local_main import create_local_app
    import backend.core.local_file_chooser as fc

    monkeypatch.setattr(fc.sys, "platform", "darwin")
    monkeypatch.setattr(fc, "osascript_path", lambda: "/usr/bin/osascript")
    return TestClient(create_local_app())


def _stub_dialog(monkeypatch, paths, *, cancelled=False):
    import backend.core.local_file_chooser as fc

    def choose(**_kwargs):
        return fc.ChooserResult(paths=[Path(p) for p in paths], cancelled=cancelled)

    monkeypatch.setattr(fc, "choose_media_files", choose)


def test_choosing_a_file_reports_its_real_path_and_folder(local_client, folder, monkeypatch):
    _stub_dialog(monkeypatch, [folder / "0811-morning.mov"])

    body = local_client.post("/local/choose-media", json={}).json()

    assert body["cancelled"] is False
    chosen = body["files"][0]
    assert chosen["usable"] is True
    assert chosen["path"] == str(folder / "0811-morning.mov")
    assert chosen["folder"] == str(folder), "the folder is the point: the cut file goes there"


def test_cancelling_the_dialog_is_not_an_error(local_client, monkeypatch):
    _stub_dialog(monkeypatch, [], cancelled=True)

    body = local_client.post("/local/choose-media", json={}).json()

    assert body["cancelled"] is True and body["files"] == []


def test_an_unusable_choice_is_reported_per_file_not_as_a_failure(local_client, folder, monkeypatch):
    _stub_dialog(monkeypatch, [folder / "notes.txt", folder / "0811-morning.mov"])

    files = local_client.post("/local/choose-media", json={}).json()["files"]

    assert files[0]["usable"] is False and "只处理音视频" in files[0]["reason"]
    assert files[1]["usable"] is True


def test_a_previously_cut_file_is_refused_with_advice(local_client, folder, monkeypatch):
    cut = folder / f"0811-morning{fi.CUT_FILE_MARKER}.mov"
    cut.write_bytes(b"already cut")
    _stub_dialog(monkeypatch, [cut])

    files = local_client.post("/local/choose-media", json={}).json()["files"]

    assert files[0]["usable"] is False and "选原片" in files[0]["reason"]


def test_processing_by_path_queues_the_file_where_it_is(local_client, folder, monkeypatch):
    """The task is created against the file's own path — nothing is copied first."""
    import backend.routers.local_processing as lp

    ran: list = []

    async def no_pipeline(previous, done, ctx):
        # Stands in for the worker so no ffmpeg runs; the context is what matters.
        ran.append(ctx.in_path)
        done.set()

    class _Passed:
        def as_metadata(self):
            return {"duration_seconds": 12.0}

    # The fixture's files are a few bytes of text with media extensions, so the
    # real preflight rejects them for being unreadable — correctly, and it is
    # tested where it belongs. Here the question is what happens to a file that
    # passes.
    monkeypatch.setattr(lp, "preflight_media_file", lambda _path: _Passed())
    monkeypatch.setattr(lp, "_run_serially", no_pipeline)

    body = local_client.post(
        "/queue/process-local-files",
        json={"paths": [str(folder / "0811-morning.mov")]},
    ).json()

    assert body["count"] == 1
    assert body["queued"][0]["filename"] == "0811-morning.mov"
    assert body["queued"][0]["task_id"]
    assert ran == [folder / "0811-morning.mov"], "the pipeline reads the user's own file"


def test_processing_refuses_a_path_it_cannot_use_before_creating_a_task(local_client, folder):
    response = local_client.post(
        "/queue/process-local-files",
        json={"paths": [str(folder / "notes.txt")]},
    )

    assert response.status_code == 400
    assert "只处理音视频" in response.json()["detail"]


def test_processing_without_paths_says_so(local_client):
    response = local_client.post("/queue/process-local-files", json={})

    assert response.status_code == 400
    assert "没有收到" in response.json()["detail"]


# ── placing a dropped file, end to end through the route ──────────────────
#
# A browser hands a dropped file's bytes to the page with its name, size and
# modification time, and never its folder. So a drop gets uploaded: a second copy
# of a gigabyte into the store, and no "beside the original" for the cut version.
# This edition is on the machine holding the file, so the page sends the three
# labels — no bytes — and asks.


def test_a_dropped_file_in_a_known_folder_comes_back_with_its_path(local_client, folder, monkeypatch):
    from backend.core import local_folder_intake as intake
    import backend.routers.local_processing as routes

    recording = folder / "0811-morning.mov"
    monkeypatch.setattr(routes, "recent_local_folders", lambda *a, **k: [str(folder)])
    info = recording.stat()

    body = local_client.post("/local/locate-dropped", json={
        "name": recording.name,
        "size_bytes": info.st_size,
        "modified_ms": info.st_mtime * 1000,
    }).json()

    assert body["found"] is True
    assert body["path"] == str(recording)
    assert body["folder"] == str(folder), "the folder is the point: the cut file goes there"
    assert intake.locate_dropped_file(recording.name, info.st_size, [folder]) == recording.resolve()


def test_a_dropped_file_nobody_has_seen_is_answered_no_not_guessed(local_client, folder, monkeypatch):
    """"No" costs nothing — the page uploads, which is what a drop did before this
    route existed. A guess would deliver a cut file beside the wrong copy."""
    import backend.routers.local_processing as routes

    monkeypatch.setattr(routes, "recent_local_folders", lambda *a, **k: [str(folder)])

    body = local_client.post("/local/locate-dropped", json={
        "name": "never-seen.mp4", "size_bytes": 4096,
    }).json()

    assert body["found"] is False and "path" not in body


# ── running one of these again ─────────────────────────────────────────────
#
# Retry looks for the recording in FluentFlow's own store, which is exactly where
# a task read in place never put it: the answer was "source file not found" for a
# file sitting in the user's folder the whole time. On 2026-08-23 that left
# restarting the service as the only way to re-run a folder that had failed.


@pytest.fixture()
def in_place_task(local_client, folder, monkeypatch):
    """A finished-and-failed task that was read from the user's own folder."""
    import backend.routers.local_processing as lp
    from backend.core import job_store

    ran: list = []

    async def no_pipeline(previous, done, ctx):
        ran.append(ctx.in_path)
        done.set()

    class _Passed:
        def as_metadata(self):
            return {"duration_seconds": 12.0}

    monkeypatch.setattr(lp, "preflight_media_file", lambda _path: _Passed())
    monkeypatch.setattr(lp, "_run_serially", no_pipeline)

    recording = folder / "0811-morning.mov"
    body = local_client.post(
        "/queue/process-local-files", json={"paths": [str(recording)]}
    ).json()
    task_id = body["queued"][0]["task_id"]
    job_store.upsert_job(task_id=task_id, status="failed", client_id=LOCAL_OWNER_ID)
    ran.clear()
    return {"task_id": task_id, "recording": recording, "ran": ran}


def test_retrying_an_in_place_task_reads_the_users_own_file_again(local_client, in_place_task):
    response = local_client.post(f"/jobs/{in_place_task['task_id']}/retry")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_task_id"] == in_place_task["task_id"]
    assert body["task_id"] != in_place_task["task_id"]
    # Nothing was copied into the store: the new task reads the original path.
    assert in_place_task["ran"] == [in_place_task["recording"]]


def test_retrying_an_in_place_task_keeps_it_in_the_serial_queue(local_client, in_place_task):
    """The retry goes back through the folder entry, so it queues like one.

    A retry that ran its own pipeline directly would process alongside whatever is
    already running — one machine, two encodes — and would skip the note the
    folder entry writes afterwards.
    """
    body = local_client.post(f"/jobs/{in_place_task['task_id']}/retry").json()

    assert body["job"]["status"] == "queued"


def test_retrying_says_where_the_file_was_when_it_has_moved(local_client, in_place_task):
    """The recording is the user's own; only they can say where it went."""
    in_place_task["recording"].unlink()

    response = local_client.post(f"/jobs/{in_place_task['task_id']}/retry")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert str(in_place_task["recording"]) in detail
    assert "Source file not found" not in detail


# ── choosing the folder, which is the entry this edition is for ─────────────
#
# The browser's own picker cannot hand over a folder — that is a deliberate
# browser boundary — so without this the only way in was one file at a time, or
# uploading gigabytes that were already on the disk.


def _stub_folder_dialog(monkeypatch, path, *, cancelled=False):
    import backend.core.local_file_chooser as fc

    def choose(**_kwargs):
        return fc.ChooserResult(paths=[Path(path)] if path else [], cancelled=cancelled)

    monkeypatch.setattr(fc, "choose_media_folder", choose)


def test_choosing_a_folder_answers_with_what_is_in_it(local_client, folder, monkeypatch):
    """The count comes back with the path, in one call.

    That number is what the next decision is about — a folder is however many
    Claude calls it has recordings in it — so it has to be on screen before the
    button, not discovered in the records afterwards.
    """
    _stub_folder_dialog(monkeypatch, folder)

    body = local_client.post("/local/choose-folder", json={}).json()

    assert body["cancelled"] is False
    assert body["path"] == str(folder)
    assert body["count"] == 2
    assert sorted(body["files"]) == ["0811-morning.mov", "0812-review.mp4"]
    assert body["skipped_unsupported"] == 1, "the text file"


def test_a_second_pass_over_the_same_folder_says_what_it_will_skip(
    local_client, folder, monkeypatch
):
    """Running a folder twice must not cut the cuts, and the count has to explain
    itself: "I chose ten and it started six" is the question this answers."""
    (folder / f"0811-morning{fi.CUT_FILE_MARKER}.mov").write_bytes(b"already cut")
    _stub_folder_dialog(monkeypatch, folder)

    body = local_client.post("/local/choose-folder", json={}).json()

    assert body["count"] == 2
    assert body["skipped_cut_files"] == 1


def test_cancelling_the_folder_dialog_is_an_ordinary_answer(local_client, monkeypatch):
    _stub_folder_dialog(monkeypatch, None, cancelled=True)

    body = local_client.post("/local/choose-folder", json={}).json()

    assert body["cancelled"] is True
    assert "path" not in body


def test_a_folder_it_cannot_use_is_refused_with_a_sentence(local_client, tmp_path, monkeypatch):
    _stub_folder_dialog(monkeypatch, tmp_path / "not-there")

    response = local_client.post("/local/choose-folder", json={})

    assert response.status_code == 400
    assert response.json()["detail"]
