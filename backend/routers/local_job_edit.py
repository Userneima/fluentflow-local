"""Local-edition transcript/summary edit routes.

Wires the shared job-edit router to the local client scope. Every local request
is local execution, so it passes neither the ``reject_readonly`` desktop-sync
guard nor the ``on_after_write`` desktop-sync propagation hook.
"""

from typing import Optional

from fastapi import Request

from backend.core.local_request_scope import request_client_id
from backend.routers.job_edit import create_job_edit_router


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


router = create_job_edit_router(request_client_scope=_local_client_scope)
