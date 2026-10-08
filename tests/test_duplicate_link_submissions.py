"""One link, one task, unless the user asks for a second.

Requirements (2026-10-08 review: the same link submitted twice was downloaded,
transcribed and written up twice, leaving two identical records):

- A link that is already queued or running is not started again: the caller
  gets that task back, marked ``duplicate_of_active``.
- A link already completed is refused with 409 ``duplicate_link`` naming the
  existing task, until the caller confirms with ``allow_duplicate: true``.
- A failed or cancelled earlier task never blocks a new submission.
- The same video shared twice is the same link, whatever tracking parameters
  the share added; Bilibili part 2 is a different video from part 1.
- The page, the Agent API and the MCP tool all behave this way.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core import job_store
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.video_source import normalize_source_link
import backend.routers.local_video_sources as lvs
from scripts import fluentflow_mcp_server as mcp

_TOKEN = "dup-token"
VIDEO = "https://www.bilibili.com/video/BV1dupTEST01"


@pytest.fixture()
def started(monkeypatch):
    """Records what would have started; nothing is downloaded."""
    runs: list[str] = []

    async def start(**kwargs):
        runs.append(kwargs["task_id"])

    monkeypatch.setattr(lvs, "_start_behind_queue", start)
    yield runs
    # Queued and running rows left behind would be picked up as interrupted work
    # by any later test that starts the app.
    job_store.delete_jobs(
        [
            job["task_id"]
            for job in job_store.list_jobs(limit=None, client_id=LOCAL_OWNER_ID, include_result=False)
            if "BV1dupTEST01" in str((job.get("metadata") or {}).get("video_source_url") or "")
        ],
        client_id=LOCAL_OWNER_ID,
    )


@pytest.fixture()
def client(monkeypatch, started):
    from backend.local_main import create_local_app

    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    return TestClient(create_local_app())


def _existing(task_id: str, status: str, link: str) -> None:
    job_store.upsert_job(
        task_id=task_id, status=status, client_id=LOCAL_OWNER_ID, stage="done",
        source_type="video_link", source_filename="讲座",
        metadata={"route": "/video-sources/jobs", "video_source_url": link},
    )


def _submit(client: TestClient, link: str, **extra):
    return client.post("/video-sources/jobs", json={"input": link, **extra})


def _link_tasks(link: str) -> list[dict]:
    target = normalize_source_link(link)
    return [
        job for job in job_store.list_jobs(limit=None, client_id=LOCAL_OWNER_ID, include_result=False)
        if normalize_source_link(str((job.get("metadata") or {}).get("video_source_url") or "")) == target
    ]


# ── what counts as the same link ────────────────────────────────────────────

def test_the_same_video_shared_with_different_tracking_is_the_same_link():
    plain = normalize_source_link(VIDEO)
    assert normalize_source_link(f"{VIDEO}/?spm_id_from=333.1007&vd_source=abc&share_source=copy") == plain
    assert normalize_source_link(f"看这个 {VIDEO}?utm_source=wechat 很好") == plain


def test_bilibili_part_two_is_a_different_video_from_part_one():
    assert normalize_source_link(f"{VIDEO}?p=2&vd_source=x") == normalize_source_link(f"{VIDEO}?p=2")
    assert normalize_source_link(f"{VIDEO}?p=2") != normalize_source_link(f"{VIDEO}?p=3")
    assert normalize_source_link(f"{VIDEO}?p=2") != normalize_source_link(VIDEO)


def test_a_youtube_video_keeps_its_id_and_loses_the_share_tag():
    assert (
        normalize_source_link("https://www.youtube.com/watch?v=abc123&si=XYZ&utm_medium=x")
        == normalize_source_link("https://www.youtube.com/watch?v=abc123")
    )
    assert normalize_source_link("https://www.youtube.com/watch?v=abc123") != normalize_source_link(
        "https://www.youtube.com/watch?v=other"
    )


# ── the page's link route ───────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_link_already_in_progress_returns_that_task_and_starts_nothing(client, started, status):
    link = f"{VIDEO}A{status}"
    _existing(f"dup-active-{status}", status, link)

    response = _submit(client, f"{link}?vd_source=again")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duplicate_of_active"] is True
    assert body["job"]["task_id"] == f"dup-active-{status}"
    assert body["job"]["duplicate_of_active"] is True
    assert started == [], "nothing downloaded a second time"
    assert len(_link_tasks(link)) == 1, "no second record"


def test_a_link_already_completed_is_refused_with_the_existing_task(client, started):
    link = f"{VIDEO}Bdone"
    _existing("dup-done", "completed", link)

    response = _submit(client, f"{link}?spm_id_from=333")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "duplicate_link",
        "existing_task_id": "dup-done",
        "existing_status": "completed",
        "message": "这个链接已经处理过了。要再处理一次，请确认后重新提交。",
    }
    assert started == []
    assert len(_link_tasks(link)) == 1


def test_a_completed_link_is_processed_again_once_the_user_confirms(client, started):
    link = f"{VIDEO}Cagain"
    _existing("dup-confirm", "completed", link)

    response = _submit(client, link, allow_duplicate=True)

    assert response.status_code == 200, response.text
    new_id = response.json()["job"]["task_id"]
    assert new_id != "dup-confirm"
    assert response.json()["duplicate_of_active"] is False
    assert started == [new_id]


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_a_link_that_failed_or_was_cancelled_can_always_be_submitted_again(client, started, status):
    link = f"{VIDEO}D{status}"
    _existing(f"dup-{status}", status, link)

    response = _submit(client, link)

    assert response.status_code == 200, response.text
    assert response.json()["job"]["task_id"] != f"dup-{status}"
    assert len(started) == 1


def test_a_different_bilibili_part_is_not_a_duplicate(client, started):
    _existing("dup-part1", "completed", f"{VIDEO}E?p=1")

    response = _submit(client, f"{VIDEO}E?p=2")

    assert response.status_code == 200, response.text
    assert len(started) == 1


# ── the Agent API ───────────────────────────────────────────────────────────

def _agent_submit(client: TestClient, link: str, **extra):
    return client.post(
        "/agent/v1/tasks",
        headers={"x-fluentflow-access-token": _TOKEN},
        json={"input": link, "input_type": "video_link", **extra},
    )


def test_the_agent_api_answers_an_active_duplicate_with_the_existing_task(client, started):
    link = f"{VIDEO}Fagent"
    _existing("dup-agent-active", "running", link)

    response = _agent_submit(client, link)

    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == "dup-agent-active"
    assert response.json()["duplicate_of_active"] is True
    assert started == []


def test_the_agent_api_refuses_a_completed_duplicate_until_confirmed(client, started):
    link = f"{VIDEO}Gagent"
    _existing("dup-agent-done", "completed", link)

    refused = _agent_submit(client, link)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "duplicate_link"
    assert refused.json()["detail"]["existing_task_id"] == "dup-agent-done"

    confirmed = _agent_submit(client, link, allow_duplicate=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["task_id"] != "dup-agent-done"


# ── the MCP tool ────────────────────────────────────────────────────────────

def test_the_mcp_tool_passes_the_users_confirmation_through(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kwargs: sent.append(kwargs["payload"]) or {})

    mcp.submit_video_link(VIDEO)
    mcp.submit_video_link(VIDEO, allow_duplicate=True)

    assert sent[0]["allow_duplicate"] is False
    assert sent[1]["allow_duplicate"] is True


def test_the_mcp_tool_describes_what_happens_to_a_duplicate():
    tool = next(item for item in mcp.TOOL_DEFINITIONS if item["name"] == "submit_video_link")
    assert "allow_duplicate" in tool["inputSchema"]["properties"]
    text = tool["description"]
    assert "duplicate_of_active" in text and "duplicate_link" in text and "409" in text
