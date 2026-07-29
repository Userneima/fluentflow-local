"""Local-edition client-event routes."""

from fastapi import Request

from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_request_scope import request_client_id
from backend.routers.client_events import create_client_events_router


def _local_client_scope(request: Request) -> str:
    return request_client_id(request) or "anonymous"


router = create_client_events_router(
    request_client_scope=_local_client_scope,
    job_events=JOB_EVENTS,
)
