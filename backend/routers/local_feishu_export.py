"""Local-edition Feishu export: send existing markdown to a Feishu document.

The local edition keeps exactly the two local-owner export routes from the
design contract: the user's own Feishu app credentials (OpenAPI) or the local
``lark-cli`` login. Hosted account OAuth does not exist here, so requesting it
is a client error rather than a silent fallback. Credentials resolve through
the local credential store (form value first).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Form, HTTPException, Request

from backend.core import feishu_sync, note_versions
from backend.core.event_context import event_metadata, new_task_id
from backend.core.event_logger import log_event
from backend.core.lark_cli_exporter import export_markdown_via_lark_cli
from backend.core.lark_exporter import export_markdown_to_lark
from backend.core.job_store import get_job
from backend.core.local_config import resolve_secret
from backend.core.local_entry_guards import TASK_ID_PATTERN
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.local_request_scope import request_client_id
from backend.core.note_title import resolve_lark_doc_title
from backend.core.storage_paths import _artifact_storage_dir
from backend.core.lark_export_target import _local_lark_export_target, _record_export_on_task

router = APIRouter()
logger = logging.getLogger(__name__)

def _friendly_error(error: Any) -> str:
    """What went wrong and what to do next, in one line for the export toast."""
    diagnosis = diagnose_error(error)
    detail = str(diagnosis.get("detail") or "").strip() or str(error)
    next_action = str(diagnosis.get("next_action") or "").strip()
    return f"{detail} {next_action}".strip()


def _export_task_id(requested: Optional[str], client_id: Optional[str]) -> str:
    """Resolve the task id a manual export runs under.

    The export needs nothing from the job row (the markdown is in the
    request); the id only namespaces telemetry and artifact-image lookups. So:
    malformed → 400 (it reaches artifact paths); id of a job this client owns
    → keep it (its frame images may resolve); id that no longer exists →
    continue under a FRESH id, matching hosted behavior for deleted/pruned
    tasks; id owned by another client → 404 (their artifacts must not resolve
    into this caller's document).
    """
    candidate = (requested or "").strip()
    if not candidate:
        return new_task_id()
    if not TASK_ID_PATTERN.fullmatch(candidate):
        raise HTTPException(status_code=400, detail="Invalid task id")
    if get_job(candidate) is None:
        return new_task_id()
    if get_job(candidate, client_id=client_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return candidate


@router.post("/export-lark")
async def export_lark(
    request: Request,
    markdown: str = Form(...),
    title: Optional[str] = Form(None),
    lark_export_route: Optional[str] = Form(None),
    lark_via_cli: Optional[str] = Form(None),
    lark_app_id: Optional[str] = Form(None),
    lark_app_secret: Optional[str] = Form(None),
    folder_token: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
    source_type: Optional[str] = Form(None),
    source_filename: Optional[str] = Form(None),
    source_duration_seconds: Optional[float] = Form(None),
) -> dict[str, Any]:
    """Standalone endpoint: export existing markdown to a Feishu document."""
    loop = asyncio.get_event_loop()
    client_id = request_client_id(request) or "anonymous"
    task_id_value = _export_task_id(task_id, client_id)
    # "auto" asks lark-cli whether it is signed in; keep that off the event loop.
    export_target = await loop.run_in_executor(
        None, _local_lark_export_target, lark_export_route, lark_via_cli
    )
    resolved = resolve_lark_doc_title(markdown, filename_stem="", form_title=title)
    kwargs: dict[str, Any] = {}
    if (app_id := resolve_secret(lark_app_id, "lark_app_id")):
        kwargs["app_id"] = app_id
    if (app_secret := resolve_secret(lark_app_secret, "lark_app_secret")):
        kwargs["app_secret"] = app_secret
    if folder_token:
        kwargs["folder_token"] = folder_token

    log_event(
        task_id=task_id_value,
        event_name="lark_export_started",
        source_type=source_type,
        source_filename=source_filename,
        source_duration_seconds=source_duration_seconds,
        summary_length=len(markdown or ""),
        stage="export",
        export_target=export_target,
        metadata=event_metadata(route="/export-lark", trigger="manual", doc_title=resolved),
    )
    started_at = time.perf_counter()
    try:
        if export_target == "lark_cli":
            # A note already kept in Feishu updates its document there.
            owned = get_job(task_id_value, client_id=client_id) or {}
            previous = feishu_sync.recorded_sync(owned.get("result"))
            resp = await loop.run_in_executor(
                None,
                lambda: export_markdown_via_lark_cli(
                    resolved,
                    markdown,
                    task_id=task_id_value,
                    artifact_root=_artifact_storage_dir(),
                    previous=previous,
                    keep_feishu=feishu_sync.keeper(task_id_value, owned.get("result"), note_versions.EXPORT) if previous else None,
                ),
            )
        else:
            resp = await loop.run_in_executor(
                None,
                lambda: export_markdown_to_lark(
                    resolved,
                    markdown,
                    task_id=task_id_value,
                    artifact_root=_artifact_storage_dir(),
                    **kwargs,
                ),
            )
        if isinstance(resp, dict):
            resp["doc_title"] = resolved
            resp["task_id"] = task_id_value
            resp["export_target"] = export_target
        feishu_doc_url = resp.get("url") if isinstance(resp, dict) else None
        if feishu_doc_url:
            _record_export_on_task(
                task_id_value,
                client_id,
                lark_doc_title=resolved,
                lark_response=resp,
                lark_error=None,
            )
        log_event(
            task_id=task_id_value,
            event_name="lark_export_completed",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=source_duration_seconds,
            summary_length=len(markdown or ""),
            stage="export",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=True,
            export_target=export_target,
            feishu_doc_url=feishu_doc_url,
            metadata=event_metadata(route="/export-lark", trigger="manual", doc_title=resolved),
        )
        return resp
    except HTTPException:
        raise
    except Exception as exc:
        friendly_error = _friendly_error(exc)
        _record_export_on_task(task_id_value, client_id, lark_error=friendly_error)
        log_event(
            task_id=task_id_value,
            event_name="lark_export_completed",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=source_duration_seconds,
            summary_length=len(markdown or ""),
            stage="export",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=False,
            error_reason=friendly_error,
            export_target=export_target,
            metadata=event_metadata(
                route="/export-lark", trigger="manual", doc_title=resolved, raw_error=str(exc)
            ),
        )
        log_event(
            task_id=task_id_value,
            event_name="task_failed",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=source_duration_seconds,
            summary_length=len(markdown or ""),
            stage="export",
            success=False,
            error_reason=friendly_error,
            metadata=event_metadata(route="/export-lark", trigger="manual", raw_error=str(exc)),
        )
        raise HTTPException(status_code=500, detail=friendly_error) from exc
