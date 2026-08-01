"""Local-edition job read-side routes: live events and source/artifact reads.

Wires the shared job read-side router to the local client scope and the local
job-event hub. It does not import ``server_helpers``; storage layout comes from
the shared ``storage_paths`` helpers inside the factory.
"""

from typing import Optional

from fastapi import Request

from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_media_access import LocalMediaAccess
from backend.core.local_request_scope import request_client_id
from backend.routers.job_read import create_job_read_router


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


router = create_job_read_router(
    request_client_scope=_local_client_scope,
    job_events=JOB_EVENTS,
    media_access=LocalMediaAccess(),
)
