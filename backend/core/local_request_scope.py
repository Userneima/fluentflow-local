"""Request scope helpers for the local edition."""

from __future__ import annotations

import hmac
import os
import time
from typing import Optional

from fastapi import HTTPException, Request


EXECUTION_SCOPE_LOCAL = "local"

LOCAL_REQUEST_HOSTS = {"127.0.0.1", "localhost", "::1", "testclient"}

AGENT_ACCESS_TOKEN_ENV = "FLUENTFLOW_ACCESS_TOKEN"
AGENT_ACCESS_HEADER = "x-fluentflow-access-token"


def require_local_agent_access(request: Request) -> None:
    """Design contract: local Agent/API access uses an explicit local access
    token and stays DISABLED until one is configured. Not a substitute for the
    loopback/session boundary (composition-root unit)."""
    token = (os.environ.get(AGENT_ACCESS_TOKEN_ENV) or "").strip()
    if not token:
        raise HTTPException(
            status_code=403,
            detail="本地 Agent API 未启用：请先设置 FLUENTFLOW_ACCESS_TOKEN 访问令牌。",
        )
    auth = (request.headers.get("authorization") or "").strip()
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = bearer or (request.headers.get(AGENT_ACCESS_HEADER) or "").strip()
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Agent API 访问令牌无效。")
    _note_agent_request(request)


# When an agent (the MCP server, a script) last got through with the token, so
# the Agent access panel can say whether one is connected. Kept in memory: it
# answers "has an agent come by since FluentFlow started", which is what a
# person setting one up needs. A browser page reading the same API (it sends
# Sec-Fetch-* headers, which no agent client does) is not an agent.
_last_agent_request_at: Optional[float] = None


def _note_agent_request(request: Request) -> None:
    global _last_agent_request_at
    if request.headers.get("sec-fetch-mode") or request.headers.get("sec-fetch-site"):
        return
    _last_agent_request_at = time.time()


def agent_access_status() -> dict:
    return {
        "token_configured": bool((os.environ.get(AGENT_ACCESS_TOKEN_ENV) or "").strip()),
        "last_agent_request_at": _last_agent_request_at,
    }


def normalize_client_id(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    safe = "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_"})
    return safe[:96] or None


# The one owner every task on this machine belongs to. Local has one user, but
# its callers each send their own client id (the page, the MCP server, scripts,
# a second browser), and scoping by that id split one person's history four
# ways: tasks submitted through Claude never showed up on the page.
LOCAL_OWNER_ID = "local-single-user"


def request_client_id(request: Optional[Request]) -> Optional[str]:
    return LOCAL_OWNER_ID


def request_is_localhost(request: Request) -> bool:
    """Whether the request really came from this machine.

    ONLY the socket peer counts. This used to also accept the URL hostname,
    which comes from the client-supplied Host header (nginx forwards it
    verbatim), so `curl -H 'Host: localhost' -H '<execution-target>: local'`
    satisfied the localhost test on the public site and skipped the entire
    account middleware — verified live against production before this fix.
    """
    client_host = ((request.client.host if request.client else "") or "").strip().lower()
    return client_host in LOCAL_REQUEST_HOSTS
