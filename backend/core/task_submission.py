"""Turning a submission into a queued task: the options it runs with, the
pipeline context, and the entries shared by the routes and the Agent API
(a recording by path, a retry). The routes in ``routers/local_processing``
and ``routers/local_agent`` call these; nothing here reads a request body.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

from backend.core import claude_code_note, local_intake_flow, task_flow, visual_note_channel
from backend.core import speaker_diarization as speaker_diarization_core
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.history_retention import enforce_history_retention
from backend.core.job_store import get_job, list_jobs_for_retention, save_result_changes, upsert_job
from backend.core.local_config import as_recorded, resolve_secret, saved_choices
from backend.core.local_entry_guards import claim_task_id, friendly_error, local_ai_kwargs
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_keyframe_provider import extract_keyframes as extract_local_keyframes
from backend.core.local_limits_config import max_media_duration_seconds, max_upload_mb
from backend.core.local_request_scope import request_is_localhost
from backend.core.local_retention_config import source_retention_days
from backend.core.local_stt_policy import DEFAULT_LOCAL_STT_MODEL, LOCAL_STT_PROVIDER
from backend.core.local_task_detail import build_task_snapshot
from backend.core.media_intake import copy_source_file, file_size_mb, path_size_mb, source_type_for_suffix
from backend.core.media_job import MediaJobContext
from backend.core.media_preflight import MediaPreflightError, preflight_media_file
from backend.core.queue_options import _queue_options_from_mapping
from backend.core.request_scope import local_client_scope
from backend.core.result_retention import finalize_completed_result_storage
from backend.core.runtime_env import truthy
from backend.core.storage_cleanup import remove_tree
from backend.core.storage_paths import find_source_file
from backend.core.task_queue import _start_behind_queue, _with_queue_place
from backend.core.title_display import display_title_for_user


def _local_stt_provider_label(_provider: str) -> str:
    return "faster-whisper"


def _finalize_local_result_storage(task_id: str, result: dict, metadata: dict | None) -> dict:
    return finalize_completed_result_storage(task_id, result, metadata, source_retention_days=source_retention_days(), find_source_file=find_source_file)


_SOURCE_EXPIRY_FIELDS = ("source_file_available", "source_file_storage", "source_retention_status", "source_retention_cleaned_at")


def _record_source_expiry(task_id: str, result: dict, client_id: Optional[str] = None, touch_updated_at: bool = False):
    """Write only the expiry fields onto the task as it is now; the result
    handed in was read when the sweep began (audit, 2026-10-18)."""
    job = get_job(task_id, client_id=client_id)
    if not job:
        return None
    current = dict(job.get("result") or {})
    fields = {key: result.get(key) for key in _SOURCE_EXPIRY_FIELDS if key in result}
    return save_result_changes(task_id, current, {**current, **fields}, client_id=client_id,
                               touch_updated_at=touch_updated_at)


def _enforce_local_history_retention(client_id: str | None) -> dict:
    return enforce_history_retention(client_id, source_days=source_retention_days(), list_jobs=list_jobs_for_retention, update_result=_record_source_expiry, load_job=get_job)


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _effective_duration_limit(requested: object = None) -> float | None:
    configured = _positive_float(max_media_duration_seconds())
    override = _positive_float(requested)
    if configured is not None and override is not None:
        return min(configured, override)
    return configured if configured is not None else override


def _collect_options(**raw_options: Optional[str]) -> dict[str, str]:
    """Normalize form fields into the persisted queue-options vocabulary
    (drop empties, strip values) shared by /process, /queue/process and the
    by-path routes, with the saved choices filled in where the request named
    none (decision 6.31). What is stored is what the task runs with, so a
    retry repeats it and the task record says whether it exported."""
    return saved_choices({
        key: value.strip()
        for key, value in raw_options.items()
        if isinstance(value, str) and value.strip()
    })


def _log_visual_note_unavailable(task_id: str, *, source_type: str, source_filename: str) -> str:
    """The automatic frame note is on but cannot run, so this task gets the text
    note instead. Recorded because it is invisible otherwise: the task looks
    like any other text-note task. Returns the reason, which the note carries."""
    channel = visual_note_channel.resolve_channel(resolve_secret(None, "anthropic_api_key"))
    login_expired = (
        channel.name == visual_note_channel.CHANNEL_SUBSCRIPTION
        and claude_code_note.login_state() is False
    )
    log_event(
        task_id=task_id,
        event_name="visual_note_unavailable",
        source_type=source_type,
        source_filename=source_filename,
        stage="summary",
        success=False,
        error_reason=channel.unavailable_reason,
        metadata=event_metadata(
            channel=channel.name,
            reason="login_expired" if login_expired else "channel_unavailable",
            fallback="text_note",
        ),
    )
    if login_expired:
        return "本机 Claude 的登录已过期。"
    return channel.unavailable_reason or "结合画面的笔记现在写不了。"


async def start_media_job_behind_queue(ctx: MediaJobContext, *, route: str) -> bool:
    """The pipeline and then its note, behind every job already queued."""
    ctx.event_route = route
    return await _start_behind_queue(
        task_id=ctx.task_id_value,
        client_id=ctx.client_id,
        route=route,
        stage="processing",
        chained_worker=lambda previous, done: task_flow._run_serially(previous, done, ctx),
    )


def _local_media_job_context(
    *,
    task_id: str,
    client_id: Optional[str],
    source_type: str,
    source_filename: str,
    raw_title: str,
    display_title: str,
    suffix: str,
    td: str,
    in_path: Path,
    content: bytes,
    source_file_size_mb: float | None,
    duration_preflight_sec: float | None,
    options: dict[str, str],
    deepseek_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    qwen_api_key: Optional[str] = None,
    duration_limit_seconds: float | None = None,
    announce_fallback: bool = True,
) -> MediaJobContext:
    """Build the pipeline context with the local edition's invariants: local
    STT and the local event hub. ``options`` uses the persisted queue-options vocabulary so the
    upload and retry entries cannot drift apart. The saved choices are filled
    in before the options are stored (``_collect_options``), not here, so a
    retry runs with what the task recorded (decision 6.31)."""
    skip_summary = truthy(options.get("skip_summary"))
    visual_note_will_run = local_intake_flow.auto_note_will_run()
    note_after_cut = visual_note_will_run or local_intake_flow.survey_first_enabled()
    note_fallback_reason = None
    if announce_fallback and not skip_summary and local_intake_flow.auto_note_enabled() and not visual_note_will_run:
        note_fallback_reason = _log_visual_note_unavailable(task_id, source_type=source_type, source_filename=source_filename)
    return MediaJobContext(
        task_id_value=task_id,
        client_id=client_id,
        source_type=source_type,
        source_filename=source_filename,
        raw_title_value=raw_title,
        display_title_value=display_title,
        suffix=suffix,
        td=td,
        in_path=in_path,
        content=content,
        source_fingerprint=None,
        source_file_size_mb=source_file_size_mb,
        max_upload_mb=max_upload_mb(),
        duration_preflight_sec=duration_preflight_sec,
        task_started_at=time.perf_counter(),
        loop=asyncio.get_event_loop(),
        model_size=(options.get("stt_model") or "").strip() or DEFAULT_LOCAL_STT_MODEL,
        # Always the most accurate profile. The speed choice was a setting
        # nobody knew how to use; a stored or sent ``stt_speed`` is ignored.
        speed_profile="accurate",
        language="auto",
        stt_provider_value=LOCAL_STT_PROVIDER,
        diarization_requested=truthy(options.get("speaker_diarization")),
        voice_enhance_requested=truthy(options.get("voice_enhance")),
        do_lark=truthy(options.get("export_to_lark")),
        # The pipeline's own note stage is switched off whenever this edition is
        # going to write the note itself from the cut media. That is the "remove
        # the old flow" half of the owner's decision: without this the user gets a
        # note from the text model first and the real one a minute later, which is
        # the two-notes shape the whole change exists to end.
        #
        # `will_run` rather than `enabled`: a machine that cannot reach Claude
        # never writes that note, and switching the text one off for it left a
        # fresh install with no note at all.
        #
        # The survey-first flow cuts after the transcript, so any note the
        # pipeline wrote would describe the uncut recording on its clock and
        # then stand, since the note step skips a task that has one. The note
        # step after the cut writes it instead, with the text model when
        # Claude cannot (``local_intake_flow.write_note``).
        summary_disabled=skip_summary or note_after_cut,
        note_deferred_to_visual_note=note_after_cut and not skip_summary,
        note_fallback_reason=note_fallback_reason,
        generate_visuals=truthy(options.get("generate_visuals")),
        source_last_modified_ms=None,
        export_to_lark=options.get("export_to_lark"),
        lark_export_route=options.get("lark_export_route"),
        lark_via_cli=options.get("lark_via_cli"),
        folder_token=options.get("folder_token"),
        deepseek_api_key=deepseek_api_key,
        openai_api_key=openai_api_key,
        qwen_api_key=qwen_api_key,
        ai_provider=options.get("ai_provider"),
        ai_model=options.get("ai_model"),
        note_mode=options.get("note_mode"),
        skip_summary=options.get("skip_summary"),
        system_prompt=options.get("system_prompt"),
        prompt_preset=options.get("prompt_preset"),
        prompt_preset_label=options.get("prompt_preset_label"),
        title=options.get("title"),
        lark_app_id=None,
        lark_app_secret=None,
        duration_limit_seconds=duration_limit_seconds,
        ai_kwargs_builder=local_ai_kwargs,
        secret_resolver=resolve_secret,
        keyframe_extractor=extract_local_keyframes,
        friendly_error=friendly_error,
        stt_provider_labeler=_local_stt_provider_label,
        job_events=JOB_EVENTS,
        finalize_result_storage=_finalize_local_result_storage,
        auto_lark_exporter=task_flow._auto_export_local_lark,
        enforce_history_retention=_enforce_local_history_retention,
        media_preprocessor=local_intake_flow.preprocess_media,
    )


def _preflight_rejection(
    *,
    task_id: str,
    source_path: Path,
    source_type: str,
    source_filename: str,
    source_file_size_mb: float | None,
    client_id: Optional[str],
    error: MediaPreflightError,
    route: str = "/process",
    **extra_metadata: object,
) -> HTTPException:
    log_event(
        task_id=task_id,
        event_name="media_preflight_rejected",
        source_type=source_type,
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
        stage="import",
        success=False,
        error_reason=str(error),
        metadata=event_metadata(
            route=route,
            media_preflight={"status": "rejected", "code": error.code, **error.metadata},
            **extra_metadata,
        ),
    )
    upsert_job(
        task_id=task_id,
        status="failed",
        client_id=client_id,
        stage="import",
        progress=100,
        source_type=source_type,
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
        error_reason=str(error),
    )
    remove_tree(source_path.parent)
    return HTTPException(status_code=422, detail=str(error))


async def queue_local_media_file(
    source_path: Path,
    *,
    client_id: Optional[str],
    options: dict,
    duration_limit_seconds: float | None,
    route: str,
    origin: dict,
    index: int = 1,
    total: int = 1,
) -> dict:
    """Queue one recording that stays where it is, and answer what happened to it.

    Shared by the two local path entries — the system file dialog and a named
    folder — so a guard added for one cannot be missing from the other. The file is
    not copied into FluentFlow's store: the pipeline reads it in place, which is
    what makes the cut version's home ("beside this file") a real location.
    """
    try:
        preflight = await asyncio.to_thread(preflight_media_file, source_path)
    except MediaPreflightError as exc:
        # Refused before a task row exists, so an unreadable file does not leave a
        # queued task that can never run.
        return {"filename": source_path.name, "status": "rejected", "reason": str(exc)}
    task_id_value = claim_task_id(None, client_id=client_id)
    suffix = source_path.suffix.lower() or ".mp4"
    raw_title_value = (options.get("title") or source_path.stem).strip()
    display_title_value = display_title_for_user(raw_title_value, source_path.name).strip()
    size_mb = file_size_mb(source_path.stat().st_size)
    job_metadata = event_metadata(
        route=route,
        raw_title=raw_title_value,
        display_title=display_title_value,
        queue_options=options,
        queue_position=index,
        queue_total=total,
        # Deliberately not under `video_source.file_path`: retention cleanup reads
        # that key, and this path is the user's own file. Cleanup also refuses
        # anything outside FluentFlow storage now, so this is the second lock
        # rather than the first.
        folder_intake={**origin, "original_path": str(source_path)},
        media_preflight={"status": "passed", **preflight.as_metadata()},
    )
    log_event(
        task_id=task_id_value,
        event_name="source_queued",
        source_type=source_type_for_suffix(suffix),
        source_filename=source_path.name,
        source_file_size_mb=size_mb,
        stage="queued",
        success=True,
        metadata=job_metadata,
    )
    upsert_job(
        task_id=task_id_value,
        status="queued",
        client_id=client_id,
        stage="queued",
        progress=0,
        source_type=source_type_for_suffix(suffix),
        source_filename=source_path.name,
        source_file_size_mb=size_mb,
        metadata=job_metadata,
    )
    ctx = _local_media_job_context(
        task_id=task_id_value,
        client_id=client_id,
        source_type=source_type_for_suffix(suffix),
        source_filename=source_path.name,
        raw_title=raw_title_value,
        display_title=display_title_value,
        suffix=suffix,
        td=tempfile.mkdtemp(),
        in_path=source_path,
        content=b"",
        source_file_size_mb=size_mb,
        duration_preflight_sec=preflight.as_metadata().get("duration_seconds"),
        options=options,
        duration_limit_seconds=duration_limit_seconds,
    )
    await start_media_job_behind_queue(ctx, route=route)
    return _with_queue_place({
        "task_id": task_id_value,
        "filename": source_path.name,
        "status": "queued",
        "queue_position": index,
        "queue_total": total,
    })


def _option_default_on(payload: dict, key: str) -> str:
    """The caller's value for a switch that is on unless they turned it off."""
    if key not in (payload or {}):
        return "true"
    raw = (payload or {}).get(key)
    if raw is None:
        return "true"
    # "false", not "": an empty value reads as "not said", and the saved
    # choice would then switch it back on (decision 6.31).
    return "true" if truthy(str(raw)) else "false"


def _payload_text(payload: dict, key: str) -> Optional[str]:
    """A JSON payload's value spelled the way a form field would carry it.

    The upload routes receive every option as form text ("true", "1800.0");
    a JSON caller may send ``true`` or ``1800``. Spelling both the same way is
    what keeps a task's stored ``queue_options`` identical whichever door it
    came in through, so a retry or a later reader cannot tell them apart.
    """
    value = (payload or {}).get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def local_path_options(
    payload: dict,
    *,
    file_count: int = 1,
    speaker_diarization_default_on: bool = True,
) -> tuple[dict, float | None]:
    """The run options of a by-path submission, in the upload routes' vocabulary.

    Takes every option ``/queue/process`` takes, so a file chosen with the system
    dialog or found in a folder runs with the same settings as the same file
    dragged in: export to Feishu, provider and model, prompt, visuals. A key
    missing here is dropped without an error and the run reports success with
    the feature never having run, so ``tests/test_local_option_vocabulary_parity.py``
    pins the two key sets equal.

    ``title`` applies only to a single file, as on the upload route: a batch
    sharing one typed title would make every task the same name.

    For callers with no page (the Agent API, the MCP tool, a curl) an absent
    ``speaker_diarization`` follows ``speaker_diarization.default_on_for_agents``,
    which is off since 2026-10-15: speaker separation runs only when asked for,
    and the bundled agent skill offers it for multi-person recordings. The
    page's own routes pass ``speaker_diarization_default_on=False``: the page
    always says what the user chose, and an absent switch means off there,
    exactly as on the upload route. An explicit value always wins.
    """
    limit = _effective_duration_limit(payload.get("duration_limit_seconds"))
    if speaker_diarization_default_on:
        asked = (payload or {}).get("speaker_diarization") is not None
        diarization = (
            _option_default_on(payload, "speaker_diarization")
            if asked or speaker_diarization_core.default_on_for_agents()
            else None
        )
    else:
        diarization = _payload_text(payload, "speaker_diarization")
    return _collect_options(
        export_to_lark=_payload_text(payload, "export_to_lark"),
        lark_export_route=_payload_text(payload, "lark_export_route"),
        lark_via_cli=_payload_text(payload, "lark_via_cli"),
        title=_payload_text(payload, "title") if file_count == 1 else None,
        folder_token=_payload_text(payload, "folder_token"),
        ai_provider=_payload_text(payload, "ai_provider"),
        ai_model=_payload_text(payload, "ai_model"),
        note_mode=_payload_text(payload, "note_mode"),
        skip_summary=_payload_text(payload, "skip_summary"),
        generate_visuals=_payload_text(payload, "generate_visuals"),
        stt_model=_payload_text(payload, "stt_model"),
        stt_speed=_payload_text(payload, "stt_speed"),
        speaker_diarization=diarization,
        voice_enhance=_payload_text(payload, "voice_enhance"),
        system_prompt=_payload_text(payload, "system_prompt"),
        prompt_preset=_payload_text(payload, "prompt_preset"),
        prompt_preset_label=_payload_text(payload, "prompt_preset_label"),
        duration_limit_seconds=(str(limit) if limit is not None else None),
    ), limit


def _in_place_origin(job: dict) -> Optional[dict]:
    """Where this task's recording lives, when the task never copied it in.

    A task from a folder or the system file dialog is read where it sits, so
    FluentFlow's own store has nothing for it and the retry that looks there
    answers "source file not found" for a file that is sitting on the disk.
    The path was recorded when the task was queued; this reads it back.
    """
    metadata = job.get("metadata")
    origin = metadata.get("folder_intake") if isinstance(metadata, dict) else None
    if not isinstance(origin, dict) or not origin.get("original_path"):
        return None
    return origin


def _stored_queue_options(job: dict) -> dict:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    stored = metadata.get("queue_options")
    return as_recorded(_queue_options_from_mapping(stored if isinstance(stored, dict) else metadata))


async def _retry_in_place(
    *,
    local_caller: bool,
    task_id: str,
    client_id: Optional[str],
    job: dict,
    origin: dict,
    overrides: Optional[dict] = None,
) -> dict:
    """Re-run a task from the file it was read from, where that file still is.

    Goes back through ``queue_local_media_file`` rather than repeating its steps:
    the same preflight, the same serial chain, the same note afterwards. A retry
    that reimplemented them would be a second entry for anyone adding a guard to
    remember, and the last one to be remembered.
    """
    if not local_caller:
        raise HTTPException(status_code=403, detail="只有本机能按路径处理文件。")
    original = Path(str(origin.get("original_path")))
    if not original.is_file():
        # Named, because the recording is the user's own file in their own folder
        # and only they can say where it went.
        raise HTTPException(
            status_code=404,
            detail=f"原文件已不在这个位置，无法重新处理：{original}",
        )
    options = {**_stored_queue_options(job), **(overrides or {})}
    duration_limit = _effective_duration_limit(options.get("duration_limit_seconds"))
    queued = await queue_local_media_file(
        original,
        client_id=client_id,
        options=options,
        duration_limit_seconds=duration_limit,
        route="/jobs/{task_id}/retry",
        origin={
            **{key: value for key, value in origin.items() if key != "original_path"},
            "retry_source_task_id": task_id,
        },
    )
    if queued.get("status") == "rejected":
        raise HTTPException(status_code=400, detail=str(queued.get("reason") or "无法处理这个文件。"))
    retry_task_id = str(queued["task_id"])
    started_job = get_job(retry_task_id, client_id=client_id) or {
        "task_id": retry_task_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "source_filename": queued.get("filename"),
    }
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": retry_task_id,
        "job": {**started_job, "task_snapshot": build_task_snapshot(started_job)},
    }


async def retry_for_request(request: Request, task_id: str, overrides: Optional[dict] = None) -> dict:
    """The retry route's work, with optional queue-option overrides for the Agent API."""
    return await retry_task(
        task_id,
        client_id=local_client_scope(request),
        local_caller=request_is_localhost(request),
        overrides=overrides,
    )


async def _retry_link_task(
    *,
    task_id: str,
    job: dict,
    client_id: Optional[str],
    carry: Optional[dict],
    overrides: Optional[dict] = None,
) -> Optional[dict]:
    """Re-submit a link task that never got its video: fetch the link again.

    A link that failed while downloading has no recording to re-run, and the
    retry used to answer that the recording "was cleaned up" although there
    never was one. The full link is kept as ``video_source_url``; older tasks
    only have the share-text preview, which is searched for a link instead.
    Returns None when the task is not a link task or no link can be found.
    """
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    if job.get("source_type") != "video_link" and not metadata.get("video_source_url"):
        return None
    from backend.core.video_source import extract_first_url
    from backend.core.link_submission import submit_video_source_job

    link = str(metadata.get("video_source_url") or "").strip() or extract_first_url(
        str(metadata.get("video_source_input_preview") or "")
    )
    if not link:
        return None
    stored = metadata.get("queue_options")
    raw_options: dict = as_recorded(dict(stored) if isinstance(stored, dict) else {})
    # How the link is fetched follows the user's settings now, not the ones in
    # force when it was first submitted: someone who changed the browser whose
    # login is read must not have a retry (theirs, the MCP tool's, or startup
    # recovery's) quietly use the old choice. Leaving it out lets
    # submit_video_source_job read the current `video_cookies_browser`.
    raw_options.pop("cookies_from_browser", None)
    raw_options.update(overrides or {})
    new_job = await submit_video_source_job(
        input_text=link,
        title=str(metadata.get("video_source_title") or "").strip(),
        raw_options=raw_options,
        client_id=client_id,
        extra_metadata={"retry_source_task_id": task_id, **(carry or {})},
        # Retrying this task is the confirmation: a completed copy of the same
        # link elsewhere does not block it. One already queued or running is
        # still answered instead of being fetched a second time.
        allow_duplicate=True,
    )
    retry_task_id = new_job["task_id"]
    started_job = get_job(retry_task_id, client_id=client_id) or new_job
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": retry_task_id,
        "job": {**started_job, "task_snapshot": build_task_snapshot(started_job)},
    }


async def retry_task(
    task_id: str,
    *,
    client_id: Optional[str],
    local_caller: bool,
    carry: Optional[dict] = None,
    overrides: Optional[dict] = None,
) -> dict:
    """Queue a fresh task from this one's recording; the old row stays as it was.

    The route and startup recovery both come here, so a restart-interrupted task
    re-runs through exactly the path the user's own retry button takes.
    ``local_caller`` says the request came from this machine, which is what
    allows reading a file by its path. ``carry`` is written onto the new task
    when it is created, before anything can run it. ``overrides`` replaces
    stored queue options for the new task (an agent re-running a recording with
    speaker separation turned on).
    """
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cancel the active job before retrying it")
    in_place = _in_place_origin(job)
    if in_place is not None:
        return await _retry_in_place(
            local_caller=local_caller,
            task_id=task_id,
            client_id=client_id,
            job=job,
            origin={**in_place, **(carry or {})},
            overrides=overrides,
        )
    source = find_source_file(task_id)
    if not source:
        link_retry = await _retry_link_task(
            task_id=task_id, job=job, client_id=client_id, carry=carry, overrides=overrides
        )
        if link_retry is not None:
            return link_retry
        raise HTTPException(
            status_code=404,
            detail="这个任务的原始录音已按保留策略清理，无法直接重新运行。请重新添加文件后再处理。",
        )

    filename = Path(job.get("source_filename") or source.name).name
    suffix = source.suffix or Path(filename).suffix.lower() or ".mp4"
    retry_task_id = claim_task_id(None, client_id=client_id)
    try:
        target_path = await asyncio.to_thread(copy_source_file, retry_task_id, suffix, source)
    except Exception as exc:
        upsert_job(
            task_id=retry_task_id,
            status="failed",
            client_id=client_id,
            stage="import",
            progress=100,
            source_type=source_type_for_suffix(suffix),
            source_filename=filename,
            error_reason=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Could not prepare the stored source for retry.",
        ) from exc
    source_file_size_mb = path_size_mb(target_path)
    source_type = source_type_for_suffix(suffix)
    try:
        media_preflight = await asyncio.to_thread(preflight_media_file, target_path)
    except MediaPreflightError as exc:
        raise _preflight_rejection(
            task_id=retry_task_id,
            source_path=target_path,
            source_type=source_type,
            source_filename=filename,
            source_file_size_mb=source_file_size_mb,
            client_id=client_id,
            error=exc,
            route="/jobs/{task_id}/retry",
            retry_source_task_id=task_id,
        ) from exc

    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    stored = metadata.get("queue_options")
    options = as_recorded(_queue_options_from_mapping(stored if isinstance(stored, dict) else metadata))
    options.update(overrides or {})
    raw_title_value = str(
        options.get("title") or metadata.get("raw_title") or Path(filename).stem
    ).strip()
    options.setdefault("title", raw_title_value)
    display_title_value = str(
        metadata.get("display_title") or display_title_for_user(raw_title_value, filename)
    ).strip()
    effective_duration_limit = _effective_duration_limit(
        options.get("duration_limit_seconds")
    )
    if effective_duration_limit is not None:
        options["duration_limit_seconds"] = str(effective_duration_limit)

    job_metadata = event_metadata(
        route="/jobs/{task_id}/retry",
        retry_source_task_id=task_id,
        **(carry or {}),
        raw_title=raw_title_value,
        display_title=display_title_value,
        queue_options=options,
        media_preflight={"status": "passed", **media_preflight.as_metadata()},
    )
    log_event(
        task_id=retry_task_id,
        event_name="task_retried",
        source_type=source_type,
        source_filename=filename,
        source_file_size_mb=source_file_size_mb,
        stage="import",
        success=True,
        metadata=job_metadata,
    )
    upsert_job(
        task_id=retry_task_id,
        status="queued",
        client_id=client_id,
        stage="queued",
        progress=0,
        source_type=source_type,
        source_filename=filename,
        source_file_size_mb=source_file_size_mb,
        metadata=job_metadata,
    )

    ctx = _local_media_job_context(
        task_id=retry_task_id,
        client_id=client_id,
        source_type=source_type,
        source_filename=filename,
        raw_title=raw_title_value,
        display_title=display_title_value,
        suffix=suffix,
        td=tempfile.mkdtemp(),
        in_path=target_path,
        content=b"",
        source_file_size_mb=source_file_size_mb,
        duration_preflight_sec=media_preflight.duration_seconds,
        options=options,
        duration_limit_seconds=effective_duration_limit,
    )
    # Behind the same chain as every other upload, and with the note after it:
    # a retry that started at once ran beside whatever was already transcribing,
    # which is the memory collapse QUEUE_CONCURRENCY exists to prevent, and it
    # ended at the transcript without ever writing the note.
    await start_media_job_behind_queue(ctx, route="/jobs/{task_id}/retry")

    started_job = get_job(retry_task_id, client_id=client_id) or {
        "task_id": retry_task_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "source_type": source_type,
        "source_filename": filename,
        "source_file_size_mb": source_file_size_mb,
        "metadata": job_metadata,
    }
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": retry_task_id,
        "job": {**started_job, "task_snapshot": build_task_snapshot(started_job)},
    }


def context_for_task(job: dict, *, need_source: bool = True) -> MediaJobContext:
    """The pipeline context of a task already on record, to carry it on in
    place (``task_actions.resume_task``): its own options, title and
    recording. ``need_source`` is false when only steps after the transcript
    run, which read the task's own files rather than the recording.

    Raises ``FileNotFoundError`` with the user's words when the recording it
    needs is gone.
    """
    task_id = str(job.get("task_id") or "")
    client_id = job.get("client_id")
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    options = _stored_queue_options(job)
    in_place = _in_place_origin(job)
    source: Path | None = Path(str(in_place["original_path"])) if in_place else find_source_file(task_id)
    if need_source and (source is None or not source.is_file()):
        where = f"：{source}" if source is not None else ""
        raise FileNotFoundError(f"这个任务的录像已不在原来的位置{where}，没法接着做；请重新添加文件。")
    filename = Path(str(job.get("source_filename") or (source.name if source else "media.mp4"))).name
    suffix = (source.suffix if source else "") or Path(filename).suffix.lower() or ".mp4"
    raw_title = str(metadata.get("raw_title") or options.get("title") or Path(filename).stem).strip()
    display_title = str(metadata.get("display_title") or display_title_for_user(raw_title, filename)).strip()
    preflight = metadata.get("media_preflight") if isinstance(metadata.get("media_preflight"), dict) else {}
    ctx = _local_media_job_context(
        task_id=task_id,
        client_id=client_id,
        source_type=str(job.get("source_type") or source_type_for_suffix(suffix)),
        source_filename=filename,
        raw_title=raw_title,
        display_title=display_title,
        suffix=suffix,
        td=tempfile.mkdtemp(),
        in_path=source,
        content=b"",
        source_file_size_mb=job.get("source_file_size_mb"),
        duration_preflight_sec=preflight.get("duration_seconds"),
        options=options,
        duration_limit_seconds=_effective_duration_limit(options.get("duration_limit_seconds")),
        # Said when the task was submitted; carrying it on is not a new submission.
        announce_fallback=False,
    )
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if "note_deferred_to_visual_note" in result:
        # Decided when the task ran, not by what is reachable now.
        ctx.note_deferred_to_visual_note = bool(result["note_deferred_to_visual_note"])
    ctx.event_route = str(metadata.get("route") or "/jobs/{task_id}/resume")
    return ctx
