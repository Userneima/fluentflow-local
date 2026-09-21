"""Run breath-gap removal against a task that already exists.

Not a new job type. The task, its source media, its artifact directory, its
ownership and its retention window all exist already; this adds two outputs to a
result the user has already got. The shape follows the transcript/summary edit
routes: locate the job, mutate its result, persist through ``update_job_result``.

The cut list is written first and kept whether or not a render follows. That
ordering is the product decision, not an implementation convenience: the list is
the deliverable, and it can be re-rendered with different padding or exported to
an editor, while a finished file cannot be turned back into the decisions behind
it. A caller that only wants the numbers can ask for the plan and skip the
render entirely.

Rendering re-encodes, so it is minutes of CPU on a machine that also serves
requests. Only one render runs at a time per process, and a task already
rendering is refused rather than queued — the answer "one is already running"
is more useful than a silent backlog. Progress lives in the result, so an
ordinary job poll shows it.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core import cut_timeline, silence_cuts
from backend.core.job_store import get_job, list_jobs_by_statuses, update_job_result
from backend.core.result_artifacts import (
    DEBREATH_ARTIFACT_DIRNAME,
    DEBREATH_ARTIFACT_SUFFIXES,
    DEBREATH_CUT_LIST_KIND,
    DEBREATH_MEDIA_KIND,
    CUT_FILE_NAME_MARKER,
    DEBREATH_TRANSCRIPT_KIND,
    TRANSCRIPT_MEDIA_CUT,
    artifact_target_path,
    describe_existing_artifact,
    write_text_artifact,
)
from backend.core.silence_cuts import CutPlan, SilenceCutError
from backend.core.storage_paths import find_source_file, in_place_source_path
from backend.core.subtitle_format import _format_srt

logger = logging.getLogger(__name__)

CUT_LIST_KIND = DEBREATH_CUT_LIST_KIND
MEDIA_KIND = DEBREATH_MEDIA_KIND
TRANSCRIPT_KIND = DEBREATH_TRANSCRIPT_KIND
ARTIFACT_SUFFIXES = DEBREATH_ARTIFACT_SUFFIXES
ARTIFACT_DIRNAME = DEBREATH_ARTIFACT_DIRNAME

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Containers ffmpeg can cut and concatenate here. A source outside this set is
# refused with its extension named, rather than failing several minutes into an
# encode.
SUPPORTED_SUFFIXES = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi",
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus",
}

_render_lock = threading.Lock()
_running: set[str] = set()


class DebreathError(RuntimeError):
    """A reason worth showing the user, in their language."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def debreath_state(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    state = result.get("debreath")
    return dict(state) if isinstance(state, dict) else {}


def is_running(result: Any) -> bool:
    return debreath_state(result).get("status") == STATUS_RUNNING


def running_task_ids() -> set[str]:
    with _render_lock:
        return set(_running)


def claim(task_id: str, *, allow_concurrent: bool = False) -> None:
    """Reserve the single render slot, or refuse in the caller's words.

    Public so an HTTP route can claim while the caller is still waiting on the
    response. A background task claiming for itself answers "accepted" first and
    refuses afterwards, where nobody sees it.
    """
    with _render_lock:
        if task_id in _running:
            raise DebreathError("这个任务的去气口正在进行中，等它结束再提交")
        if _running and not allow_concurrent:
            raise DebreathError("已有一个去气口在渲染，等它结束再提交（渲染很吃 CPU，一次只跑一个）")
        _running.add(task_id)


def release(task_id: str) -> None:
    """Give the render slot back. Idempotent, so a belt-and-braces caller is free
    to call it after ``run_debreath`` has already released."""
    with _render_lock:
        _running.discard(task_id)


# Everything a de-breath produces goes in one directory inside the task's
# artifact folder, and the recording the user uploaded is not in it: that file
# never moves out of the task's source storage and is never rewritten. Keeping
# the two apart by directory rather than by filename is what makes "which of
# these is the original" answerable by looking.
def _cut_list_filename(source: Path) -> str:
    return f"{ARTIFACT_DIRNAME}/{source.stem}_cut_list.json"


def _media_filename(source: Path) -> str:
    return f"{ARTIFACT_DIRNAME}/{source.stem}_debreath{source.suffix.lower()}"


def _transcript_filename(source: Path) -> str:
    return f"{ARTIFACT_DIRNAME}/{source.stem}_debreath.srt"


def _plan_payload(plan: CutPlan) -> dict[str, Any]:
    """The summary that belongs in the job result — counts, not 2000 ranges.

    The ranges themselves live in the cut-list artifact. Putting them in the
    result too would write megabytes into the job row that every list query then
    reads.
    """
    payload = plan.as_dict()
    payload.pop("cuts", None)
    payload.pop("keeps", None)
    return payload


def _store(
    task_id: str,
    *,
    client_id: str | None,
    state: dict[str, Any],
    artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge one transition into the job result and persist it."""
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise DebreathError("任务不存在")
    result = dict(job.get("result") or {})
    result["debreath"] = {**debreath_state(result), **state}
    if artifacts:
        result["artifacts"] = {**dict(result.get("artifacts") or {}), **artifacts}
    updated = update_job_result(task_id, result, client_id=client_id)
    if not updated:
        raise DebreathError("任务不存在")
    return updated


def _remapped_subtitles(
    task_id: str,
    source: Path,
    plan: CutPlan,
    *,
    client_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Write the transcript onto the cut version's clock, as a subtitle file.

    The cut file is shorter than the recording, so every timestamp the task's
    transcript carries is wrong for it. Producing this here — from the cut list,
    beside the media it belongs to — means the shortened version has subtitles of
    its own instead of borrowing the original's, which would put every caption
    progressively later than the words. It is also the thing that makes the
    remapping checkable: it can be opened against the rendered file.

    Returns the artifact record and what the remap cost, or ``(None, None)`` when
    the task has no timestamped transcript to remap. A task without one is
    normal, and it is not a reason to fail a cut that succeeded.
    """
    job = get_job(task_id, client_id=client_id)
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else {}
    if str(result.get("transcript_media") or "") == TRANSCRIPT_MEDIA_CUT:
        # This transcript was made from a cut file, so it is not on the original's
        # clock and cannot be moved onto this cut's. Remapping it anyway would
        # shift every line by an interval that was already taken out once.
        return None, {
            "source": cut_timeline.TIMELINE_SOURCE_NATIVE,
            "skipped_reason": "这个任务的转录本来就是从剪后文件转出来的，时间点不需要再换算。",
        }
    segments = cut_timeline.timeline_segments(result)
    if not segments:
        return None, None
    report = cut_timeline.remap_segments(segments, cut_timeline.kept_ranges(plan.as_dict()))
    if not report.segments:
        return None, report.as_dict()
    artifact = write_text_artifact(
        task_id, TRANSCRIPT_KIND, _transcript_filename(source), _format_srt(report.segments)
    )
    return artifact, report.as_dict()


# Below both of these, rendering is not worth doing: see `_worth_rendering`.
MIN_WORTHWHILE_REMOVED_SECONDS = 5.0
MIN_WORTHWHILE_REMOVED_PERCENT = 0.5

def _worth_rendering(plan: silence_cuts.CutPlan) -> bool:
    """Whether the saving justifies re-encoding the whole file.

    Both numbers have to be tiny, because either one alone gives a wrong answer: a
    ratio alone would throw away 54 seconds of real silence in a three hour
    recording, and a seconds floor alone would re-encode a 30 second clip to remove
    a fifth of it. Tiny by both measures means the output would be the same file.

    The line is set to leave measured material alone: an ordinary 11.5 minute video
    at 1.0% / 6s still renders. What it catches is 0.0% / 0.3s — the second pass
    over an already cut file.
    """
    return not (
        plan.removed_seconds < MIN_WORTHWHILE_REMOVED_SECONDS
        and (plan.silence_ratio_percent or 0.0) < MIN_WORTHWHILE_REMOVED_PERCENT
    )


def already_cut_reason(source: Path, runner: Callable[..., Any] = silence_cuts.run_command) -> str | None:
    """Whether this file has already had its breath gaps removed, and how we know.

    Not a safety check. A second pass over a cut file was measured on a real 15
    minute output: 2 cuts, 0.3 seconds, 0.0% — the padding left at every join is
    shorter than ``min_silence_seconds``, so silencedetect does not even report it.
    Re-cutting does not damage anything. What it does is spend a full re-encode of
    the whole file to gain a third of a second, and leave a second near-duplicate
    file (``x_debreath_debreath.mov``) beside the original.

    Two answers are possible and only one of them is certain:

    - **Our own output**, recognised by the mark this product writes into the file
      when it renders one. That mark travels with the bytes, so a renamed or moved
      file is still recognised. Certain.
    - **A name that looks like one** (`..._debreath.mp4`). A useful hint, and the
      only thing available for a file whose bytes were re-encoded by something else.

    What cannot be answered: whether a file some other tool cut is already cut.
    There is no signature for that, and none is needed — the measurement above is
    why. Such a file simply runs and finds ~0% to remove, which is a safe landing
    rather than a detection.
    """
    try:
        if silence_cuts.carries_cut_mark(source, runner=runner):
            return "这份文件是 FluentFlow 剪过的（文件里有标记），空白已经去掉了，不再花一次编码重剪。"
    except (SilenceCutError, OSError):  # pragma: no cover - probing only
        pass
    if CUT_FILE_NAME_MARKER in source.stem:
        return f"文件名看起来已经是剪过的版本（{source.name}），没有再剪一遍，直接转写。"
    return None


@dataclass(frozen=True)
class PreparedCut:
    """The outcome of cutting a recording *before* anything else reads it.

    ``path`` is the file the rest of the pipeline should use. It is the cut file
    only when using it is safe; every other outcome falls back to the recording
    and says why in ``state``, because at this point in the pipeline the
    alternative to falling back is losing the user's transcription.
    """

    path: Path
    used: bool
    state: dict[str, Any]
    artifacts: dict[str, dict[str, Any]]


def prepare_cut_media(
    task_id: str,
    source: Path,
    *,
    noise_db: float = silence_cuts.DEFAULT_NOISE_DB,
    min_silence_seconds: float = silence_cuts.DEFAULT_MIN_SILENCE_SECONDS,
    padding_seconds: float = silence_cuts.DEFAULT_PADDING_SECONDS,
    runner: Callable[..., Any] = silence_cuts.run_command,
) -> PreparedCut:
    """Cut the breath gaps before transcription, or explain why the original stands.

    Cutting first is what makes everything downstream belong to one file: the
    transcript is made *from* the shortened audio, so its timestamps are the
    shortened file's from the start and nothing has to be remapped for the page,
    the note, or the subtitles.

    It also moves one risk earlier, and that risk decides most of this function.
    Words the cut removes are words the transcript will never contain, and
    getting them back costs a whole re-transcription. So this never fails the
    job and never guesses:

    - **Cuts too close to the speech → transcribe the original.** The engine's own
      spot check is the only thing that catches a faintly recorded room, where the
      default threshold cuts a couple of decibels below the voice while every count
      looks reasonable. When it reports that, the cut list is still kept (it is the
      deliverable, and re-rendering it at a wider threshold is cheap) but the
      recording is what gets transcribed.
    - **Nothing to cut → the recording already is the shortened version.** Recorded
      as such, not treated as a failure.
    - **Anything going wrong → the recording.** An unsupported container, ffmpeg
      missing, a render that will not verify: none of those are worth costing
      somebody their upload. The reason is recorded and the entry on the result
      page can still be used by hand afterwards.

    Writes no job result: the caller owns the result at this point in the
    pipeline, and this returns the state and artifacts to merge into it.
    """
    # A threshold that suits this material rather than an absolute number that suits
    # a normal one. Only when the caller left the default: an explicit setting is the
    # caller's judgement and is not second-guessed.
    threshold_choice: dict[str, Any] = {}
    if noise_db == silence_cuts.DEFAULT_NOISE_DB:
        try:
            noise_db, threshold_choice = silence_cuts.suggest_noise_db(source, runner=runner)
        except (SilenceCutError, OSError) as exc:  # pragma: no cover - measurement only
            logger.warning("could not measure the level of %s: %s", source, exc)
    settings = {
        "noise_db": noise_db,
        "min_silence_seconds": min_silence_seconds,
        "padding_seconds": padding_seconds,
        "render_requested": True,
    }
    base: dict[str, Any] = {
        "threshold_choice": threshold_choice or None,
        "status": STATUS_COMPLETED,
        "stage": "done",
        "settings": settings,
        "ran_before_transcription": True,
        "started_at": _now(),
        "error": None,
    }

    def fallback(reason: str, **extra: Any) -> PreparedCut:
        """The recording stands, and the result says why in the user's words."""
        return PreparedCut(
            path=source,
            used=False,
            state={
                **base,
                "rendered": False,
                "used_for_transcription": False,
                "not_used_reason": reason,
                "finished_at": _now(),
                **extra,
            },
            artifacts={},
        )

    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        return fallback(f"不支持这种文件（{source.suffix or '无扩展名'}），没有去气口，按原文件处理。")
    # Not for safety — re-cutting was measured harmless. This skips a re-encode of
    # the whole file that would gain a fraction of a second.
    already = already_cut_reason(source, runner=runner)
    if already:
        return fallback(already, already_cut=True)

    artifacts: dict[str, dict[str, Any]] = {}
    try:
        plan = silence_cuts.plan_silence_cuts(
            source,
            noise_db=noise_db,
            min_silence_seconds=min_silence_seconds,
            padding_seconds=padding_seconds,
            runner=runner,
        )
        cut_list = write_text_artifact(
            task_id,
            CUT_LIST_KIND,
            _cut_list_filename(source),
            json.dumps(plan.as_dict(), ensure_ascii=False, indent=1) + "\n",
        )
        artifacts[CUT_LIST_KIND] = cut_list
        measured = {
            **base,
            "plan": _plan_payload(plan),
            "warnings": list(plan.warnings),
            "cut_list_updated_at": _now(),
        }
        if not plan.cuts:
            return PreparedCut(
                path=source,
                used=False,
                state={**measured, "rendered": False, "used_for_transcription": False,
                       "finished_at": _now()},
                artifacts=artifacts,
            )

        if not _worth_rendering(plan):
            # Rendering re-encodes 100% of the file. Removing a fraction of a
            # percent of it produces a near-duplicate beside the original for a
            # saving measured in seconds. This is the general form of the
            # already-cut check above, and it covers what that cannot: a file some
            # other tool cut has no mark to find, but it lands here — measured at
            # 0.0% / 0.3s on a real cut file.
            return PreparedCut(
                path=source,
                used=False,
                state={
                    **measured,
                    "rendered": False,
                    "used_for_transcription": False,
                    "not_used_reason": (
                        f"可剪的空白只有 {plan.removed_seconds:.1f} 秒"
                        f"（占 {plan.silence_ratio_percent}%），"
                        "重新导出一遍整个文件不值得，这次按原文件转写。"
                    ),
                    "not_worth_rendering": True,
                    "finished_at": _now(),
                },
                artifacts=artifacts,
            )

        separation = plan.level_separation if isinstance(plan.level_separation, dict) else {}
        if separation.get("measured") and not separation.get("separated", True):
            # The one case where cutting first would destroy something. Keep the
            # list, transcribe the recording, and say it in the caller's words.
            return PreparedCut(
                path=source,
                used=False,
                state={
                    **measured,
                    "rendered": False,
                    "used_for_transcription": False,
                    "not_used_reason": (
                        "抽查发现剪掉的部分只比紧邻的说话声低一点点，这份材料录得很轻。"
                        "为了不把话剪掉，这次按原文件转写；剪辑表保留了，"
                        "可以把「最短静音时长」调大之后自己再剪一次。"
                    ),
                    "finished_at": _now(),
                },
                artifacts=artifacts,
            )

        media_filename = _media_filename(source)
        output = artifact_target_path(task_id, media_filename)
        report = silence_cuts.render_cut_plan(source, plan, output, runner=runner)
        artifacts[MEDIA_KIND] = describe_existing_artifact(task_id, MEDIA_KIND, media_filename)
        if not report.ok:
            # The file is kept and downloadable, as everywhere else here — but a
            # render that does not measure up to its own plan is not something to
            # transcribe and then call the task's material.
            return PreparedCut(
                path=source,
                used=False,
                state={
                    **measured,
                    "rendered": True,
                    "media_filename": media_filename,
                    "render": report.as_dict(),
                    "render_verified": False,
                    "used_for_transcription": False,
                    "not_used_reason": (
                        "剪出来的文件和剪辑表对不上（没通过自检），这次按原文件转写。"
                        "剪后的文件保留了下来，可以自己听一遍。"
                    ),
                    "finished_at": _now(),
                },
                artifacts=artifacts,
            )
        return PreparedCut(
            path=output,
            used=True,
            state={
                **measured,
                "rendered": True,
                "media_filename": media_filename,
                "render": report.as_dict(),
                "render_verified": True,
                "used_for_transcription": True,
                "finished_at": _now(),
            },
            artifacts=artifacts,
        )
    except (SilenceCutError, OSError, ValueError) as exc:
        logger.warning("pre-transcription debreath failed for %s: %s", task_id, exc)
        return PreparedCut(
            path=source,
            used=False,
            state={
                **base,
                "status": STATUS_FAILED,
                "rendered": False,
                "used_for_transcription": False,
                "error": str(exc),
                "not_used_reason": f"去气口没成功（{exc}），这次按原文件处理。",
                "finished_at": _now(),
            },
            artifacts=artifacts,
        )


def resolve_source(task_id: str) -> Path:
    """The file to cut, whether it was copied in or left where it sits.

    Looking only in FluentFlow's own store told by-path tasks their source had
    expired while it sat untouched on the disk — and because the note needs cut
    media, that answer also dead-ended the note with no way out (reported
    2026-09-03 on an 18-minute meeting recording under ~/Movies).
    """
    source = find_source_file(task_id) or in_place_source_path(get_job(task_id))
    if not source:
        raise DebreathError("源文件已不在本机（可能已过保留期），无法剪辑")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise DebreathError(f"不支持这种文件（{source.suffix or '无扩展名'}）")
    return source


def run_debreath(
    task_id: str,
    *,
    client_id: str | None = None,
    noise_db: float = silence_cuts.DEFAULT_NOISE_DB,
    min_silence_seconds: float = silence_cuts.DEFAULT_MIN_SILENCE_SECONDS,
    padding_seconds: float = silence_cuts.DEFAULT_PADDING_SECONDS,
    render: bool = True,
    allow_concurrent_render: bool = False,
    claimed: bool = False,
    runner: Callable[..., Any] = silence_cuts.run_command,
) -> dict[str, Any]:
    """Plan, store the cut list, optionally render, and record the outcome.

    Runs to completion in the calling thread; callers that serve HTTP should hand
    it to a background task. Every transition is persisted, so a job poll shows
    where it is.

    ``claimed`` says the caller already holds the render slot — an HTTP route
    claims while the requester is still waiting, so a refusal is an answer rather
    than a log line. The slot is released here either way.
    """
    source = resolve_source(task_id)
    if not claimed:
        claim(task_id, allow_concurrent=allow_concurrent_render)
    settings = {
        "noise_db": noise_db,
        "min_silence_seconds": min_silence_seconds,
        "padding_seconds": padding_seconds,
        "render_requested": render,
    }
    try:
        _store(
            task_id,
            client_id=client_id,
            state={
                "status": STATUS_RUNNING,
                "stage": "detecting",
                "started_at": _now(),
                "settings": settings,
                "error": None,
            },
        )
        plan = silence_cuts.plan_silence_cuts(
            source,
            noise_db=noise_db,
            min_silence_seconds=min_silence_seconds,
            padding_seconds=padding_seconds,
            runner=runner,
        )
        cut_list = write_text_artifact(
            task_id,
            CUT_LIST_KIND,
            _cut_list_filename(source),
            json.dumps(plan.as_dict(), ensure_ascii=False, indent=1) + "\n",
        )
        state: dict[str, Any] = {
            "stage": "rendering" if (render and plan.cuts) else "done",
            "plan": _plan_payload(plan),
            "warnings": list(plan.warnings),
            "cut_list_updated_at": _now(),
        }
        artifacts: dict[str, dict[str, Any]] = {CUT_LIST_KIND: cut_list}
        # Subtitles for the shortened version, from the cut list. Written with the
        # cut list rather than with the render, because both are derived from the
        # same judgement and the list is the deliverable — a caller that asked for
        # the plan only still gets a transcript it can read against it.
        if plan.cuts:
            subtitles, remap = _remapped_subtitles(task_id, source, plan, client_id=client_id)
            if subtitles:
                artifacts[TRANSCRIPT_KIND] = subtitles
            if remap:
                state["transcript_timeline"] = remap
        _store(task_id, client_id=client_id, state=state, artifacts=artifacts)

        if not render or not plan.cuts:
            # Nothing to cut is a real answer, not a failure: the file would come
            # back identical, and re-encoding it to prove that wastes the CPU.
            return _store(
                task_id,
                client_id=client_id,
                state={
                    "status": STATUS_COMPLETED,
                    "stage": "done",
                    "rendered": False,
                    "finished_at": _now(),
                },
            )

        media_filename = _media_filename(source)
        output = artifact_target_path(task_id, media_filename)
        report = silence_cuts.render_cut_plan(source, plan, output, runner=runner)
        # Registered under the relative path, not the bare name: the file is in
        # the de-breath directory, and a record naming only the basename would
        # point at a task root that does not hold it.
        media = describe_existing_artifact(task_id, MEDIA_KIND, media_filename)
        return _store(
            task_id,
            client_id=client_id,
            state={
                "status": STATUS_COMPLETED,
                "stage": "done",
                "rendered": True,
                # Where the shortened file is, relative to the task's artifact
                # directory. Recorded so the note step and the page can name it
                # without reconstructing the filename rule.
                "media_filename": media["filename"],
                "render": report.as_dict(),
                # A render that fails its own checks is kept and flagged. The
                # checks measure; they do not delete.
                "render_verified": report.ok,
                # There is now a cut file, so it is the file to read from — which
                # is what this flag answers for everything downstream. Without
                # setting it here, a run that reaches this line still carries the
                # False left by a pre-transcription attempt that declined to use
                # its own render, and the note step goes looking for the original
                # recording instead. On a faintly recorded lecture that declined
                # once and was re-cut with a longer minimum silence, that left a
                # finished cut file on disk and a note that refused to be written,
                # reported as "the cut file is no longer on this machine".
                #
                # The subtitles are remapped above, before the render, so the cut
                # file already carries captions on its own clock; a transcript
                # made from the recording is not a reason to send the note back to
                # the recording.
                "used_for_transcription": True,
                "not_used_reason": None,
                "finished_at": _now(),
            },
            artifacts={MEDIA_KIND: media},
        )
    except DebreathError:
        raise
    except SilenceCutError as exc:
        _store(
            task_id,
            client_id=client_id,
            state={"status": STATUS_FAILED, "stage": "done", "error": str(exc), "finished_at": _now()},
        )
        raise DebreathError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - the job must not be left "running"
        logger.exception("debreath failed for %s", task_id)
        _store(
            task_id,
            client_id=client_id,
            state={
                "status": STATUS_FAILED,
                "stage": "done",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": _now(),
            },
        )
        raise
    finally:
        release(task_id)


def recover_stranded_renders() -> int:
    """Fail any de-breath left saying "running" by a previous process.

    The render slot lives in process memory, so a service killed mid-encode
    leaves ``running`` in a result that nothing will ever finish — and the route
    refuses a task in that state, so the entry would be dead for good. Cleared at
    startup for the same reason ``recover_stale_jobs`` clears stranded tasks.

    The scan is over completed jobs, because that is the only state a de-breath
    can start from, and it reads results one client at a time through the
    ordinary store API rather than reaching into SQLite.
    """
    recovered = 0
    for job in list_jobs_by_statuses(("completed",), include_result=True):
        task_id = str(job.get("task_id") or "")
        if not task_id or not is_running(job.get("result")):
            continue
        result = dict(job.get("result") or {})
        result["debreath"] = {
            **debreath_state(result),
            "status": STATUS_FAILED,
            "stage": "done",
            "error": "服务重启中断了这次去气口：剪辑表如果已经生成会保留，重新发起即可。",
            "finished_at": _now(),
        }
        if update_job_result(task_id, result, client_id=job.get("client_id")):
            recovered += 1
    return recovered
