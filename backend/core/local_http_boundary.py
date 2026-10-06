"""Local-edition HTTP boundary middleware.

The local backend binds to loopback by default, but binding alone is not the
boundary: this middleware (1) rejects requests whose PEER address is not
loopback, and (2) rejects browser requests carrying a non-local ``Origin`` —
CORS only restricts what a page can read, not what a simple cross-site POST
can execute, so an unrelated web page must be refused server-side before it
can call privileged local endpoints.

``FLUENTFLOW_ALLOW_NON_LOOPBACK=1`` opts a user into LAN exposure. It relaxes
the peer check only: a page served from somewhere else is still refused by its
``Origin``, and a client on another machine has to present the same
``FLUENTFLOW_ACCESS_TOKEN`` the Agent API takes before it may change anything.
Reading stays open on the LAN; writing does not.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from backend.core.local_request_scope import require_local_agent_access
from backend.core.runtime_env import env_truthy

LOOPBACK_PEERS = {"127.0.0.1", "::1", "localhost", "testclient", ""}
LOOPBACK_ORIGIN_HOSTS = {"127.0.0.1", "::1", "localhost"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _allow_non_loopback() -> bool:
    return env_truthy("FLUENTFLOW_ALLOW_NON_LOOPBACK")


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


def _origin_is_foreign(request: Request) -> bool:
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return False
    if origin.lower() == "null":
        # "null" origin (sandboxed iframe / file://) is not a local page.
        return True
    host = (urlparse(origin).hostname or "").lower()
    return host not in LOOPBACK_ORIGIN_HOSTS


async def local_boundary_middleware(request: Request, call_next):
    peer = (request.client.host if request.client else "") or ""
    if peer not in LOOPBACK_PEERS:
        if not _allow_non_loopback():
            return _forbidden("本地版仅接受本机请求。如需局域网访问，请设置 FLUENTFLOW_ALLOW_NON_LOOPBACK=1。")
        if request.method.upper() in MUTATING_METHODS:
            try:
                require_local_agent_access(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": f"局域网请求要带访问令牌才能改动数据：{exc.detail}"},
                )
    if _origin_is_foreign(request):
        return _forbidden("请求来源不是本机页面，已拒绝。")
    return await call_next(request)
