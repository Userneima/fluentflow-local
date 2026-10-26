"""Local-edition Agent task-package policy.

Same package shape as hosted (``agent_package_core``), with local semantics:
the local processing plan and decision log, local error diagnostics, and no
quota ``usage`` or desktop-sync ``execution`` sections. The retry
next-action follows the local rule — offered when a failed task still has
its stored source file on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core.agent_package_core import (
    AGENT_TASK_PACKAGE_VERSION,
    AgentPackagePolicy,
)
from backend.core.agent_package_core import (
    build_agent_task_package as _build_agent_task_package_core,
)
from backend.core.agent_package_core import (
    note_generation_diagnosis as _note_generation_diagnosis_core,
)
from backend.core.local_decision_log import build_decision_log
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.local_processing_plan import build_processing_plan, ensure_processing_plan
from backend.core.storage_paths import find_source_file
from backend.core import note_section_times, task_activity
from backend.core.result_schema import canonical_display_segments
from backend.core.tool_trace_core import build_tool_trace


def _no_usage(job: dict[str, Any], result: dict[str, Any], metadata: dict[str, Any]) -> None:
    return None


def _no_execution(metadata: dict[str, Any]) -> None:
    return None


def _local_extra_next_actions(job: dict[str, Any], diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    task_id = str(job.get("task_id") or "")
    if job.get("status") == "failed" and task_id and find_source_file(task_id):
        return [{
            "action": "retry_task",
            "method": "POST",
            "path": f"/agent/v1/tasks/{task_id}/retry",
            "reason": "本机仍保留原始文件，可直接重新处理，不用重新选文件。",
        }]
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    note_settled = str(result.get("summary_status") or "") not in {"pending", "running"}
    if job.get("status") == "completed" and task_id and note_settled and not str(result.get("summary_markdown") or "").strip():
        # No key configured, or the note was skipped or failed: the calling agent
        # can write it itself, which is the path that needs nothing set up.
        return [{
            "action": "get_agent_note_material",
            "method": "POST",
            "path": f"/agent/v1/tasks/{task_id}/agent-note/material",
            "reason": "这个任务还没有笔记。可以由你来写：取材料（转录、截图、写法要求），写好后交回 save_agent_note。",
        }]
    if (
        task_id and note_settled and not task_activity.activity(job).busy
        and canonical_display_segments(result) and note_section_times.missing_sections(result)[0]
    ):
        return [{
            "action": "get_agent_alignment_material", "method": "POST",
            "path": f"/agent/v1/tasks/{task_id}/agent-alignment/material",
            "reason": "笔记有章节尚未对上播放时间。由你取材料、按意思对齐并用 save_agent_alignment 交回，不需要 Claude。",
        }]
    return []


LOCAL_AGENT_PACKAGE_POLICY = AgentPackagePolicy(
    ensure_processing_plan=ensure_processing_plan,
    build_processing_plan=build_processing_plan,
    build_decision_log=build_decision_log,
    build_tool_trace=build_tool_trace,
    diagnose_error=diagnose_error,
    usage=_no_usage,
    execution=_no_execution,
    extra_next_actions=_local_extra_next_actions,
)


def note_generation_diagnosis(job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return _note_generation_diagnosis_core(job, result, diagnose_error=diagnose_error)


def build_agent_task_package(job: dict[str, Any], *, artifact_root: Path | None = None) -> dict[str, Any]:
    return _build_agent_task_package_core(
        job,
        policy=LOCAL_AGENT_PACKAGE_POLICY,
        artifact_root=artifact_root,
    )


__all__ = [
    "AGENT_TASK_PACKAGE_VERSION",
    "LOCAL_AGENT_PACKAGE_POLICY",
    "build_agent_task_package",
    "note_generation_diagnosis",
]
