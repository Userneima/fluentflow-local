"""Edition-neutral retention time helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def parse_job_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def source_retention_expiry(days: int) -> str:
    return (
        datetime.now(timezone.utc).astimezone() + timedelta(days=days)
    ).isoformat(timespec="seconds")
