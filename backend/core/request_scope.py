"""The owner a request's tasks belong to, as every local router reads it."""

from __future__ import annotations

from typing import Optional

from fastapi import Request

from backend.core.local_request_scope import request_client_id


def local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"
