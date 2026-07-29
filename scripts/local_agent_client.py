"""Minimal local HTTP client shared by the MCP server and its checks.

This intentionally targets the local Agent API only. It carries no cloud STT
provider negotiation or hosted job compatibility behavior.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_CLIENT_ID = "local-client"


class FluentFlowApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


def normalize_api_base(value: str | None) -> str:
    text = (value or os.environ.get("FLUENTFLOW_API_BASE") or DEFAULT_API_BASE).strip()
    return text.rstrip("/") or DEFAULT_API_BASE


def _parse_json_bytes(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FluentFlowApiError(f"Backend returned non-JSON response: {raw[:200]!r}") from exc
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _error_message(payload: Any, fallback: str) -> str:
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or fallback)
    return fallback


def api_request(
    method: str,
    api_base: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    client_id: str = DEFAULT_CLIENT_ID,
    access_token: str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "X-FluentFlow-Client-Id": client_id}
    token = (access_token or os.environ.get("FLUENTFLOW_ACCESS_TOKEN") or "").strip()
    if token:
        headers["X-FluentFlow-Access-Token"] = token
    if body is not None:
        headers["Content-Type"] = "application/json"
    url = f"{normalize_api_base(api_base)}{path}"
    request = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _parse_json_bytes(response.read())
    except urllib.error.HTTPError as exc:
        parsed = _parse_json_bytes(exc.read())
        raise FluentFlowApiError(_error_message(parsed, f"HTTP {exc.code}"), status=exc.code, payload=parsed) from exc
    except urllib.error.URLError as exc:
        raise FluentFlowApiError(f"Cannot reach FluentFlow backend at {url}: {exc.reason}") from exc
