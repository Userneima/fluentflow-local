from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from backend.core.frontend_paths import local_frontend_index_path


# NOTE: "agent" is NOT an API prefix — /agent is the processing-records SPA
# page. The Agent API lives under /agent/v1 only, blocked separately below so
# a direct visit or refresh of /agent still serves the app.
LOCAL_API_PREFIXES = frozenset(
    {
        "credentials",
        "events",
        "export-lark",
        "health",
        "hotword-libraries",
        "jobs",
        "process",
        "queue",
        "regenerate-summary",
        "runtime-config",
        "speaker-diarization",
        "summarize-transcript-file",
        "version",
        "video-sources",
    }
)

RESERVED_PREFIXES = frozenset(
    {
        "account",
        "admin",
        "auth",
        "desktop-sync",
        "guest-trial",
        "oss-upload-sessions",
    }
)


def create_local_spa_router(index_path: Optional[Path] = None) -> APIRouter:
    router = APIRouter()
    resolved_index = index_path or local_frontend_index_path()

    @router.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @router.get("/favicon.svg", include_in_schema=False)
    def favicon_svg() -> Response:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="16" fill="#111111"/>'
            '<path d="M16 39c9-15 23-15 32 0" fill="none" '
            'stroke="#8de7d2" stroke-width="6" stroke-linecap="round"/>'
            '<path d="M20 25h24" fill="none" stroke="#f4d77a" '
            'stroke-width="6" stroke-linecap="round"/>'
            "</svg>"
        )
        return Response(content=svg, media_type="image/svg+xml")

    def serve_index() -> FileResponse:
        if not resolved_index.exists():
            raise HTTPException(
                status_code=503,
                detail=(
                    "本地前端还没有构建：请在项目目录运行 "
                    "npm run build:frontend:local 后刷新页面。"
                ),
            )
        return FileResponse(str(resolved_index), headers={"Cache-Control": "no-cache"})

    @router.get("/", include_in_schema=False)
    def serve_frontend_index() -> FileResponse:
        return serve_index()

    @router.get("/{client_path:path}", include_in_schema=False)
    def serve_frontend_route(client_path: str) -> FileResponse:
        path = client_path or ""
        first_segment = path.split("/", 1)[0]
        blocked_prefixes = LOCAL_API_PREFIXES | RESERVED_PREFIXES | {"assets"}
        if first_segment in blocked_prefixes or "." in first_segment:
            raise HTTPException(status_code=404, detail="Not Found")
        # /agent is the SPA processing-records page; only the Agent API
        # namespace /agent/v1 is an API surface.
        if path == "agent/v1" or path.startswith("agent/v1/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return serve_index()

    return router


router = create_local_spa_router()
