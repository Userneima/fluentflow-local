"""Retention settings owned by local processing."""

from __future__ import annotations

import os


def artifact_retention_days() -> int:
    try:
        return max(int(os.environ.get("FLUENTFLOW_ARTIFACT_RETENTION_DAYS", "30")), 0)
    except ValueError:
        return 30


def source_retention_days() -> int:
    try:
        return max(int(os.environ.get("FLUENTFLOW_SOURCE_RETENTION_DAYS", "7")), 0)
    except ValueError:
        return 7
