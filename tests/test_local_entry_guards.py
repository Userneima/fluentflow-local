import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

import backend.core.local_entry_guards as guards
from backend.core.local_entry_guards import (
    CancellationGate,
    claim_task_id,
    local_ai_kwargs,
    read_upload_bounded,
    require_owned_task_id,
    run_worker_with_terminal_state,
)


# ---- task id claims and owned references ---------------------------------------

def _install_jobs(monkeypatch, jobs: dict[str, dict]):
    def fake_get(task_id, client_id=None, **kwargs):
        job = jobs.get(task_id)
        if job is None:
            return None
        if client_id is not None and job.get("client_id") != client_id:
            return None
        return job

    monkeypatch.setattr(guards, "get_job", fake_get)


def _install_claims(monkeypatch, claimed: set[str]):
    def fake_create(**values):
        task_id = values["task_id"]
        if task_id in claimed:
            return False
        claimed.add(task_id)
        return True

    monkeypatch.setattr(guards, "create_job_if_absent", fake_create)


def test_empty_task_id_mints_and_claims_new(monkeypatch):
    claimed: set[str] = set()
    _install_claims(monkeypatch, claimed)
    first = claim_task_id("", client_id="a")
    second = claim_task_id(None, client_id="a")
    assert first and second and first != second
    assert claimed == {first, second}


@pytest.mark.parametrize("bad", ["../etc", "a/b", "a b", ".hidden", "-lead", "x" * 129, "任务"])
def test_malformed_task_id_is_400(monkeypatch, bad):
    _install_claims(monkeypatch, set())
    with pytest.raises(HTTPException) as excinfo:
        claim_task_id(bad, client_id="a")
    assert excinfo.value.status_code == 400


def test_fresh_valid_task_id_is_claimed(monkeypatch):
    claimed: set[str] = set()
    _install_claims(monkeypatch, claimed)
    assert claim_task_id("job-1_A", client_id="a") == "job-1_A"
    assert claimed == {"job-1_A"}


def test_existing_task_id_always_conflicts_when_claiming(monkeypatch):
    _install_claims(monkeypatch, {"t1"})
    with pytest.raises(HTTPException) as excinfo:
        claim_task_id("t1", client_id="mine")
    assert excinfo.value.status_code == 409


def test_owned_task_reference_requires_allowed_state(monkeypatch):
    _install_jobs(
        monkeypatch,
        {"t1": {"task_id": "t1", "client_id": "mine", "status": "queued"}},
    )
    assert require_owned_task_id(
        "t1", client_id="mine", allowed_statuses={"queued", "running"}
    ) == "t1"

    _install_jobs(
        monkeypatch,
        {"t1": {"task_id": "t1", "client_id": "mine", "status": "completed"}},
    )
    with pytest.raises(HTTPException) as excinfo:
        require_owned_task_id(
            "t1", client_id="mine", allowed_statuses={"queued", "running"}
        )
    assert excinfo.value.status_code == 409


def test_owned_task_reference_rejects_another_client(monkeypatch):
    _install_jobs(
        monkeypatch,
        {"t1": {"task_id": "t1", "client_id": "other", "status": "queued"}},
    )
    with pytest.raises(HTTPException) as excinfo:
        require_owned_task_id("t1", client_id="mine")
    assert excinfo.value.status_code == 404


# ---- read_upload_bounded --------------------------------------------------------

def _upload(content: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(content), filename="f.txt")


def test_bounded_read_returns_content_under_limit():
    data = asyncio.run(read_upload_bounded(_upload(b"hello"), limit_mb=1.0))
    assert data == b"hello"
    assert isinstance(data, bytearray)


def test_bounded_read_aborts_at_limit():
    big = b"x" * (3 * 1024 * 1024)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(read_upload_bounded(_upload(big), limit_mb=1.0))
    assert excinfo.value.status_code == 413


def test_bounded_read_unlimited_when_no_limit():
    data = asyncio.run(read_upload_bounded(_upload(b"y" * 2048), limit_mb=0))
    assert len(data) == 2048


# ---- local_ai_kwargs (strict provider↔key) --------------------------------------

def _install_secrets(monkeypatch, stored: dict[str, str]):
    monkeypatch.setattr(
        guards, "resolve_secret",
        lambda form_value, name: (form_value or "").strip() or stored.get(name),
    )


def test_key_only_sent_to_its_own_provider(monkeypatch):
    _install_secrets(monkeypatch, {"deepseek_api_key": "ds-key"})
    kwargs = local_ai_kwargs(ai_provider="qwen")
    assert kwargs["provider"] == "qwen"
    assert "api_key" not in kwargs  # deepseek key must NOT leak to qwen


def test_matching_key_is_attached(monkeypatch):
    _install_secrets(monkeypatch, {"qwen_api_key": "qw-key", "deepseek_api_key": "ds-key"})
    kwargs = local_ai_kwargs(ai_provider="qwen")
    assert kwargs["api_key"] == "qw-key"


def test_provider_inferred_from_available_key(monkeypatch):
    _install_secrets(monkeypatch, {"openai_api_key": "oa-key"})
    kwargs = local_ai_kwargs()
    assert kwargs["provider"] == "openai"
    assert kwargs["api_key"] == "oa-key"


def test_no_keys_leaves_provider_defaulting(monkeypatch):
    _install_secrets(monkeypatch, {})
    kwargs = local_ai_kwargs(ai_model="m1", system_prompt="s", note_mode="direct")
    assert "provider" not in kwargs and "api_key" not in kwargs
    assert kwargs == {"model": "m1", "system_prompt": "s", "note_mode": "direct"}


def test_form_key_overrides_stored(monkeypatch):
    _install_secrets(monkeypatch, {"deepseek_api_key": "stored"})
    kwargs = local_ai_kwargs(deepseek_api_key="form", ai_provider="deepseek")
    assert kwargs["api_key"] == "form"


# ---- run_worker_with_terminal_state ---------------------------------------------

class _Hub:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, task_id, event):
        self.published.append((task_id, event))


def test_escaped_exception_yields_failed_job_and_terminal_event(monkeypatch):
    upserts: list[dict] = []
    events: list[dict] = []
    monkeypatch.setattr(guards, "upsert_job", lambda **values: upserts.append(values))
    monkeypatch.setattr(guards, "log_event", lambda **values: events.append(values))
    hub = _Hub()
    gate = CancellationGate()

    async def worker():
        raise OSError("disk full")

    asyncio.run(
        run_worker_with_terminal_state(
            task_id="t1", client_id="a", hub=hub, route="/x", stage="import",
            worker=worker, gate=gate,
        )
    )

    assert upserts and upserts[0]["status"] == "failed"
    assert upserts[0]["error_reason"].strip()
    assert hub.published and hub.published[0][1]["stage"] == "error"
    assert events and events[0]["event_name"] == "task_failed"
    assert gate.closed


def test_cancellation_publishes_terminal_and_closes_gate(monkeypatch):
    upserts: list[dict] = []
    monkeypatch.setattr(guards, "upsert_job", lambda **values: upserts.append(values))
    hub = _Hub()
    gate = CancellationGate()

    async def scenario():
        started = asyncio.Event()

        async def worker():
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(
            run_worker_with_terminal_state(
                task_id="t2", client_id="a", hub=hub, route="/x", stage="import",
                worker=worker, gate=gate,
            )
        )
        await started.wait()
        task.cancel()
        await task  # guard swallows the cancellation after publishing

    asyncio.run(scenario())
    assert hub.published[-1][1] == {"stage": "error", "progress": 0, "error": "Task cancelled"}
    assert upserts[-1]["status"] == "cancelled"
    assert gate.closed


def test_clean_completion_touches_nothing(monkeypatch):
    upserts: list[dict] = []
    monkeypatch.setattr(guards, "upsert_job", lambda **values: upserts.append(values))
    monkeypatch.setattr(
        guards,
        "get_job",
        lambda task_id, client_id=None: {"task_id": task_id, "status": "completed"},
    )
    hub = _Hub()
    gate = CancellationGate()

    async def worker():
        return None

    asyncio.run(
        run_worker_with_terminal_state(
            task_id="t3", client_id="a", hub=hub, route="/x", stage="import",
            worker=worker, gate=gate,
        )
    )
    assert upserts == [] and hub.published == []
    assert gate.closed


def test_clean_return_without_terminal_job_is_failed(monkeypatch):
    upserts: list[dict] = []
    monkeypatch.setattr(guards, "upsert_job", lambda **values: upserts.append(values))
    monkeypatch.setattr(
        guards,
        "get_job",
        lambda task_id, client_id=None: {"task_id": task_id, "status": "running"},
    )
    monkeypatch.setattr(guards, "log_event", lambda **values: None)
    hub = _Hub()

    async def worker():
        return None

    asyncio.run(
        run_worker_with_terminal_state(
            task_id="t4", client_id="a", hub=hub, route="/x", stage="import",
            worker=worker,
        )
    )

    assert upserts[-1]["status"] == "failed"
    assert "without a terminal state" in upserts[-1]["error_reason"]
    assert hub.published[-1][1]["stage"] == "error"
