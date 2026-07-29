"""Local-edition task-detail policy and entry points."""

from __future__ import annotations

from typing import Any

from backend.core.local_decision_log import build_decision_log as build_local_decision_log
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.note_diagnosis import build_note_generation_diagnosis
from backend.core.storage_paths import find_source_file
from backend.core.task_detail_core import (
    build_task_detail as build_task_detail_core,
    build_task_snapshot as build_task_snapshot_core,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _queue_options(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("queue_options")
    return value if isinstance(value, dict) else {}


def _source_type(job: dict[str, Any], result: dict[str, Any]) -> str:
    value = _text(job.get("source_type") or result.get("source"))
    return value or "unknown"


class LocalTaskDetailPolicy:
    @staticmethod
    def note_generation_diagnosis(
        job: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return build_note_generation_diagnosis(
            job,
            result,
            diagnose_error=diagnose_error,
        )

    @staticmethod
    def diagnose_error(error: Any) -> dict[str, Any]:
        return diagnose_error(error)

    @staticmethod
    def build_decision_log(
        result: dict[str, Any],
        *,
        job: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return build_local_decision_log(result, job=job, metadata=metadata)

    @staticmethod
    def stt_route(result: dict[str, Any], metadata: dict[str, Any]) -> str:
        provider = _text(
            result.get("stt_provider") or _queue_options(metadata).get("stt_provider")
        )
        model = _text(result.get("stt_model") or _queue_options(metadata).get("stt_model"))
        if provider == "local":
            return "本地 · faster-whisper" + (f" / {model}" if model else "")
        if provider:
            return f"转写引擎：{provider}" + (f" / {model}" if model else "")
        return ""

    @staticmethod
    def route_snapshot(
        job: dict[str, Any],
        result: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        queue_options = _queue_options(metadata)
        processing_plan = (
            result.get("processing_plan")
            if isinstance(result.get("processing_plan"), dict)
            else {}
        )
        execution = (
            processing_plan.get("execution")
            if isinstance(processing_plan.get("execution"), dict)
            else {}
        )
        provider = _text(
            result.get("stt_provider")
            or metadata.get("stt_provider")
            or queue_options.get("stt_provider")
        )
        model = _text(
            result.get("stt_model")
            or metadata.get("stt_model")
            or queue_options.get("stt_model")
        )
        source_type = _source_type(job, result)
        transcription = ""
        if source_type == "transcript_file":
            transcription = "transcript_file"
        elif provider == "local":
            transcription = "local"
        elif provider:
            transcription = provider
        else:
            transcription = _text(execution.get("scope")) or "local"
        route = {
            "transcription": transcription,
            "stt_provider": provider or None,
            "stt_model": model or None,
            "execution_scope": _text(execution.get("scope")) or None,
            "transcription_tool": _text(execution.get("transcription_tool")) or None,
        }
        return {key: value for key, value in route.items() if value not in (None, "")}

    @staticmethod
    def next_action_for_failure(
        job: dict[str, Any],
        failed_step: dict[str, Any] | None,
    ) -> str:
        error_text = _text(
            job.get("error_reason") or (failed_step or {}).get("error_reason")
        ).lower()
        if any(token in error_text for token in ("lark", "feishu", "飞书")):
            return "检查本机飞书应用或 lark-cli 连接后重试导出。"
        if any(token in error_text for token in ("not found", "404", "归属")):
            return "刷新本机任务列表；如果仍找不到，请重新提交。"
        if job.get("source_type") == "video_link":
            return "重新粘贴链接再试；如果仍失败，改用本地视频上传。"
        return "在本机重新提交任务；如果连续失败，请查看本机日志。"

    @staticmethod
    def is_source_retryable(job: dict[str, Any]) -> bool:
        # Local retry re-runs from the stored source file via the local
        # /jobs/{id}/retry route; offer it only when that file still exists.
        if _text(job.get("status")) != "failed":
            return False
        return find_source_file(_text(job.get("task_id"))) is not None

    @staticmethod
    def additional_stage_step(_stage: str) -> str | None:
        return None

    @staticmethod
    def additional_recorded_step(_step_type: str) -> str | None:
        return None


POLICY = LocalTaskDetailPolicy()


def build_task_snapshot(
    job: dict[str, Any],
    *,
    job_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return build_task_snapshot_core(job, policy=POLICY, job_steps=job_steps)


def build_task_detail(
    job: dict[str, Any],
    *,
    job_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return build_task_detail_core(job, policy=POLICY, job_steps=job_steps)
