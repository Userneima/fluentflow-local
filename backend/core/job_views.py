"""The two shapes a job result takes on the wire, and the wall between them.

A job has exactly two representations:

* **the record** — everything, including the note body and the full transcript.
  Served by ``GET /jobs/{id}``. This is what an editor may edit and save.
* **a list row** — metadata plus short previews, cheap enough to send a hundred
  at a time. Served by ``GET /jobs``. Nothing may be edited from it.

They used to be the same dict with some fields shortened, which is how a
240-character preview reached the note editor under the name
``summary_markdown``. The editor could not tell it apart from the real note, its
autosave fired, and a 13,021-character note became a 240-character stub with no
recoverable copy.

So the wall here is structural rather than advisory: a list row is *built* from
an allowlist and can only contain preview-named fields, and
``assert_is_list_row`` re-checks that on the way out. A future edit that
reintroduces a body field under its canonical name fails immediately instead of
quietly shipping a stub that something downstream will save.
"""

from __future__ import annotations

from typing import Any, Final

from backend.core.result_schema import normalize_result_for_read

PREVIEW_CHARS: Final[int] = 240

# Field names that mean "this is the record itself". A list row carrying any of
# them is indistinguishable from a record to every consumer downstream.
RECORD_BODY_FIELDS: Final[frozenset[str]] = frozenset({
    "summary_markdown",
    "transcript_text",
    "cleaned_transcript_text",
    "raw_transcript_text",
    "raw_segments",
    "display_segments",
    "stt_raw_segments",
    "segments",
})

# Metadata a list row may carry verbatim: small, and needed to render a row.
_LIST_ROW_FIELDS: Final[tuple[str, ...]] = (
    "task_id",
    "status",
    "filename",
    "raw_title",
    "display_title",
    "audio_duration_seconds",
    "stt_elapsed_seconds",
    "stt_realtime_factor",
    "stt_provider",
    "stt_provider_label",
    "stt_model",
    "stt_speed",
    "stt_language",
    "detected_language",
    "source_language",
    "subtitle_mode",
    "translation_status",
    "translation_error",
    "summary_status",
    "summary_error",
    "summary_skipped",
    # Who wrote the note. Small metadata, and load-bearing for an agent listing
    # tasks to work through: without it, "write notes for everything that needs
    # one" cannot tell a pipeline note from one the agent itself already wrote,
    # so a second run rewrites its own work.
    "summary_edited",
    "summary_source",
    "summary_source_label",
    "feishu_doc_url",
    "lark_error",
    "source_fingerprint",
    "playback_audio_available",
    "source_file_available",
    "requested_note_mode",
    "resolved_note_mode",
    "note_mode_chunk_count",
    "note_mode_segment_count",
    "note_mode_evidence_count",
    "note_mode_chapter_count",
    "note_mode_important_evidence_count",
    "note_mode_covered_important_evidence_count",
    "note_mode_coverage_missing_count",
    "note_mode_plan_reason",
    "note_mode_plan_confidence",
    "note_mode_plan_warnings",
    "note_mode_plan_provider",
    "note_mode_plan_model",
    "note_mode_plan_fallback",
    "note_mode_plan_error",
    "note_mode_plan_selected_mode",
    "prompt_preset",
    "prompt_preset_label",
    "imported_from_local_history",
)


def assert_is_list_row(row: dict[str, Any]) -> dict[str, Any]:
    """Raise if a projection leaked a record body field. Returns the row."""
    leaked = sorted(RECORD_BODY_FIELDS.intersection(row))
    if leaked:
        raise AssertionError(
            "A job list row must never carry record body fields "
            f"(leaked: {', '.join(leaked)}). Add a *_preview field instead — see "
            "backend/core/job_views.py."
        )
    return row


def _preview(value: Any) -> str:
    return str(value)[:PREVIEW_CHARS] if value else ""


def job_list_row(result: Any) -> dict[str, Any] | None:
    """Project a stored result into a list row: metadata plus previews only."""
    result = normalize_result_for_read(result)
    if not isinstance(result, dict):
        return None

    summary_markdown = result.get("summary_markdown") or ""
    transcript_text = result.get("transcript_text") or result.get("transcript_text_preview") or ""
    lark_response = result.get("lark_response") if isinstance(result.get("lark_response"), dict) else None

    row: dict[str, Any] = {field: result.get(field) for field in _LIST_ROW_FIELDS}
    row.update({
        # Consumers branch on this rather than guessing from field lengths.
        "result_partial": True,
        "summary_preview": _preview(summary_markdown),
        "summary_markdown_chars": len(str(summary_markdown)),
        "transcript_text_preview": _preview(transcript_text),
        "transcript_text_chars": len(str(transcript_text)),
        "artifacts": result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {},
        "lark_response": {"url": lark_response.get("url")} if lark_response and lark_response.get("url") else None,
    })
    return assert_is_list_row(row)
