#!/usr/bin/env python3
"""Summarize FluentFlow STT performance events from SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.runtime_paths import default_event_db_path  # noqa: E402

DEFAULT_DB_PATH = default_event_db_path()


def _average_numeric(rows: list[dict[str, Any]], metadata_key: str) -> float | None:
    values = [
        (row.get("metadata") or {}).get(metadata_key)
        for row in rows
    ]
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 3)


def _average_first_numeric(rows: list[dict[str, Any]], metadata_keys: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        for key in metadata_keys:
            value = metadata.get(key)
            if isinstance(value, (int, float)):
                values.append(float(value))
                break
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _detected_languages(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        value = (row.get("metadata") or {}).get("detected_language")
        if isinstance(value, str) and value.strip():
            counts[value.strip()] += 1
    return dict(sorted(counts.items()))


def _format_detected_languages(value: dict[str, int]) -> str:
    if not value:
        return "unknown"
    return ", ".join(f"{lang}:{count}" for lang, count in value.items())


def _event_provider(event: dict[str, Any]) -> str:
    return str((event.get("metadata") or {}).get("stt_provider") or "local")


def _event_factor(event: dict[str, Any]) -> float | None:
    factor = event.get("stt_realtime_factor")
    if isinstance(factor, (int, float)):
        return float(factor)
    return None


def _best_event_by_provider(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = _event_provider(row)
        current = best.get(provider)
        row_elapsed = row.get("duration_seconds")
        current_elapsed = current.get("duration_seconds") if current else None
        if current is None or (
            isinstance(row_elapsed, (int, float))
            and (not isinstance(current_elapsed, (int, float)) or row_elapsed < current_elapsed)
        ):
            best[provider] = row
    return best


def _same_source_comparisons(same_source_runs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for fingerprint, rows in sorted(same_source_runs.items()):
        by_provider = _best_event_by_provider(rows)
        if len(by_provider) < 2:
            continue
        provider_rows = []
        for provider, row in sorted(by_provider.items()):
            factor = _event_factor(row)
            provider_rows.append({
                "stt_provider": provider,
                "task_id": row.get("task_id"),
                "source_filename": row.get("source_filename"),
                "source_duration_seconds": row.get("source_duration_seconds"),
                "stt_elapsed_seconds": row.get("duration_seconds"),
                "stt_realtime_factor": round(factor, 4) if factor is not None else None,
                "detected_language": (row.get("metadata") or {}).get("detected_language"),
                "stt_model": (row.get("metadata") or {}).get("stt_model"),
            })
        elapsed_values = [
            row["stt_elapsed_seconds"]
            for row in provider_rows
            if isinstance(row.get("stt_elapsed_seconds"), (int, float)) and row["stt_elapsed_seconds"] > 0
        ]
        fastest = min(provider_rows, key=lambda row: row["stt_elapsed_seconds"] if isinstance(row.get("stt_elapsed_seconds"), (int, float)) else float("inf"))
        slowest = max(provider_rows, key=lambda row: row["stt_elapsed_seconds"] if isinstance(row.get("stt_elapsed_seconds"), (int, float)) else 0)
        comparisons.append({
            "source_fingerprint_sha256": fingerprint,
            "source_filename": provider_rows[0].get("source_filename"),
            "source_duration_seconds": provider_rows[0].get("source_duration_seconds"),
            "providers": provider_rows,
            "fastest_provider": fastest.get("stt_provider") if elapsed_values else None,
            "slowest_provider": slowest.get("stt_provider") if elapsed_values else None,
            "fastest_vs_slowest_speedup": (
                round(slowest["stt_elapsed_seconds"] / fastest["stt_elapsed_seconds"], 3)
                if elapsed_values and isinstance(fastest.get("stt_elapsed_seconds"), (int, float)) and fastest["stt_elapsed_seconds"] > 0
                else None
            ),
        })
    return comparisons


def load_stt_events(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT created_at, task_id, source_filename, source_duration_seconds,
                   source_file_size_mb, duration_seconds, transcript_length,
                   success, metadata
            FROM events
            WHERE event_name = 'stt_completed'
            ORDER BY created_at
            """
        ).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        meta = item["metadata"]
        factor = meta.get("stt_realtime_factor")
        if factor is None and item.get("source_duration_seconds"):
            factor = item["duration_seconds"] / item["source_duration_seconds"]
        item["stt_realtime_factor"] = factor
        if not meta.get("stt_provider"):
            meta["stt_provider"] = "local"
            meta["stt_provider_inferred"] = True
        events.append(item)
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    factors = [
        e["stt_realtime_factor"]
        for e in events
        if isinstance(e.get("stt_realtime_factor"), (int, float))
    ]
    duration_sum = sum(e.get("source_duration_seconds") or 0 for e in events)
    stt_sum = sum(e.get("duration_seconds") or 0 for e in events)

    def group_key(event: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
        meta = event.get("metadata") or {}
        return (
            str(meta.get("stt_provider") or "local"),
            str(meta.get("runtime_os") or "unknown"),
            str(meta.get("runtime_machine") or "unknown"),
            str(meta.get("stt_model") or "unknown"),
            str(meta.get("stt_speed") or "unknown"),
            str(meta.get("stt_language") or "unknown"),
        )

    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[group_key(event)].append(event)

    provider_totals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        meta = event.get("metadata") or {}
        provider_totals[str(meta.get("stt_provider") or "local")].append(event)

    provider_rows = []
    for provider, rows in sorted(provider_totals.items()):
        row_duration_sum = sum(r.get("source_duration_seconds") or 0 for r in rows)
        row_stt_sum = sum(r.get("duration_seconds") or 0 for r in rows)
        row_factor = row_stt_sum / row_duration_sum if row_duration_sum else None
        inferred_count = sum(1 for r in rows if (r.get("metadata") or {}).get("stt_provider_inferred"))
        provider_rows.append({
            "stt_provider": provider,
            "sample_count": len(rows),
            "inferred_provider_count": inferred_count,
            "detected_languages": _detected_languages(rows),
            "avg_cloud_upload_audio_size_mb": _average_first_numeric(rows, ("elevenlabs_audio_size_mb",)),
            "avg_cloud_upload_duration_seconds": _average_first_numeric(rows, ("elevenlabs_duration_seconds",)),
            "source_duration_seconds": round(row_duration_sum, 3),
            "stt_elapsed_seconds": round(row_stt_sum, 3),
            "weighted_realtime_factor": round(row_factor, 4) if row_factor else None,
            "weighted_realtime_speed": round(1 / row_factor, 3) if row_factor else None,
        })

    group_rows = []
    for key, rows in sorted(grouped.items()):
        row_duration_sum = sum(r.get("source_duration_seconds") or 0 for r in rows)
        row_stt_sum = sum(r.get("duration_seconds") or 0 for r in rows)
        row_factor = row_stt_sum / row_duration_sum if row_duration_sum else None
        group_rows.append({
            "stt_provider": key[0],
            "runtime_os": key[1],
            "runtime_machine": key[2],
            "stt_model": key[3],
            "stt_speed": key[4],
            "stt_language": key[5],
            "sample_count": len(rows),
            "detected_languages": _detected_languages(rows),
            "avg_cloud_upload_audio_size_mb": _average_first_numeric(rows, ("elevenlabs_audio_size_mb",)),
            "avg_cloud_upload_duration_seconds": _average_first_numeric(rows, ("elevenlabs_duration_seconds",)),
            "source_duration_seconds": round(row_duration_sum, 3),
            "stt_elapsed_seconds": round(row_stt_sum, 3),
            "weighted_realtime_factor": round(row_factor, 4) if row_factor else None,
            "weighted_realtime_speed": round(1 / row_factor, 3) if row_factor else None,
        })

    same_source_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        fp = ((event.get("metadata") or {}).get("source_fingerprint") or {}).get("sha256")
        if fp:
            same_source_runs[fp].append(event)
    comparisons = _same_source_comparisons(same_source_runs)

    return {
        "event_count": len(events),
        "source_duration_seconds": round(duration_sum, 3),
        "stt_elapsed_seconds": round(stt_sum, 3),
        "weighted_realtime_factor": round(stt_sum / duration_sum, 4) if duration_sum else None,
        "weighted_realtime_speed": round(duration_sum / stt_sum, 3) if stt_sum else None,
        "min_realtime_factor": round(min(factors), 4) if factors else None,
        "max_realtime_factor": round(max(factors), 4) if factors else None,
        "providers": provider_rows,
        "groups": group_rows,
        "same_source_comparison_count": len(comparisons),
        "same_source_comparisons": comparisons,
    }


def write_markdown(summary: dict[str, Any], output: Path | None) -> None:
    speed = summary["weighted_realtime_speed"]
    lines = [
        "# FluentFlow STT Performance Report",
        "",
        f"- STT samples: {summary['event_count']}",
        f"- Total source duration: {summary['source_duration_seconds']} sec",
        f"- Total STT elapsed: {summary['stt_elapsed_seconds']} sec",
        f"- Weighted realtime factor: {summary['weighted_realtime_factor']}",
        f"- Weighted realtime speed: {speed}x" if speed is not None else "- Weighted realtime speed: unavailable",
        f"- Same-source comparison groups: {summary['same_source_comparison_count']}",
        "",
        "## Providers",
        "",
        "| Provider | Samples | Inferred provider | Detected languages | Avg cloud upload MB | Avg cloud upload sec | Duration sec | STT sec | Factor | Speed |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["providers"]:
        lines.append(
            f"| {row['stt_provider']} | {row['sample_count']} | {row['inferred_provider_count']} | "
            f"{_format_detected_languages(row['detected_languages'])} | "
            f"{row['avg_cloud_upload_audio_size_mb'] if row['avg_cloud_upload_audio_size_mb'] is not None else 'n/a'} | "
            f"{row['avg_cloud_upload_duration_seconds'] if row['avg_cloud_upload_duration_seconds'] is not None else 'n/a'} | "
            f"{row['source_duration_seconds']} | {row['stt_elapsed_seconds']} | "
            f"{row['weighted_realtime_factor']} | "
            f"{str(row['weighted_realtime_speed']) + 'x' if row['weighted_realtime_speed'] is not None else 'unavailable'} |"
        )
    lines.extend([
        "",
        "## Groups",
        "",
        "| Provider | Runtime | Model | Speed | Requested language | Detected languages | Samples | Avg cloud upload MB | Duration sec | STT sec | Factor | Speed |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in summary["groups"]:
        runtime = f"{row['runtime_os']} / {row['runtime_machine']}"
        lines.append(
            f"| {row['stt_provider']} | {runtime} | {row['stt_model']} | {row['stt_speed']} | {row['stt_language']} | "
            f"{_format_detected_languages(row['detected_languages'])} | "
            f"{row['sample_count']} | "
            f"{row['avg_cloud_upload_audio_size_mb'] if row['avg_cloud_upload_audio_size_mb'] is not None else 'n/a'} | "
            f"{row['source_duration_seconds']} | {row['stt_elapsed_seconds']} | "
            f"{row['weighted_realtime_factor']} | "
            f"{str(row['weighted_realtime_speed']) + 'x' if row['weighted_realtime_speed'] is not None else 'unavailable'} |"
        )
    lines.extend([
        "",
        "## Same-Source Comparisons",
        "",
    ])
    if summary["same_source_comparisons"]:
        lines.extend([
            "| Source | Duration sec | Fastest | Slowest | Speedup | Provider details |",
            "| --- | ---: | --- | --- | ---: | --- |",
        ])
        for item in summary["same_source_comparisons"]:
            details = "; ".join(
                f"{row['stt_provider']} {row['stt_elapsed_seconds']}s factor={row['stt_realtime_factor']}"
                for row in item["providers"]
            )
            lines.append(
                f"| {item.get('source_filename') or item['source_fingerprint_sha256'][:12]} | "
                f"{item.get('source_duration_seconds')} | "
                f"{item.get('fastest_provider') or 'n/a'} | "
                f"{item.get('slowest_provider') or 'n/a'} | "
                f"{item.get('fastest_vs_slowest_speedup') if item.get('fastest_vs_slowest_speedup') is not None else 'n/a'} | "
                f"{details} |"
            )
    else:
        lines.append("No same-source cross-provider comparisons yet.")
    text = "\n".join(lines) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    summary = summarize(load_stt_events(args.db))
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
