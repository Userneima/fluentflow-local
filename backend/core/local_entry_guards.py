"""Shared runtime guards every local-edition task entry must go through.

Extracted after review found each new local entry re-making the same
mistakes inline (unvalidated caller-supplied task ids, whole-file reads
before the size check, provider keys sent to the wrong provider, background
workers dying without a terminal state). New local routes — including the
Agent API and the composition root — must use these instead of local copies.

Guards provided:

- ``claim_task_id``: format check + atomic global reservation for every new
  task, so concurrent callers cannot steal the same id.
- ``require_owned_task_id``: validate a reference to an existing task without
  granting write access to arbitrary same-client history.
- ``read_upload_bounded``: chunked upload read that aborts with 413 as soon
  as the limit is crossed, never buffering an unbounded payload first.
- ``local_ai_kwargs``: strict provider↔key matching against the local
  credential store — a stored key is only ever sent to its own provider;
  with no matching key the provider's own env/config fallback (and its
  clear error message) applies.
- ``run_worker_with_terminal_state``: catch-all for background workers so
  any escaped exception still yields a failed job + terminal hub event, and
  cancellation yields a terminal event; ``CancellationGate`` keeps orphaned
  executor threads from resurrecting a cancelled job via progress callbacks.
"""

from __future__ import annotations

import asyncio
import re
import threading
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException, UploadFile

from backend.core.event_context import new_task_id
from backend.core.event_logger import log_event
from backend.core.job_store import create_job_if_absent, get_job, upsert_job
from backend.core.local_config import resolve_secret
from backend.core.local_error_diagnostics import diagnose_error

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

_READ_CHUNK_BYTES = 1024 * 1024


def friendly_error(error: Any) -> str:
    return str(diagnose_error(error).get("detail") or "").strip() or str(error)


def _validated_task_id_value(requested: Optional[str]) -> str:
    candidate = (requested or "").strip()
    if candidate and not TASK_ID_PATTERN.fullmatch(candidate):
        raise HTTPException(status_code=400, detail="Invalid task id")
    return candidate


def claim_task_id(
    requested: Optional[str],
    *,
    client_id: Optional[str],
    status: str = "queued",
    stage: str = "preparing",
    progress: float = 0,
) -> str:
    """Atomically reserve a caller-supplied id or a fresh generated id.

    The reservation and conflict check happen in one SQLite INSERT. A separate
    ``require_owned_task_id`` API handles trusted references to pre-created
    jobs; creation never has an "allow existing" escape hatch.
    """
    requested_value = _validated_task_id_value(requested)
    attempts = 1 if requested_value else 5
    for _ in range(attempts):
        candidate = requested_value or new_task_id()
        if create_job_if_absent(
            task_id=candidate,
            status=status,
            client_id=client_id,
            stage=stage,
            progress=progress,
        ):
            return candidate
    if requested_value:
        raise HTTPException(status_code=409, detail="Task id already exists")
    raise RuntimeError("Could not reserve a unique task id")


def require_owned_task_id(
    requested: str,
    *,
    client_id: Optional[str],
    allowed_statuses: set[str] | frozenset[str] | None = None,
) -> str:
    """Validate an existing task reference and require client ownership."""
    candidate = _validated_task_id_value(requested)
    if not candidate:
        raise HTTPException(status_code=400, detail="Task id is required")
    existing = get_job(candidate, client_id=client_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if allowed_statuses is not None and existing.get("status") not in allowed_statuses:
        raise HTTPException(status_code=409, detail="Task is not in a reusable state")
    return candidate


async def read_upload_bounded(upload: UploadFile, limit_mb: float) -> bytearray:
    """Read an upload in chunks, aborting with 413 the moment the configured
    limit is crossed — the oversized tail is never buffered."""
    max_bytes = int(limit_mb * 1024 * 1024) if limit_mb > 0 else None
    await upload.seek(0)
    content = bytearray()
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File is too large. Limit is {limit_mb:g} MB.",
            )
        content.extend(chunk)
    return content


_PROVIDER_KEY_NAMES = (
    ("deepseek", "deepseek_api_key"),
    ("openai", "openai_api_key"),
    ("qwen", "qwen_api_key"),
)


def local_ai_kwargs(
    *,
    deepseek_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    qwen_api_key: Optional[str] = None,
    ai_provider: Optional[str] = None,
    ai_model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    note_mode: Optional[str] = None,
) -> dict[str, Any]:
    """Build summarizer kwargs with STRICT provider↔key matching.

    A key (form value first, then the local credential store) is only ever
    attached for the provider it belongs to. If the selected provider has no
    stored key, ``api_key`` stays unset so ``ai_client`` falls back to that
    provider's own environment variable — or fails with its own clear
    "key not configured" message — instead of receiving another provider's
    credential. With no provider selected, the first provider that has a key
    (deepseek → openai → qwen, matching the product default order) is used.
    """
    form_values = {
        "deepseek_api_key": deepseek_api_key,
        "openai_api_key": openai_api_key,
        "qwen_api_key": qwen_api_key,
    }
    resolved = {
        provider: resolve_secret(form_values[key_name], key_name)
        for provider, key_name in _PROVIDER_KEY_NAMES
    }
    provider_name = (ai_provider or "").strip().lower()
    if not provider_name:
        provider_name = next(
            (provider for provider, _ in _PROVIDER_KEY_NAMES if resolved[provider]), ""
        )
    kwargs: dict[str, Any] = {}
    if provider_name:
        kwargs["provider"] = provider_name
    matching_key = resolved.get(provider_name)
    if matching_key:
        kwargs["api_key"] = matching_key
    if (model := (ai_model or "").strip()):
        kwargs["model"] = model
    if (prompt := (system_prompt or "").strip()):
        kwargs["system_prompt"] = prompt
    if (mode := (note_mode or "").strip()):
        kwargs["note_mode"] = mode
    return kwargs


class CancellationGate:
    """Thread-safe flag for progress callbacks running in executor threads.

    Cancelling an asyncio task does NOT stop an already-running executor
    thread (e.g. a yt-dlp download). Without this gate the orphaned thread's
    progress callbacks keep upserting ``running`` state and resurrect a job
    the user already cancelled. Callbacks must no-op once ``closed``.
    """

    def __init__(self) -> None:
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def cancellation_event(self) -> threading.Event:
        """Signal shared with blocking workers that support cancellation."""
        return self._closed


async def run_worker_with_terminal_state(
    *,
    task_id: str,
    client_id: Optional[str],
    hub: Any,
    route: str,
    stage: str,
    worker: Callable[[], Awaitable[None]],
    gate: CancellationGate | None = None,
) -> None:
    """Run a background worker with a guaranteed terminal outcome.

    Entry-specific failure handling stays inside ``worker``; this is the
    catch-all so nothing can die silently: an escaped exception persists a
    failed job and publishes a terminal ``error`` event, and cancellation
    publishes a terminal event (the cancel route persists the cancelled
    status). ``gate`` is closed on every exit path so orphaned executor
    threads stop writing job state.
    """
    async def fail_worker(detail: str, raw_error: str) -> None:
        log_event(
            task_id=task_id,
            event_name="task_failed",
            stage=stage,
            success=False,
            error_reason=detail,
            metadata={"route": route, "raw_error": raw_error},
        )
        upsert_job(
            task_id=task_id,
            status="failed",
            client_id=client_id,
            stage=stage,
            progress=100,
            error_reason=detail,
        )
        await hub.publish(task_id, {"stage": "error", "progress": 100, "error": detail})

    try:
        await worker()
        job = get_job(task_id, client_id=client_id)
        if not job or job.get("status") not in {"completed", "failed", "cancelled"}:
            detail = "Worker exited without a terminal state"
            await fail_worker(detail, detail)
    except asyncio.CancelledError:
        current = get_job(task_id, client_id=client_id)
        if not current or current.get("status") != "cancelled":
            upsert_job(
                task_id=task_id,
                status="cancelled",
                client_id=client_id,
                stage=stage,
                progress=0,
                error_reason="worker_cancelled",
            )
        await hub.publish(
            task_id, {"stage": "error", "progress": 0, "error": "Task cancelled"}
        )
    except Exception as exc:
        detail = friendly_error(exc)
        await fail_worker(detail, str(exc))
    finally:
        if gate is not None:
            gate.close()
