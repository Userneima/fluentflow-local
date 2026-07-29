"""Local-edition job-mutation routes (cancellation and deletion).

Wires the shared job-mutation router to the local client scope and the local
job-event hub. The local edition has no quota ledger and no hosted in-process
queue, and every local request is local execution, so it passes neither the
``reconcile_cancel`` hook nor the ``reject_readonly`` desktop-sync guard.
"""

from typing import Optional

from fastapi import Request

from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_request_scope import request_client_id
from backend.routers.job_mutation import create_job_mutation_router


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


router = create_job_mutation_router(
    request_client_scope=_local_client_scope,
    job_events=JOB_EVENTS,
)
