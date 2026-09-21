#!/usr/bin/env python3
"""Measure what the frame pass would supply, without paying a model.

The visual note has two halves that cost very differently. Writing the note is
minutes of model time and real money — 125s, 255s and 381s on the three course
recordings, at $1.10 to $1.96 each. Choosing the frames is ffmpeg on a file that
is already on disk: 9 to 38 seconds, free. Almost every frame-selection defect
found so far — the four-look demo class, a slide deck deduped down to three
slides, a talking-head recording paying for twenty webcam shots — is visible in
the free half alone.

So this runs only that half, against the cut files the task already produced, and
prints what the model *would* have been sent. Nothing is uploaded, nothing is
re-cut, nothing is re-transcribed, and the task's own frames are never touched.

    ./venv/bin/python scripts/measure_frame_supply.py
    ./venv/bin/python scripts/measure_frame_supply.py --json before.json
    # change the extractor, then
    ./venv/bin/python scripts/measure_frame_supply.py --json after.json

The columns worth watching, and what a bad number looks like:

- ``frames``: how many distinct pictures the recording has, which is what gets
  offered to a channel that can open files for itself. Measured at 79 to 100+ on
  four recordings, against the 20 a single request can carry.
- ``per_look``: minutes of recording per distinct picture. Now a property of the
  material rather than of a budget, since nothing is trimmed to fit a request.
- ``max_gap``: the longest stretch carrying no frame. This is not automatically a
  fault — a static screen genuinely produces one — but it should be explainable.
- ``sources``: ``scene`` was found by detection, ``cue`` is a sampled look moved
  onto a subtitle cue, ``floor`` is a sampled look in silence. A file that is all
  ``floor`` is one where detection contributed nothing.

Shortening a recording to make this faster would defeat it: length is the thing
under test. Use a purpose-built short fixture for logic, and the real recordings
for supply.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core import cut_timeline  # noqa: E402
from backend.core import frame_extractor  # noqa: E402
from backend.core import visual_note_job  # noqa: E402
from backend.core.claude_vision import FRAME_ATTACH_MAX, FRAME_INDEX_CAP  # noqa: E402
from backend.core.job_store import list_jobs  # noqa: E402

SCENE_THRESHOLD = 0.3


def _measurable_media(task_id: str, result: dict[str, Any]) -> Path | None:
    """The de-breathed file, via the product's own lookup rather than a copy of it.

    ``cut_media`` already refuses to guess, never substitutes the original
    recording for a missing cut file, and knows whether the file has a picture at
    all. Re-deriving any of that here would let the measurement drift away from
    what the run actually reads, which is the one thing it exists to report.
    """
    media = visual_note_job.cut_media(task_id, result)
    if media is None or not media.has_video:
        return None
    return media.path


def _cue_seconds(result: dict[str, Any]) -> list[float]:
    segments = cut_timeline.timeline_segments(result)
    cues: list[float] = []
    for segment in segments:
        try:
            cues.append(round(float(segment.get("start")), 1))
        except (TypeError, ValueError):
            continue
    return sorted(set(cues))


def _previous_run(result: dict[str, Any]) -> tuple[int, int] | None:
    """What the last real note actually sent and cited, for comparison."""
    state = result.get("visual_note")
    if not isinstance(state, dict) or not state.get("frames_sent"):
        return None
    return len(state.get("frames_sent") or []), len(state.get("frames_cited") or [])


def measure(task_id: str, media: Path, result: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fluentflow-frame-supply-") as scratch:
        frames = frame_extractor.extract_candidate_frames(
            str(media),
            Path(scratch),
            scene_threshold=SCENE_THRESHOLD,
            max_scene_frames=FRAME_INDEX_CAP,
            anchor_seconds=_cue_seconds(result),
        )
        usable = [frame for frame in frames if frame.get("low_information") is not True]
        stamps = [float(frame.get("timestamp_seconds") or 0.0) for frame in usable]
        sources: dict[str, int] = {}
        for frame in usable:
            key = str(frame.get("source") or "unknown")
            sources[key] = sources.get(key, 0) + 1
        distances = [
            float(frame["duplicate_distance"]) for frame in usable
            if frame.get("duplicate_distance") is not None
        ]
    duration = float(result.get("audio_duration_seconds") or result.get("duration_seconds") or 0.0)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])] or [0.0]
    return {
        "task_id": task_id,
        "filename": str(result.get("filename") or ""),
        "duration_seconds": round(duration, 1),
        "frames": len(usable),
        "index_cap": FRAME_INDEX_CAP,
        "attach_max": FRAME_ATTACH_MAX,
        "sources": sources,
        "per_look_seconds": round(duration / len(usable), 1) if usable else None,
        "max_gap_seconds": round(max(gaps), 1),
        "closest_duplicate_percent": round(min(distances), 2) if distances else None,
        "previous_run": _previous_run(result),
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


HEADER = f'{"recording":<28}{"task":>9}{"min":>5}{"frames":>7}{"per look":>10}{"max gap":>9}  sources / last real run'


def _render_row(row: dict[str, Any]) -> str:
    per_look = row["per_look_seconds"]
    previous = row["previous_run"]
    tail = ", ".join(f"{k}={v}" for k, v in sorted(row["sources"].items())) or "no frames"
    if previous:
        tail += f"  (last run sent {previous[0]}, cited {previous[1]})"
    return (
        f'{row["filename"][:26]:<28}'
        f'{row["task_id"][:8]:>9}'
        f'{row["duration_seconds"] / 60:>5.0f}'
        f'{row["frames"]:>7}'
        f'{("-" if per_look is None else f"{per_look / 60:.1f}min"):>10}'
        f'{row["max_gap_seconds"] / 60:>8.1f}m  {tail}'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", action="append", default=[], help="Task id to measure; repeatable. Default: every task with a cut file.")
    parser.add_argument("--limit", type=int, default=200, help="How many recent jobs to consider (default 200).")
    parser.add_argument("--json", type=Path, help="Also write the rows here, for diffing against another run.")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    print(HEADER, flush=True)
    for job in list_jobs(limit=args.limit):
        task_id = str(job.get("task_id") or "")
        if args.task and task_id not in args.task:
            continue
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        media = _measurable_media(task_id, result)
        if media is None:
            continue
        row = measure(task_id, media, result)
        rows.append(row)
        # Printed as each one lands: a silent minute reads as a hang, and the
        # first row is often enough to know the change went the wrong way.
        print(_render_row(row), flush=True)

    if not rows:
        print(
            "No task has a de-breathed file on disk, so there is nothing to measure "
            "without re-cutting. Run the de-breath step on a recording first.",
            file=sys.stderr,
        )
        return 1

    print(f"\n{len(rows)} recordings, {sum(row['elapsed_seconds'] for row in rows):.0f}s total, no model calls.")
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
