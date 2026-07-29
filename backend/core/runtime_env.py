"""Edition-neutral runtime environment helpers."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Any


def _persistent_internal_queue_token() -> str:
    configured = (os.environ.get("FLUENTFLOW_INTERNAL_QUEUE_TOKEN") or "").strip()
    if configured:
        return configured
    token_path = Path(
        (os.environ.get("FLUENTFLOW_INTERNAL_QUEUE_TOKEN_PATH") or "").strip()
        or (Path(__file__).resolve().parents[1] / "data" / "internal_queue_token")
    ).expanduser()
    try:
        existing = token_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    except OSError:
        return secrets.token_hex(32)
    token = secrets.token_hex(32)
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token, encoding="utf-8")
    except OSError:
        pass
    return token


INTERNAL_QUEUE_TOKEN = _persistent_internal_queue_token()


def env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def request_is_internal_queue(request: Any) -> bool:
    supplied = request.headers.get("x-fluentflow-internal-queue-token") or ""
    return bool(supplied and hmac.compare_digest(supplied, INTERNAL_QUEUE_TOKEN))
