#!/usr/bin/env python3
"""Compare STT candidate quality, speed, and optional cost summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _candidate_label(run: dict[str, Any], path: Path) -> str:
    for key in ("candidate_name", "provider", "model"):
        value = run.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return path.parent.name or path.stem


def load_candidate(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}

    source_duration = _number(run.get("source_duration_seconds"))
    elapsed = _number(run.get("stt_elapsed_seconds"))
    realtime_factor = _number(run.get("realtime_factor"))
    if realtime_factor is None and source_duration and elapsed is not None:
        realtime_factor = elapsed / source_duration
    realtime_speed = 1 / realtime_factor if realtime_factor and realtime_factor > 0 else None

    estimated_cost = _number(run.get("estimated_cost_usd"))
    cost_per_hour = _number(run.get("cost_per_audio_hour_usd"))
    if cost_per_hour is None and estimated_cost is not None and source_duration:
        cost_per_hour = estimated_cost / (source_duration / 3600)

    return {
        "summary_path": str(path),
        "label": _candidate_label(run, path),
        "provider": run.get("provider") or "unknown",
        "model": run.get("model") or "unknown",
        "engine_version": run.get("engine_version") or "",
        "hypothesis": payload.get("hypothesis") or "",
        "char_accuracy": _number(metrics.get("char_accuracy")),
        "cer": _number(metrics.get("cer")),
        "segment_exact_rate": _number(metrics.get("segment_exact_rate")),
        "glossary_recall": _number(metrics.get("glossary_recall")),
        "active_confusion_count": metrics.get("active_confusion_count"),
        "source_duration_seconds": source_duration,
        "stt_elapsed_seconds": elapsed,
        "realtime_factor": _round(realtime_factor),
        "realtime_speed": _round(realtime_speed, 3),
        "billable_seconds": _number(run.get("billable_seconds")),
        "estimated_cost_usd": estimated_cost,
        "cost_per_audio_hour_usd": _round(cost_per_hour),
        "notes": run.get("notes") or "",
    }


def _best_by(candidates: list[dict[str, Any]], key: str, *, higher_is_better: bool) -> dict[str, Any] | None:
    rows = [row for row in candidates if isinstance(row.get(key), (int, float))]
    if not rows:
        return None
    return max(rows, key=lambda row: row[key]) if higher_is_better else min(rows, key=lambda row: row[key])


def summarize(paths: list[Path]) -> dict[str, Any]:
    candidates = [load_candidate(path) for path in paths]
    candidates.sort(
        key=lambda row: (
            -(row.get("char_accuracy") or -1),
            -(row.get("segment_exact_rate") or -1),
            row.get("stt_elapsed_seconds") if isinstance(row.get("stt_elapsed_seconds"), (int, float)) else float("inf"),
        )
    )
    best_quality = _best_by(candidates, "char_accuracy", higher_is_better=True)
    best_segment_rate = _best_by(candidates, "segment_exact_rate", higher_is_better=True)
    fastest = _best_by(candidates, "stt_elapsed_seconds", higher_is_better=False)
    cheapest = _best_by(candidates, "estimated_cost_usd", higher_is_better=False)
    return {
        "candidate_count": len(candidates),
        "candidates": candidates,
        "best_quality_label": best_quality.get("label") if best_quality else None,
        "best_segment_exact_label": best_segment_rate.get("label") if best_segment_rate else None,
        "fastest_label": fastest.get("label") if fastest else None,
        "cheapest_label": cheapest.get("label") if cheapest else None,
    }


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "n/a"
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def write_markdown(summary: dict[str, Any], output: Path | None) -> None:
    lines = [
        "# FluentFlow STT Quality Comparison",
        "",
        f"- Candidates: {summary['candidate_count']}",
        f"- Best character accuracy: {summary['best_quality_label'] or 'n/a'}",
        f"- Best segment exact rate: {summary['best_segment_exact_label'] or 'n/a'}",
        f"- Fastest measured STT: {summary['fastest_label'] or 'n/a'}",
        f"- Lowest entered cost: {summary['cheapest_label'] or 'n/a'}",
        "",
        "| Candidate | Provider | Model | Char accuracy | CER | Segment exact | Glossary recall | Confusions | STT sec | Speed | Cost USD | Cost / audio hour | Notes |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary["candidates"]:
        lines.append(
            f"| {row['label']} | {row['provider']} | {row['model']} | "
            f"{_fmt(row['char_accuracy'])} | {_fmt(row['cer'])} | {_fmt(row['segment_exact_rate'])} | "
            f"{_fmt(row['glossary_recall'])} | {_fmt(row['active_confusion_count'])} | "
            f"{_fmt(row['stt_elapsed_seconds'])} | {_fmt(row['realtime_speed'], 'x')} | "
            f"{_fmt(row['estimated_cost_usd'])} | {_fmt(row['cost_per_audio_hour_usd'])} | "
            f"{row['notes']} |"
        )
    text = "\n".join(lines) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+", type=Path, help="summary.json files produced by scripts/evaluate_stt.py")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    summary = summarize([path.expanduser().resolve() for path in args.summaries])
    if args.format == "json":
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    else:
        write_markdown(summary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
