"""The agent route that exports a task's note (``POST /tasks/{task_id}/exports``):
Feishu through lark-cli or the OpenAPI, and PDF."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from backend.core.event_context import event_metadata
from backend.core.job_store import append_job_result_list_item
from backend.core import feishu_sync
from backend.core.lark_cli_exporter import export_markdown_via_lark_cli
from backend.core.lark_exporter import export_markdown_to_lark
from backend.core.local_config import get_preference, resolve_secret
from backend.core.local_entry_guards import friendly_error
from backend.core import note_prompt
from backend.core import note_pdf, note_versions
from backend.core.local_request_scope import require_local_agent_access
from backend.core.note_title import resolve_lark_doc_title
from backend.core.storage_paths import _artifact_storage_dir
from backend.core.lark_export_target import _local_lark_export_target, _record_export_on_task
from backend.routers.agent_v1 import common
from backend.routers.agent_v1.common import (
    _job_for_request,
    _task_brief,
)

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

@router.post("/tasks/{task_id}/exports")
async def export_agent_task(
    request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)
) -> dict[str, Any]:
    payload = payload or {}
    client_id = common._local_client_scope(request)
    job = _job_for_request(request, task_id)
    result = dict(job.get("result") or {})
    markdown = str(payload.get("markdown") or result.get("summary_markdown") or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="No markdown note available to export")
    target = str(payload.get("target") or "lark").strip().lower()
    if target == "pdf":
        try:
            exported = await asyncio.get_running_loop().run_in_executor(
                None, lambda: note_pdf.export_pdf(task_id, client_id=client_id, output_dir=payload.get("output_dir")),
            )
        except note_pdf.NotePdfError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "target": "pdf", **exported}
    if target not in {"lark", "feishu"}:
        raise HTTPException(status_code=400, detail="target is lark or pdf")

    title = str(
        payload.get("title") or result.get("display_title") or job.get("source_filename") or task_id
    ).strip()
    resolved_title = resolve_lark_doc_title(markdown, filename_stem="", form_title=title)
    # A note a text model wrote says so at its top (decision 5.3).
    markdown = note_prompt.with_text_model_line(markdown, result)
    # The request's route and folder, else the user's export settings, else
    # auto. Local-owner routes only; hosted account OAuth is rejected inside.
    # "auto" asks lark-cli whether it is signed in; keep that off the event loop.
    route_choice = str(payload.get("lark_export_route") or "").strip() or get_preference("lark_export_route") or "auto"
    folder_token = str(payload.get("folder_token") or "").strip() or get_preference("lark_folder_token")
    export_target = await asyncio.get_running_loop().run_in_executor(
        None, _local_lark_export_target, route_choice, payload.get("lark_via_cli")
    )
    kwargs: dict[str, Any] = {}
    if (app_id := resolve_secret(payload.get("lark_app_id"), "lark_app_id")):
        kwargs["app_id"] = app_id
    if (app_secret := resolve_secret(payload.get("lark_app_secret"), "lark_app_secret")):
        kwargs["app_secret"] = app_secret
    if folder_token:
        kwargs["folder_token"] = folder_token

    route = "/agent/v1/tasks/{task_id}/exports"
    started_at = time.perf_counter()
    common.log_event(
        task_id=task_id,
        event_name="agent_export_started",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        summary_length=len(markdown),
        stage="export",
        export_target=export_target,
        metadata=event_metadata(route=route, target=target, doc_title=resolved_title),
    )
    try:
        loop = asyncio.get_running_loop()
        if export_target == "lark_cli":
            # A note already kept in Feishu updates its document there.
            previous = feishu_sync.recorded_sync(job.get("result"))
            export_response = await loop.run_in_executor(
                None,
                lambda: export_markdown_via_lark_cli(
                    resolved_title, markdown, task_id=task_id, artifact_root=_artifact_storage_dir(),
                    previous=previous,
                    keep_feishu=feishu_sync.keeper(task_id, job.get("result"), note_versions.EXPORT) if previous else None,
                ),
            )
        else:
            export_response = await loop.run_in_executor(
                None,
                lambda: export_markdown_to_lark(
                    resolved_title,
                    markdown,
                    task_id=task_id,
                    artifact_root=_artifact_storage_dir(),
                    **kwargs,
                ),
            )
    except Exception as exc:
        detail = friendly_error(exc)
        common.log_event(
            task_id=task_id,
            event_name="agent_export_completed",
            source_type=job.get("source_type"),
            source_filename=job.get("source_filename"),
            summary_length=len(markdown),
            stage="export",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=False,
            error_reason=detail,
            export_target=export_target,
            metadata=event_metadata(route=route, target=target, raw_error=str(exc)),
        )
        raise HTTPException(status_code=500, detail=detail) from exc

    if isinstance(export_response, dict):
        export_response["doc_title"] = resolved_title
        export_response["task_id"] = task_id
    export_record = {
        "target": target,
        "route": export_target,
        "title": resolved_title,
        "url": export_response.get("url") if isinstance(export_response, dict) else None,
        "response": export_response,
    }
    updated = append_job_result_list_item(
        task_id,
        "exports",
        export_record,
        client_id=client_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    if export_record["url"]:
        # Where the editor reads the export from, same as the web export.
        _record_export_on_task(
            task_id, client_id, lark_doc_title=resolved_title, lark_response=export_response, lark_error=None,
        )
        updated = common.get_job(task_id, client_id=client_id) or updated
    common.log_event(
        task_id=task_id,
        event_name="agent_export_completed",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        summary_length=len(markdown),
        stage="export",
        duration_seconds=round(time.perf_counter() - started_at, 3),
        success=True,
        export_target=export_target,
        feishu_doc_url=export_record["url"],
        metadata=event_metadata(route=route, target=target, doc_title=resolved_title),
    )
    return {"ok": True, "task_id": task_id, "export": export_record, "task": _task_brief(updated)}
