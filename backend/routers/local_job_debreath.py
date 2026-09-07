"""Local-edition breath-gap removal route.

Wires the shared de-breath router to the local client scope. It passes neither
hosted hook: there are no desktop-sync jobs in this application, so nothing is
read-only, and there is no shared server to meter — the single render slot in
``debreath_job`` is the guard that matters on one machine, and it is claimed
while the caller is still waiting on the response.

Classification note: ``local_ready``. The factory's whole import graph is
local/shared (``debreath_job`` → ``silence_cuts`` → stdlib + ffmpeg), and no
hosted account, quota, or desktop-sync module is reachable from it.
"""

from typing import Any, Optional

from fastapi import BackgroundTasks, Request

from backend.core.local_request_scope import request_client_id
from backend.routers.job_debreath import create_job_debreath_router, start_debreath


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


router = create_job_debreath_router(request_client_scope=_local_client_scope)


def start_local_debreath(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The local de-breath entry, for callers other than the route itself.

    The local Agent API reaches this so an agent goes through the same guards the
    page does, rather than a parallel implementation that can drift.
    """
    return start_debreath(
        request, task_id, background_tasks, payload, request_client_scope=_local_client_scope
    )
