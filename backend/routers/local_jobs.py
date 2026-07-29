"""Local-edition job-query routes.

Wires the shared job-query router to the local client scope and the local task
projection policy. It does not import ``server_helpers`` or the hosted
task-detail policy, so ``/jobs`` and task reads stay on the shared/local graph.
"""

from typing import Optional

from fastapi import Request

from backend.core.local_request_scope import request_client_id
from backend.core.local_task_detail import build_task_detail, build_task_snapshot
from backend.routers.job_query import create_job_query_router


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


router = create_job_query_router(
    request_client_scope=_local_client_scope,
    build_task_snapshot=build_task_snapshot,
    build_task_detail=build_task_detail,
)
