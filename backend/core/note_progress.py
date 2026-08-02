"""Turn note-generation step events into UI progress.

Note generation is the longest silent stretch in the product: on a three-hour
transcript it is minutes of model calls with nothing on screen. `/process`
already streams stage/progress over SSE, but note regeneration was a single
blocking POST — the button spun and the user could not tell a slow run from a
hung one.

`ai_summarizer` reports each finished model call as
``{"step", "completed", "total"}``. This module is the only place that decides
what those steps are worth in percent, so the pipeline stays free to reorder
its work without every caller re-deriving a progress bar.
"""

from __future__ import annotations

from typing import Any, Final

# (step, share of the bar). Ordered as the pipeline runs them. Evidence
# extraction and chapter writing dominate wall-clock because they are the only
# fan-out steps; the single calls are cheap by comparison — except `revision`,
# a conditional full rewrite that only fires when the coverage check rejects
# the draft. Its share is reserved at the end so a run that skips it simply
# finishes early instead of stalling at 85%.
STEP_WEIGHTS: Final[tuple[tuple[str, float], ...]] = (
    ("evidence", 0.40),
    ("outline", 0.05),
    ("chapters", 0.30),
    ("note", 0.30),
    ("style", 0.05),
    ("coverage", 0.05),
    ("revision", 0.15),
)

STEP_LABELS: Final[dict[str, tuple[str, str]]] = {
    "evidence": ("提取要点", "Extracting key points"),
    "outline": ("规划章节", "Planning chapters"),
    "chapters": ("撰写章节", "Writing chapters"),
    "note": ("撰写笔记", "Writing the note"),
    "style": ("统一文风", "Unifying the style"),
    "coverage": ("检查覆盖度", "Checking coverage"),
    "revision": ("按覆盖度返修", "Revising for coverage"),
}

_WEIGHT_BY_STEP: Final[dict[str, float]] = dict(STEP_WEIGHTS)
_ORDER: Final[dict[str, int]] = {step: index for index, (step, _) in enumerate(STEP_WEIGHTS)}


def note_progress_fraction(step: str, completed: int, total: int) -> float:
    """Fraction of note generation finished once `completed`/`total` of `step` is done.

    Steps the current mode never runs are simply absent from the events, so
    their weight is skipped rather than stranding the bar. Unknown steps
    contribute nothing but never move the bar backwards.
    """
    index = _ORDER.get(step)
    if index is None:
        return 0.0
    done = sum(weight for name, weight in STEP_WEIGHTS if _ORDER[name] < index)
    if total > 0:
        done += _WEIGHT_BY_STEP[step] * max(0, min(completed, total)) / total
    return min(1.0, done)


def note_progress_event(
    event: dict[str, Any],
    *,
    start: float,
    end: float,
) -> dict[str, Any]:
    """Map one summarizer step event onto a `start`..`end` slice of a job's bar."""
    step = str(event.get("step") or "")
    completed = int(event.get("completed") or 0)
    total = int(event.get("total") or 0)
    fraction = note_progress_fraction(step, completed, total)
    label_zh, label_en = STEP_LABELS.get(step, (step, step))
    return {
        "stage": "summary",
        "progress": round(start + (end - start) * fraction, 1),
        "note_step": step,
        "note_step_completed": completed,
        "note_step_total": total,
        "note_step_label": label_zh,
        "note_step_label_en": label_en,
    }
