"""Local-edition HTTP boundary middleware.

The local backend binds to loopback by default, but binding alone is not the
boundary: this middleware (1) rejects requests whose PEER address is not
loopback, and (2) rejects browser requests carrying a non-local ``Origin`` —
CORS only restricts what a page can read, not what a simple cross-site POST
can execute, so an unrelated web page must be refused server-side before it
can call privileged local endpoints.

``FLUENTFLOW_ALLOW_NON_LOOPBACK=1`` opts a user into LAN exposure and relaxes
both checks; the default is strict.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse

LOOPBACK_PEERS = {"127.0.0.1", "::1", "localhost", "testclient", ""}
LOOPBACK_ORIGIN_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _allow_non_loopback() -> bool:
    value = (os.environ.get("FLUENTFLOW_ALLOW_NON_LOOPBACK") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


async def local_boundary_middleware(request: Request, call_next):
    if not _allow_non_loopback():
        peer = (request.client.host if request.client else "") or ""
        if peer not in LOOPBACK_PEERS:
            return _forbidden("本地版仅接受本机请求。如需局域网访问，请设置 FLUENTFLOW_ALLOW_NON_LOOPBACK=1。")
        origin = (request.headers.get("origin") or "").strip()
        if origin and origin.lower() != "null":
            host = (urlparse(origin).hostname or "").lower()
            if host not in LOOPBACK_ORIGIN_HOSTS:
                return _forbidden("请求来源不是本机页面，已拒绝。")
        elif origin:
            # "null" origin (sandboxed iframe / file://) is not a local page.
            return _forbidden("请求来源不是本机页面，已拒绝。")
    return await call_next(request)
