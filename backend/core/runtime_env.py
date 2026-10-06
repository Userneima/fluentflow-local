"""Edition-neutral runtime environment helpers."""

from __future__ import annotations

import os
from typing import Any


def truthy(value: Any) -> bool:
    """Whether a form field, option, or environment value says "on"."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def env_truthy(name: str) -> bool:
    return truthy(os.environ.get(name))
