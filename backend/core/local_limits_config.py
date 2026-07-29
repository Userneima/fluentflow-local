"""Resource guardrails for local processing."""

from __future__ import annotations

import os


def max_upload_mb() -> float:
    try:
        return max(float(os.environ.get("FLUENTFLOW_MAX_UPLOAD_MB", "2048")), 1.0)
    except ValueError:
        return 2048.0


def max_transcript_upload_mb() -> float:
    """Bound text-like uploads that must be decoded and parsed in memory."""
    try:
        return max(float(os.environ.get("FLUENTFLOW_MAX_TRANSCRIPT_UPLOAD_MB", "64")), 1.0)
    except ValueError:
        return 64.0


def max_queue_files() -> int:
    try:
        return max(int(os.environ.get("FLUENTFLOW_MAX_QUEUE_FILES", "5")), 1)
    except ValueError:
        return 5


def max_media_duration_seconds() -> float:
    try:
        return max(
            float(os.environ.get("FLUENTFLOW_MAX_MEDIA_DURATION_SECONDS", "14400")),
            0.0,
        )
    except ValueError:
        return 14400.0
