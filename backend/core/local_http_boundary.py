"""Local-edition HTTP boundary middleware.

The local backend binds to loopback by default, but binding alone is not the
boundary: this middleware (1) rejects requests whose PEER address is not
loopback, and (2) rejects browser requests carrying a non-local ``Origin`` —
CORS only restricts what a page can read, not what a simple cross-site POST
can execute, so an unrelated web page must be refused server-side before it
can call privileged local endpoints.

``FLUENTFLOW_ALLOW_NON_LOOPBACK=1`` opts a user into LAN exposure. It relaxes
the peer check, and it lets the page this server serves on its LAN address
count as its own page (see ``_origin_is_own_lan_page``): a page served from
somewhere else is still refused by its ``Origin``. A client on another machine
has to present the same ``FLUENTFLOW_ACCESS_TOKEN`` the Agent API takes for
every request, reads included: transcripts and notes are the user's data. The
page sends it once it is filled in under "菜单 → Agent 接入" on that device.
Only what it takes to load the page and get to that panel is served without
it (see ``_open_without_token``). Without a token configured, other devices
get the page and nothing else.
"""

from __future__ import annotations

import os

import hmac

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from backend.core.local_request_scope import require_local_agent_access
from backend.core.runtime_env import env_truthy
from backend.routers.local_spa import LOCAL_API_PREFIXES, RESERVED_PREFIXES

LOOPBACK_PEERS = {"127.0.0.1", "::1", "localhost", "testclient", ""}
LOOPBACK_ORIGIN_HOSTS = {"127.0.0.1", "::1", "localhost"}
READ_METHODS = {"GET", "HEAD"}
# What the page needs before it has a token: the page itself, its static files,
# and the health check it opens with.
_OPEN_PATHS = {"/", "/health", "/favicon.ico", "/favicon.svg"}


def _allow_non_loopback() -> bool:
    return env_truthy("FLUENTFLOW_ALLOW_NON_LOOPBACK")


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


def _host_and_port(netloc: str) -> tuple[str, int | None]:
    parsed = urlparse(f"//{netloc}")
    try:
        port = parsed.port
    except ValueError:
        port = None
    return (parsed.hostname or "").lower(), port


def _own_mdns_names() -> set[str]:
    """This machine's own ``.local`` name, however the hostname is reported."""
    try:
        name = socket.gethostname().strip().lower().rstrip(".")
    except OSError:
        return set()
    bare = name[: -len(".local")] if name.endswith(".local") else name
    return {f"{bare}.local"} if bare else set()


def _is_ip_literal_or_own_mdns(host: str) -> bool:
    """An IP address, or this machine's own ``.local`` name.

    Any other ``.local`` name is refused: another device on the network can
    answer mDNS for a name of its choosing and point it at this machine, and a
    page it serves under that name would then pass as ours (mDNS rebinding).
    """
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return host.endswith(".local") and host in _own_mdns_names()


def _open_without_token(request: Request) -> bool:
    """Whether another device may fetch this without the access token: only
    what loads the page and lets it reach 「菜单 → Agent 接入」."""
    if request.method.upper() not in READ_METHODS:
        return False
    path = request.url.path or "/"
    if path in _OPEN_PATHS or path.startswith("/assets/"):
        return True
    # The page's own routes (/agent, /editor, …) serve the same index.html.
    segments = path.lstrip("/")
    first = segments.split("/", 1)[0]
    if segments == "agent/v1" or segments.startswith("agent/v1/"):
        return False
    return bool(first) and "." not in first and first not in (LOCAL_API_PREFIXES | RESERVED_PREFIXES | {"assets"})


def _origin_is_own_lan_page(request: Request, origin: str) -> bool:
    """Whether a LAN-mode request comes from the page this server served.

    Opened from another device as ``http://192.168.1.5:8000``, the page's
    Origin is that address, which is not loopback, so every write it made was
    refused as foreign and LAN mode was read-only in practice.

    Origin equal to the request's own Host is that page. It is accepted only
    when the host is an IP address or an mDNS ``.local`` name: a site on the
    internet cannot make a browser send our IP as its Origin, but a DNS name it
    controls can be pointed at this machine (DNS rebinding), and then its
    Origin and the Host would match too. Requests from another machine still
    need the access token on top of this, so this alone opens nothing.
    """
    if not _allow_non_loopback():
        return False
    parsed = urlparse(origin)
    origin_host = (parsed.hostname or "").lower()
    try:
        origin_port = parsed.port
    except ValueError:
        return False
    host_header = (request.headers.get("host") or "").strip()
    if not host_header or not origin_host:
        return False
    request_host, request_port = _host_and_port(host_header)
    default_port = 443 if parsed.scheme == "https" else 80
    if origin_host != request_host:
        return False
    if (origin_port or default_port) != (request_port or default_port):
        return False
    return _is_ip_literal_or_own_mdns(origin_host)


def _origin_is_foreign(request: Request) -> bool:
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return False
    if origin.lower() == "null":
        # "null" origin (sandboxed iframe / file://) is not a local page.
        return True
    host = (urlparse(origin).hostname or "").lower()
    if host in LOOPBACK_ORIGIN_HOSTS:
        return False
    return not _origin_is_own_lan_page(request, origin)


ACCESS_TOKEN_COOKIE = "fluentflow_access_token"


def _cookie_token_ok(request: Request) -> bool:
    """A page on another device loads screenshots with plain <img> tags, which
    cannot send the token header; the page keeps the same token in a cookie.
    Accepted for reads only, so a cross-site form cannot use it to write (the
    Origin check also still applies)."""
    if request.method not in {"GET", "HEAD"}:
        return False
    token = (os.environ.get("FLUENTFLOW_ACCESS_TOKEN") or "").strip()
    supplied = (request.cookies.get(ACCESS_TOKEN_COOKIE) or "").strip()
    return bool(token and supplied and hmac.compare_digest(supplied, token))


async def local_boundary_middleware(request: Request, call_next):
    peer = (request.client.host if request.client else "") or ""
    if peer not in LOOPBACK_PEERS:
        if not _allow_non_loopback():
            return _forbidden("本地版仅接受本机请求。如需局域网访问，请设置 FLUENTFLOW_ALLOW_NON_LOOPBACK=1。")
        if not _open_without_token(request) and not _cookie_token_ok(request):
            try:
                require_local_agent_access(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": (
                        "从其他设备访问需要访问令牌：请在这台设备的「菜单 → Agent 接入」里填入访问令牌"
                        "（就是启动 FluentFlow 的电脑上设置的 FLUENTFLOW_ACCESS_TOKEN）。"
                        f"（{exc.detail}）"
                    )},
                )
    if _origin_is_foreign(request):
        return _forbidden("请求来源不是本机页面，已拒绝。")
    return await call_next(request)
