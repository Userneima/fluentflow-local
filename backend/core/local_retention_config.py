"""Retention settings owned by local processing."""

from __future__ import annotations

import os


def source_retention_days() -> int:
    try:
        return max(int(os.environ.get("FLUENTFLOW_SOURCE_RETENTION_DAYS", "7")), 0)
    except ValueError:
        return 7
