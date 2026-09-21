#!/usr/bin/env python3
"""Cut the breath gaps out of a folder of recordings, and optionally transcribe them.

This is the batch shape of what the product does per task, for the case the product
does not cover: a directory of files that live outside FluentFlow, processed in place,
with no task ids and no backend running. It calls the engine directly
(`backend/core/silence_cuts.py`), so it needs ffmpeg and nothing else — no server, no
API key, no network.

Use the product's own entries instead when the recording should become a task with
history, a note, and an export. This script is for "process these files where they
lie".

Each guard here exists because the ad-hoc version of this script got it wrong on real
material, 2026-08-26:

* A second run re-processed its own output, because the products were named after the
  input and matched the same glob. 15 files were cut and transcribed twice, 246MB of
  duplicates. Now the mark the render leaves in the file is checked, and so is the
  suffix.
* The file list was built by the shell, and every recording had spaces in its name, so
  a whole batch measured nothing and said so only as "unreadable". Paths are walked
  here.
* Four of ten files removed 0.0% and the run reported "ok" for each. A cut that found
  nothing is now called out in the per-file line and again in the summary, because
  that is the shape of a threshold that does not suit the material — see the threshold
  note in `silence_cuts.suggest_noise_db`.

`--scale-width` is the one option no measurement can choose for you, so it is off by
default. Read the smallest text in a few frames before setting it. Carrying a width
over from another recording is what produced the failure it is documented against: a
whiteboard recording full of syllabus text was halved on the strength of a face-and-
chat recording that had halved cleanly, and the text came out barely readable. A
whole-frame metric will not save you either — a whiteboard is mostly blank
background, so the average washes out the one region that broke.

Examples:
    # Plan only. Prints the threshold each file gets and what it would remove.
    python3 scripts/debreath_batch.py --dry-run "~/recordings"

    # Cut, then transcribe the cut file so the subtitles belong to the file being watched
    python3 scripts/debreath_batch.py --transcribe "~/recordings"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.silence_cuts import (  # noqa: E402
    SilenceCutError,
    carries_cut_mark,
    plan_silence_cuts,
    render_cut_plan,
)

MEDIA_SUFFIXES = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg",
                  ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
CUT_SUFFIX = "_debreath"

# Below this, rendering costs a lossy re-encode and buys nothing. Measured: two
# recordings found 1 and 10 gaps, removed 0.0% after rounding, and were re-encoded
# anyway — 57.9MB in, 57.5MB out, for a file the same length as the original.
MIN_WORTHWHILE_REMOVED_PERCENT = 0.5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="+", help="Files or directories to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan and report; write nothing")
    parser.add_argument("--transcribe", action="store_true",
                        help="Transcribe the cut file locally and write .srt and .txt beside it")
    parser.add_argument("--noise-db", type=float, default=None,
                        help="Silence threshold in dB. Omit to let the engine measure one "
                             "per file, which is the point of this script")
    parser.add_argument("--min-silence-seconds", type=float, default=None,
                        help="Shortest gap worth cutting")
    parser.add_argument("--scale-width", type=int, default=None,
                        help="Downscale the picture to this width, aspect ratio kept. LOOK AT "
                             "THE RECORDING FIRST — grab a few frames and read the smallest "
                             "text in them. Dense small text (a whiteboard, code, body copy, "
                             "tables) must keep its pixels; faces, large text and static "
                             "backgrounds can be halved. Do not carry a width over from "
                             "another recording")
    parser.add_argument("--model-size", default="large-v3",
                        help="Local transcription model size (--transcribe only)")
    parser.add_argument("--language", default=None,
                        help="Transcription language hint, e.g. zh (--transcribe only)")
    parser.add_argument("--force", action="store_true",
                        help="Process files that already look cut. Off by default: the "
                             "first version of this script cut its own output")
    return parser.parse_args(argv)


def collect_media(paths: list[str], *, force: bool) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Every recording under these paths, and the ones deliberately left alone.

    Walked here rather than expanded by the shell: these filenames contain spaces,
    and a shell-split list silently measures nothing.
    """
    found: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for raw in paths:
        root = Path(raw).expanduser()
        candidates = (
            sorted(f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES)
            if root.is_dir()
            else [root]
        )
        for path in candidates:
            if not path.is_file():
                skipped.append((path, "not a file"))
                continue
            if not force and CUT_SUFFIX in path.stem:
                skipped.append((path, "named like a cut file"))
                continue
            if not force and _already_cut(path):
                skipped.append((path, "carries this product's cut mark"))
                continue
            found.append(path)
    return found, skipped


def _already_cut(path: Path) -> bool:
    """The mark travels with the bytes, so a renamed or moved output is still known."""
    try:
        return carries_cut_mark(path)
    except (SilenceCutError, OSError):
        return False


def output_for(source: Path) -> Path:
    return source.with_name(f"{source.stem}{CUT_SUFFIX}{source.suffix}")


def process(source: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Plan, render, optionally transcribe. Returns what happened, never raises."""
    started = time.time()
    record: dict[str, Any] = {"source": str(source), "ok": False}
    plan_kwargs: dict[str, Any] = {}
    if args.noise_db is not None:
        plan_kwargs["noise_db"] = args.noise_db
    if args.min_silence_seconds is not None:
        plan_kwargs["min_silence_seconds"] = args.min_silence_seconds

    plan = plan_silence_cuts(source, **plan_kwargs)
    summary = plan.as_dict()
    choice = summary.get("threshold_choice") or {}
    record.update(
        noise_db=summary["detection"]["noise_db"],
        threshold_adapted=bool(choice.get("adapted")),
        quiet_floor_db=choice.get("quiet_floor_db"),
        speech_level_db=choice.get("speech_level_db"),
        cut_count=summary["cut_count"],
        removed_percent=summary["removed_percent"],
        separation_db=(summary.get("level_separation") or {}).get("separation_db"),
        warnings=list(summary["warnings"]),
        cut_list=summary,
    )

    if args.dry_run:
        record.update(ok=True, action="planned", seconds=round(time.time() - started, 1))
        return record

    cut_list_path = source.with_name(f"{source.stem}_cut_list.json")
    cut_list_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    record["cut_list_path"] = str(cut_list_path)

    # A plan that keeps everything — or keeps all but a rounding error — has nothing
    # worth rendering, and rendering it would hand back a re-encode of the original
    # dressed up as a result.
    if not plan.cuts or summary["removed_percent"] < MIN_WORTHWHILE_REMOVED_PERCENT:
        if args.transcribe:
            # Nothing was cut, so the original IS the file that will be listened to and
            # its transcript belongs to it. Skipping the transcript here would leave the
            # batch without one for exactly the recordings that could not be shortened.
            record["transcript"] = transcribe(source, args)
        record.update(ok=True, action="nothing worth cutting",
                      seconds=round(time.time() - started, 1))
        return record

    output = output_for(source)
    report = render_cut_plan(source, plan, output, scale_width=args.scale_width)
    record.update(
        output=str(output),
        verified=report.ok,
        failed_checks=list(report.failed_checks),
        source_mb=round(source.stat().st_size / 1_048_576, 1),
        output_mb=round(output.stat().st_size / 1_048_576, 1) if output.is_file() else None,
    )
    if args.transcribe:
        record["transcript"] = transcribe(output, args)
    record.update(ok=True, action="cut", seconds=round(time.time() - started, 1))
    return record


def transcribe(media: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Transcribe the CUT file, so every timestamp belongs to the file being watched.

    Transcribing the original and mapping afterwards is the other way round, and it is
    how a note ends up describing one file with another file's clock.
    """
    from backend.core.local_stt import transcribe_audio
    from backend.core.subtitle_format import _format_srt

    result = transcribe_audio(
        media, model_size=args.model_size, language=args.language,
        engine="auto", word_timestamps=True,
    )
    segments = [{"start": s.start, "end": s.end, "text": s.text} for s in result.segments]
    # Not with_suffix(): a filename that starts with "5." makes Path treat everything
    # after that dot as the extension, and the .srt lands in a file called "5.srt".
    base = media.parent / media.name.rsplit(".", 1)[0]
    Path(f"{base}.srt").write_text(_format_srt(segments), encoding="utf-8")
    Path(f"{base}.txt").write_text((result.text or "").strip() + "\n", encoding="utf-8")
    Path(f"{base}_transcript.json").write_text(
        json.dumps(
            {
                "engine": result.engine,
                "language": result.language,
                "duration": result.duration,
                # Word timings cost a whole re-transcription to recover, so they are
                # written even though nothing reads them yet.
                "segments": [
                    {"start": s.start, "end": s.end, "text": s.text,
                     "words": list(s.words) if s.words else None}
                    for s in result.segments
                ],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return {"segments": len(segments), "engine": result.engine, "srt": f"{base}.srt"}


def describe(index: int, total: int, source: Path, record: dict[str, Any]) -> str:
    if not record.get("ok"):
        return f"[{index}/{total}] {source.name}: FAILED — {record.get('error')}"
    threshold = f"{record['noise_db']:g}dB"
    if record.get("threshold_adapted"):
        threshold += " (measured for this file)"
    line = (f"[{index}/{total}] {source.name}\n"
            f"    threshold {threshold}, removed {record['removed_percent']}% "
            f"in {record['cut_count']} places")
    if record.get("separation_db") is not None:
        line += f", separation {record['separation_db']}dB"
    if record.get("output_mb") is not None:
        line += f"\n    {record['source_mb']}MB -> {record['output_mb']}MB"
        line += " (verified)" if record.get("verified") else f" (CHECKS FAILED: {record['failed_checks']})"
    if record.get("transcript"):
        line += f"\n    transcript: {record['transcript']['segments']} segments"
    # Reported loudly rather than folded into "ok": this is what a threshold that does
    # not suit the material looks like, and the ad-hoc run hid four of them.
    if record["removed_percent"] == 0.0:
        line += "\n    NOTHING WAS CUT — the threshold does not reach this material's gaps"
    for warning in record.get("warnings", []):
        line += f"\n    warning: {warning}"
    return line


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    media, skipped = collect_media(args.paths, force=args.force)
    for path, why in skipped:
        print(f"skipped {path.name}: {why}", flush=True)
    if not media:
        print("nothing to process")
        return 1

    print(f"{len(media)} file(s) to process"
          f"{' (dry run)' if args.dry_run else ''}", flush=True)
    records: list[dict[str, Any]] = []
    for index, source in enumerate(media, start=1):
        try:
            record = process(source, args)
        except (SilenceCutError, OSError, ValueError, RuntimeError) as exc:
            # One bad file must not end the batch; the rest are independent of it.
            record = {"source": str(source), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            traceback.print_exc(limit=2)
        records.append(record)
        print(describe(index, len(media), source, record), flush=True)

    done = [r for r in records if r.get("ok")]
    empty = [r for r in done if r.get("removed_percent") == 0.0]
    failed = [r for r in records if not r.get("ok")]
    print(f"\n{len(done)}/{len(records)} processed"
          + (f", {len(empty)} removed nothing" if empty else "")
          + (f", {len(failed)} failed" if failed else ""), flush=True)
    if empty:
        print("The files that removed nothing are the ones to look at: their gaps are "
              "not quiet enough for any threshold this measured. An audio 'enhancement' "
              "pass does that — try the original recording.", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
