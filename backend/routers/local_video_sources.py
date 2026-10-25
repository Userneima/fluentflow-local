"""Local-edition video-link routes. The intake itself (resolve, download,
then the local pipeline) is ``backend/core/link_submission``, which the Agent
API calls as well."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core.job_store import list_jobs
from backend.core.link_submission import (
    _ALLOWED_COOKIE_BROWSERS,
    _ROUTE,
    allow_duplicate_requested,
    submit_video_source_job,
)
from backend.core.request_scope import local_client_scope
from backend.core.video_source import check_browser_cookies

router = APIRouter()


@router.post("/video-sources/cookie-check")
async def video_source_cookie_check(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    browser = str(payload.get("browser") or "").strip().lower()
    if browser not in _ALLOWED_COOKIE_BROWSERS:
        raise HTTPException(status_code=400, detail="不支持的浏览器")
    return await asyncio.to_thread(check_browser_cookies, browser)


@router.post("/video-sources/jobs")
async def create_video_source_job(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    job = await submit_video_source_job(
        input_text=str(payload.get("input") or "").strip(),
        title=str(payload.get("title") or "").strip(),
        raw_options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
        client_id=local_client_scope(request),
        allow_duplicate=allow_duplicate_requested(payload),
    )
    response: dict[str, Any] = {
        "ok": True,
        "job": job,
        "extra_urls_ignored": job.get("extra_urls_ignored", False),
        "duplicate_of_active": bool(job.get("duplicate_of_active")),
    }
    if job.get("warning"):
        response["warning"] = job["warning"]
    return response


@router.get("/video-sources/jobs")
def list_video_source_jobs(request: Request, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 200))
    jobs = [
        job
        for job in list_jobs(limit=200, client_id=local_client_scope(request))
        if (job.get("metadata") or {}).get("route") == _ROUTE
    ][:safe_limit]
    return {"jobs": jobs}
