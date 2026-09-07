"""Shared job read-side router: live event stream and source/artifact file
reads, assembled with an edition-specific client scope and job-event hub.

Read-only. Job mutation (cancel, retry, delete, transcript/summary patch,
playback-audio upload) stays out of this router.

The job store and artifact/source storage layout are edition-neutral and
imported directly. Only the client scope and the live job-event hub differ
between editions and are injected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Optional, Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from backend.core.job_store import get_job
from backend.core.result_artifacts import (
    DEBREATH_ARTIFACT_SUFFIXES,
    artifact_search_dirs,
    resolve_artifact_file,
)
from backend.core.visual_note_job import (
    VISUAL_NOTE_ARTIFACT_FILENAME,
    VISUAL_NOTE_ARTIFACT_SUFFIXES,
    VISUAL_NOTE_KIND,
)
from backend.core.storage_paths import _artifact_storage_dir, find_source_file


class JobEventStream(Protocol):
    def subscribe(self, task_id: str, *, since: int = 0) -> AsyncIterator[str]:
        """Yield server-sent-event chunks for a task from ``since``."""


_ARTIFACT_SUFFIXES = {
    "transcript_txt": ".txt",
    "transcript_srt": ".srt",
    "transcript_vtt": ".vtt",
    "transcript_bilingual_srt": ".srt",
    "transcript_bilingual_vtt": ".vtt",
    "summary_md": ".md",
    "playback_audio": ".mp3",
    "playback_audio_enhanced": ".m4a",
    "frame": ".jpg",
    # Downloadable wherever a result is readable. The local edition has no route
    # that starts a de-breath yet; a cut list synced from elsewhere is still a
    # file the owner may fetch.
    **DEBREATH_ARTIFACT_SUFFIXES,
    # Same reasoning: downloadable wherever the result is readable, including a
    # result synced from the machine that generated it.
    **VISUAL_NOTE_ARTIFACT_SUFFIXES,
}


def create_job_read_router(
    *,
    request_client_scope: Callable[[Request], Optional[str]],
    job_events: JobEventStream,
) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs/{task_id}/events")
    async def stream_job_events(request: Request, task_id: str, since: int = 0) -> StreamingResponse:
        job = get_job(task_id, client_id=request_client_scope(request))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return StreamingResponse(
            job_events.subscribe(task_id, since=since),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/jobs/{task_id}/source")
    def download_job_source(request: Request, task_id: str) -> FileResponse:
        if not get_job(task_id, client_id=request_client_scope(request)):
            raise HTTPException(status_code=404, detail="Source file not found")
        source = find_source_file(task_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source file not found")
        return FileResponse(path=str(source), filename=source.name)

    @router.get("/jobs/{task_id}/artifacts/{kind}")
    def download_job_artifact(request: Request, task_id: str, kind: str) -> FileResponse:
        job = get_job(task_id, client_id=request_client_scope(request))
        if not job:
            raise HTTPException(status_code=404, detail="Artifact not found")
        suffix = _ARTIFACT_SUFFIXES.get(kind)
        if not suffix:
            raise HTTPException(status_code=404, detail="Artifact not found")
        target_dir = _artifact_storage_dir() / task_id
        if not target_dir.is_dir():
            raise HTTPException(status_code=404, detail="Artifact not found")

        if kind == "frame":
            frame_file = request.query_params.get("file", "").strip()
            if not frame_file or ".." in frame_file or "/" in frame_file or "\\" in frame_file:
                raise HTTPException(status_code=404, detail="Artifact not found")
            target = target_dir / "frames" / frame_file
            if target.is_file():
                return FileResponse(path=str(target), filename=target.name)
            raise HTTPException(status_code=404, detail="Artifact not found")

        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        artifact = (result.get("artifacts") or {}).get(kind) if isinstance(result.get("artifacts"), dict) else None
        # The record may name a subdirectory — the de-breath outputs live in one —
        # so it is resolved rather than reduced to a basename.
        recorded = resolve_artifact_file(target_dir, (artifact or {}).get("filename")) if isinstance(artifact, dict) else None
        if recorded is not None:
            return FileResponse(path=str(recorded), filename=recorded.name)
        matches = sorted(
            file
            for directory in artifact_search_dirs(target_dir, kind)
            for file in directory.glob(f"*{suffix}")
            if file.is_file()
        )
        if kind == "summary_md":
            matches = [path for path in matches if path.name.endswith("_summary.md")]
        elif kind == VISUAL_NOTE_KIND:
            # Both notes are .md in the same directory, so the glob fallback has
            # to name this one or it would serve whichever sorts first.
            matches = [path for path in matches if path.name == VISUAL_NOTE_ARTIFACT_FILENAME]
        elif kind == "transcript_bilingual_srt":
            matches = [path for path in matches if path.name.endswith("_bilingual_zh.srt")]
        elif kind == "transcript_bilingual_vtt":
            matches = [path for path in matches if path.name.endswith("_bilingual_zh.vtt")]
        elif kind == "transcript_txt":
            matches = [path for path in matches if not path.name.endswith("_summary.md")]
        if not matches:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(path=str(matches[0]), filename=matches[0].name)

    return router
