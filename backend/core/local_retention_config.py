"""Retention settings owned by local processing."""

from __future__ import annotations

import os


def source_retention_days() -> int:
    """Days FluentFlow keeps its own copy of a recording; 0, the default, is forever.

    The owner's rule since 2026-10-13: the original is never deleted on its own.
    The de-breathed cut is not a perfect stand-in for it, and getting a deleted
    original back (finding the file again, downloading the link again) is the
    expensive part. A positive value is an explicit opt-in to cleanup.
    """
    try:
        return max(int(os.environ.get("FLUENTFLOW_SOURCE_RETENTION_DAYS", "0")), 0)
    except ValueError:
        return 0
