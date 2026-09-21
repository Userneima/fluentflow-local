"""Local-edition visual-note route.

Wires the shared factory to the local client scope and the local credential
store. It passes neither hosted hook: there are no desktop-sync jobs in this
application, so nothing is read-only, and there is no shared server to meter —
the single slot in ``visual_note_job`` is the guard that matters on one machine,
and it is claimed while the caller is still waiting on the response.

Which Claude pays is decided by ``visual_note_channel``, not here: by default the
Claude Code login already on this machine, so ordinary use configures no
credential at all. The key resolved below is the fallback for a machine without
Claude Code — the user's own, read from the backend-owned local config or from
``ANTHROPIC_API_KEY`` in the project's .env, passed to the SDK, and never written
into a job result, an event, or a log line.

The frame-extraction policy passed in is the local one — ffmpeg on this machine,
or disabled — rather than the shared provider, which can also route to a cloud
worker. That injection is why the shared factory stays edition-neutral.

Classification note: ``local_ready``. The import graph is local/shared
(``visual_note_job`` → ``visual_note_channel`` → ``claude_code_note`` or
``claude_vision``; ``local_keyframe_provider`` → ``frame_extractor`` → ffmpeg),
and no hosted account, quota, or desktop-sync module is reachable from it.
"""

from typing import Any, Optional

from fastapi import BackgroundTasks, Request

from backend.core.local_config import resolve_secret
from backend.core.local_keyframe_provider import extract_keyframes
from backend.core.local_request_scope import request_client_id
from backend.routers.job_visual_note import create_job_visual_note_router, start_visual_note


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


def _local_anthropic_key(_request: Request) -> Optional[str]:
    return resolve_secret(None, "anthropic_api_key")


router = create_job_visual_note_router(
    request_client_scope=_local_client_scope,
    resolve_api_key=_local_anthropic_key,
    keyframe_extractor=extract_keyframes,
)


def start_local_visual_note(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The local visual-note entry, for callers other than the route itself.

    The local Agent API reaches this so an agent goes through the same guards
    the page does, rather than a parallel implementation that can drift.
    """
    return start_visual_note(
        request,
        task_id,
        background_tasks,
        payload,
        request_client_scope=_local_client_scope,
        resolve_api_key=_local_anthropic_key,
        keyframe_extractor=extract_keyframes,
    )
