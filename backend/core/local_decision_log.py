"""Decision log helpers for the local edition."""

from __future__ import annotations

from typing import Any

from backend.core.decision_log_core import build_decision_log as build_decision_log_core
from backend.core.local_processing_plan import build_processing_plan


def _execution_route_label(scope: str) -> str:
    if scope == "local":
        return "本机处理"
    return "按任务来源决定"


def build_decision_log(
    result: dict[str, Any] | None,
    *,
    job: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_decision_log_core(
        result,
        job=job,
        metadata=metadata,
        build_processing_plan=build_processing_plan,
        execution_route_label=_execution_route_label,
        execution_route_reason="根据素材类型和转写设置决定走本机处理或字幕读取。",
    )
