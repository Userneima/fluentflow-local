"""Retention settings owned by local processing."""

from __future__ import annotations

import os

# Notes are the product. A local-first workspace must not delete the user's own
# writing on a timer, so history retention is OFF by default: 0 means
# `enforce_history_retention` computes no cutoff and prunes nothing.
#
# This default was 30, which did NOT mean "drop 30-day-old artifacts" — the
# cutoff deletes the whole job row, and the note body lives in that row. Worse,
# retention runs when a task COMPLETES, so the deletion surfaced as "I processed
# one new video and months of notes disappeared".
#
# Reclaiming disk is `FLUENTFLOW_SOURCE_RETENTION_DAYS`' job instead: it expires
# the multi-GB source media and leaves the transcript and note in place.
DEFAULT_ARTIFACT_RETENTION_DAYS = 0
DEFAULT_SOURCE_RETENTION_DAYS = 7


def _retention_days(env_name: str, default: int) -> int:
    try:
        return max(int(os.environ.get(env_name, str(default))), 0)
    except ValueError:
        return default


def artifact_retention_days() -> int:
    """Days before a completed task is deleted outright; 0 keeps every record."""
    return _retention_days("FLUENTFLOW_ARTIFACT_RETENTION_DAYS", DEFAULT_ARTIFACT_RETENTION_DAYS)


def source_retention_days() -> int:
    """Days before a task's stored source media is removed; the note survives."""
    return _retention_days("FLUENTFLOW_SOURCE_RETENTION_DAYS", DEFAULT_SOURCE_RETENTION_DAYS)
