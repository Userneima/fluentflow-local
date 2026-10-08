"""A file chosen with the system dialog, or found in a folder, runs with the same
settings as the same file dragged in.

Requirement (2026-10-08 review): the user picks a provider, model, prompt,
note mode, visuals and "export to Feishu" once in settings. Whichever way the
file reaches FluentFlow — uploaded, picked by path, or as part of a folder — the
task must remember exactly those settings, because the pipeline, a retry and the
auto export all read them back from the stored ``queue_options``. Before this,
the by-path entries kept only a handful and silently dropped the rest.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core import job_store
from backend.routers import local_processing as lp

# Every setting the page sends with a submission, as the page spells it.
SETTINGS = {
    "ai_provider": "deepseek",
    "ai_model": "deepseek-chat",
    "system_prompt": "只记结论和待办",
    "prompt_preset": "meeting",
    "prompt_preset_label": "会议",
    "note_mode": "chapter_coverage",
    "generate_visuals": "true",
    "export_to_lark": "true",
    "lark_export_route": "cli",
    "lark_via_cli": "true",
    "folder_token": "fldcnABC",
    "skip_summary": "false",
    "stt_model": "large-v3",
    "stt_speed": "accurate",
    "speaker_diarization": "true",
    "voice_enhance": "true",
    "duration_limit_seconds": "1800",
    "title": "周一例会",
}


class _Passed:
    duration_seconds = 12.0

    def as_metadata(self):
        return {"duration_seconds": 12.0}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from backend.local_main import create_local_app

    async def no_pipeline(previous, done, ctx):
        done.set()

    monkeypatch.setattr(lp, "preflight_media_file", lambda _path: _Passed())
    monkeypatch.setattr(lp, "_run_serially", no_pipeline)
    before = {job["task_id"] for job in job_store.list_jobs(limit=None, include_result=False)}
    yield TestClient(create_local_app())
    # The faked pipeline never finishes a task; queued rows left behind would be
    # taken for interrupted work by any later test that starts the app.
    created = [job["task_id"] for job in job_store.list_jobs(limit=None, include_result=False)
               if job["task_id"] not in before]
    job_store.delete_jobs(created)


@pytest.fixture()
def recording(tmp_path):
    folder = tmp_path / "Movies" / "meetings"
    folder.mkdir(parents=True)
    path = folder / "0811-standup.mp4"
    path.write_bytes(b"recording")
    return path


def _stored_options(task_id: str) -> dict:
    job = job_store.get_job(task_id)
    assert job, task_id
    return job["metadata"]["queue_options"]


def _upload(client: TestClient) -> dict:
    response = client.post(
        "/queue/process",
        files={"files": ("0811-standup.mp4", b"recording", "video/mp4")},
        data=SETTINGS,
    )
    assert response.status_code == 200, response.text
    return _stored_options(response.json()["queued"][0]["task_id"])


def test_a_file_picked_by_path_keeps_the_same_settings_as_an_upload(client, recording):
    uploaded = _upload(client)

    response = client.post(
        "/queue/process-local-files",
        json={"paths": [str(recording)], **SETTINGS},
    )
    assert response.status_code == 200, response.text
    by_path = _stored_options(response.json()["queued"][0]["task_id"])

    assert by_path == uploaded
    # And the settings are really there, not equal because both dropped them.
    assert by_path["export_to_lark"] == "true"
    assert by_path["system_prompt"] == "只记结论和待办"
    assert by_path["ai_model"] == "deepseek-chat"
    assert by_path["generate_visuals"] == "true"


def test_a_folder_keeps_the_same_settings_as_an_upload_except_one_shared_title(client, recording):
    (recording.parent / "0812-review.mp4").write_bytes(b"recording two")
    uploaded = _upload(client)

    response = client.post(
        "/queue/process-folder",
        json={"path": str(recording.parent), **SETTINGS},
    )
    assert response.status_code == 200, response.text
    queued = response.json()["queued"]
    assert len(queued) == 2
    for item in queued:
        stored = _stored_options(item["task_id"])
        # A typed title names one file; several files keep their own names,
        # exactly as a multi-file upload does.
        assert stored == {k: v for k, v in uploaded.items() if k != "title"}


def test_json_true_and_form_true_are_stored_the_same_way(client, recording):
    uploaded = _upload(client)
    as_json = {
        **SETTINGS,
        "generate_visuals": True,
        "export_to_lark": True,
        "lark_via_cli": True,
        "speaker_diarization": True,
        "voice_enhance": True,
        "skip_summary": False,
        "duration_limit_seconds": 1800,
    }

    response = client.post("/queue/process-local-files", json={"paths": [str(recording)], **as_json})

    assert _stored_options(response.json()["queued"][0]["task_id"]) == uploaded


def test_the_page_leaving_speaker_separation_off_means_off_as_on_upload(client, recording):
    """The page sends the switch only when it is on; absent means off on both doors."""
    without = {k: v for k, v in SETTINGS.items() if k != "speaker_diarization"}
    upload = client.post(
        "/queue/process",
        files={"files": ("a.mp4", b"x", "video/mp4")},
        data=without,
    )
    by_path = client.post("/queue/process-local-files", json={"paths": [str(recording)], **without})

    uploaded = _stored_options(upload.json()["queued"][0]["task_id"])
    picked = _stored_options(by_path.json()["queued"][0]["task_id"])
    assert "speaker_diarization" not in uploaded
    assert "speaker_diarization" not in picked


def test_the_typed_title_names_a_single_picked_file(client, recording):
    response = client.post(
        "/queue/process-local-files",
        json={"paths": [str(recording)], "title": "周一例会"},
    )

    job = job_store.get_job(response.json()["queued"][0]["task_id"])
    assert job["metadata"]["display_title"] == "周一例会"
