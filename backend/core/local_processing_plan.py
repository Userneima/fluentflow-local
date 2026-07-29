"""Processing Plan helpers for the local edition."""

from __future__ import annotations

from typing import Any

from backend.core.processing_plan_core import (
    build_processing_plan as build_processing_plan_core,
    ensure_processing_plan as ensure_processing_plan_core,
    note_strategy_from_result,
)


def _resolve_execution(
    result: dict[str, Any],
    job: dict[str, Any] | None,
    _metadata: dict[str, Any],
) -> tuple[str, str]:
    source_type = str(
        result.get("source") or (job or {}).get("source_type") or "unknown"
    ).strip()
    if source_type == "transcript_file":
        return "local", "transcript_parser"
    return "local", "local_whisper"


def build_processing_plan(
    result: dict[str, Any] | None,
    *,
    job: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_processing_plan_core(
        result,
        job=job,
        metadata=metadata,
        resolve_execution=_resolve_execution,
    )


def ensure_processing_plan(
    result: dict[str, Any] | None,
    *,
    job: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ensure_processing_plan_core(
        result,
        job=job,
        metadata=metadata,
        resolve_execution=_resolve_execution,
    )
