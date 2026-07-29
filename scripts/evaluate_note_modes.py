#!/usr/bin/env python3
"""Offline note-mode quality evaluation harness.

This is the "ruler" for FluentFlow note quality. It answers one question with
evidence instead of intuition: for the same transcript, does one note mode
(e.g. ``chapter_coverage``) actually miss fewer important points than another
(e.g. ``high_fidelity``)?

Two model-driven roles (semantic judgment must NOT be faked with heuristics,
per project rules):

1. Evidence Builder — reads ONLY the raw transcript (blind to every candidate
   note) and extracts a reasonably complete list of key points a good note must
   cover, each with an importance score (1-5). This is a temporary gold set.
2. Judge — for each candidate note, checks every gold point (covered / partial /
   missed), flags likely hallucinations, and scores six rubric dimensions. The
   judge is blind to which mode produced the note, to avoid mode bias.

The model-produced gold set and judge scores are a reference, not absolute
truth. Boundary samples still need human calibration
(see ``docs/note_mode_evaluation_plan.md``).

This harness is offline only. It never touches the production task queue.

Example
-------
    ./venv/bin/python scripts/evaluate_note_modes.py \
        --sample-dir "backend/data/eval/note_mode_comparison_口语-3" \
        --output "reports/note_mode_eval/口语-3"

By default it auto-detects the transcript (``transcript.json``) and every
``<mode>.md`` candidate note in the sample directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.ai_summarizer import (  # noqa: E402
    _chat,
    _get_client,
    _normalize_provider,
    _provider_default_model,
)

# Importance >= this counts as an "important" point for coverage-of-important.
IMPORTANT_THRESHOLD = 4
# Evidence extraction chunk size, aligned with the product's own evidence path
# (evidence_chunk_chars = 8000 in ai_summarizer) so the gold set is built at the
# same granularity the product reasons at.
GOLD_CHUNK_CHARS = 8000
RUBRIC_DIMENSIONS = (
    "coverage",
    "faithfulness",
    "specificity",
    "structure",
    "redundancy",
    "readability",
)

_EVIDENCE_BUILDER_SYSTEM = """你是 FluentFlow 的「评测关键点提取器」。

你的唯一任务：只根据给定的课程/讲座/口播「原始转录文本」，尽量完整地列出「一份合格笔记必须覆盖的关键点」。

严格要求：
- 你现在不是在写笔记，也看不到任何候选笔记。不要写导语、不要写总结、不要写章节正文。
- 只提取转录里真实出现的信息。不要补充背景、不要发挥、不要编造。
- 每个关键点要「原子化」：一条只讲一个概念、一个观点、一个方法步骤、一个例子、一个数字/条件、或一个明确结论。
- 覆盖各种类型：concept(概念/定义)、argument(观点/结论)、method(方法/步骤/框架)、example(例子/案例)、metric(数字/条件/限制)、action(建议/行动项)、detail(容易被漏但有价值的细节)。
- importance 用 1-5 打分：5=缺失会明显影响笔记完整性；4=重要章节内容；3=有价值但可压缩；2=背景补充；1=低价值。
- 宁可多列细节，也不要漏掉讲者明显强调或反复出现的点。

只输出严格 JSON，不要任何解释或 Markdown 代码围栏：
{"points": [{"type": "concept", "importance": 5, "text": "关键点内容", "source_hint": "可选，如 约第X分钟或原文关键词"}]}
"""

_JUDGE_SYSTEM = """你是 FluentFlow 的「笔记评审员」。你会拿到：
1. 一份「关键点清单」(gold)，来自原始转录，代表笔记应覆盖的重要信息。
2. 一份「候选笔记」(Markdown)。

你不知道这份笔记是用哪种模式生成的，也不要去猜。只客观评估它对关键点的覆盖和整体质量。

判定规则：
- 对每个 gold 关键点判断候选笔记是否覆盖：
  - covered：笔记清楚表达了该点的核心信息。
  - partial：提到但明显丢了关键细节(例子/数字/条件/步骤)。
  - missed：完全没有体现。
- hallucinations：列出笔记中「无法从关键点清单推断、疑似编造或与原意冲突」的具体说法(没有则空数组)。
- 六个维度打 1-5 分(5 最好)：
  - coverage：重要关键点是否被覆盖。
  - faithfulness：是否忠实、无幻觉。
  - specificity：是否保留例子、数字、步骤、限制条件等具体信息。
  - structure：章节结构是否自然清晰。
  - redundancy：是否啰嗦重复(越不啰嗦分越高)。
  - readability：是否适合复习和归档。

只输出严格 JSON，不要任何解释或 Markdown 代码围栏：
{"covered_ids": ["P001"], "partial_ids": ["P002"], "missed_ids": ["P003"],
 "hallucinations": ["具体说法"],
 "scores": {"coverage": 4, "faithfulness": 5, "specificity": 3, "structure": 4, "redundancy": 4, "readability": 4},
 "notes": "一句话说明主要问题"}
"""


@dataclass
class ModelConfig:
    provider: str
    model: str
    temperature_evidence: float = 0.1
    temperature_judge: float = 0.0
    max_retries: int = 2


@dataclass
class EvalStats:
    calls: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def _chunk_text(text: str, max_chars: int) -> list[str]:
    """Mechanical char-window split, preferring a newline break in the back half."""
    text = text or ""
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            window = text[start:end]
            newline = window.rfind("\n", max_chars // 2)
            if newline != -1:
                end = start + newline + 1
        piece = text[start:end]
        if piece.strip():
            chunks.append(piece)
        start = end
    return chunks


def _extract_json(raw: str) -> Any:
    """Best-effort JSON extraction from a model reply (handles ``` fences / preamble)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} or [...] block.
    for opener, closer in (("{", "}"), ("[", "]")):
        first = text.find(opener)
        last = text.rfind(closer)
        if first != -1 and last != -1 and last > first:
            candidate = text[first : last + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError("model did not return parseable JSON")


def _call_json(client, config: ModelConfig, system: str, user: str, *, temperature: float, stats: EvalStats) -> Any:
    last_error: Exception | None = None
    for attempt in range(config.max_retries + 1):
        started = time.monotonic()
        try:
            reply = _chat(client, config.model, system, user, temperature=temperature)
            stats.calls += 1
            stats.elapsed_seconds += time.monotonic() - started
            return _extract_json(reply)
        except Exception as exc:  # noqa: BLE001 - offline tool, surface and retry
            stats.elapsed_seconds += time.monotonic() - started
            last_error = exc
            if attempt < config.max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
    message = f"call failed after {config.max_retries + 1} attempts: {last_error}"
    stats.errors.append(message)
    raise RuntimeError(message)


def build_gold_points(client, config: ModelConfig, transcript: str, stats: EvalStats) -> list[dict[str, Any]]:
    """Evidence Builder: extract an importance-weighted gold key-point set."""
    chunks = _chunk_text(transcript, GOLD_CHUNK_CHARS)
    points: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        user = (
            f"这是整段转录的第 {index}/{len(chunks)} 部分，请只针对本段提取关键点。\n\n"
            f"---\n{chunk}\n---"
        )
        data = _call_json(client, config, _EVIDENCE_BUILDER_SYSTEM, user, temperature=config.temperature_evidence, stats=stats)
        raw_points = data.get("points") if isinstance(data, dict) else data
        if not isinstance(raw_points, list):
            continue
        for item in raw_points:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                importance = int(item.get("importance") or 3)
            except (TypeError, ValueError):
                importance = 3
            importance = max(1, min(5, importance))
            points.append({
                "type": str(item.get("type") or "detail").strip() or "detail",
                "importance": importance,
                "text": text,
                "source_hint": str(item.get("source_hint") or "").strip(),
                "chunk": index,
            })
    for order, point in enumerate(points, start=1):
        point["id"] = f"P{order:03d}"
    return points


def judge_candidate(client, config: ModelConfig, gold: list[dict[str, Any]], note_markdown: str, stats: EvalStats) -> dict[str, Any]:
    """Judge: score one candidate note against the gold key-point set (mode-blind)."""
    gold_view = [
        {"id": point["id"], "importance": point["importance"], "type": point["type"], "text": point["text"]}
        for point in gold
    ]
    user = (
        "关键点清单 (gold)：\n"
        f"{json.dumps(gold_view, ensure_ascii=False, indent=1)}\n\n"
        "候选笔记 (Markdown)：\n"
        f"---\n{note_markdown}\n---"
    )
    data = _call_json(client, config, _JUDGE_SYSTEM, user, temperature=config.temperature_judge, stats=stats)
    if not isinstance(data, dict):
        raise ValueError("judge did not return a JSON object")
    return data


def _score_candidate(gold: list[dict[str, Any]], judgment: dict[str, Any]) -> dict[str, Any]:
    valid_ids = {point["id"] for point in gold}
    important_ids = {point["id"] for point in gold if point["importance"] >= IMPORTANT_THRESHOLD}

    def _clean(key: str) -> set[str]:
        raw = judgment.get(key)
        if not isinstance(raw, list):
            return set()
        return {str(x).strip() for x in raw if str(x).strip() in valid_ids}

    covered = _clean("covered_ids")
    partial = _clean("partial_ids")
    missed = valid_ids - covered - partial  # anything not marked covered/partial counts as missed

    missed_important = sorted(important_ids & missed)
    partial_important = sorted(important_ids & partial)
    covered_important = important_ids & covered

    total = len(valid_ids)
    total_important = len(important_ids)
    scores_raw = judgment.get("scores") if isinstance(judgment.get("scores"), dict) else {}
    scores = {}
    for dim in RUBRIC_DIMENSIONS:
        try:
            scores[dim] = round(float(scores_raw.get(dim)), 2) if scores_raw.get(dim) is not None else None
        except (TypeError, ValueError):
            scores[dim] = None

    hallucinations = [str(x).strip() for x in judgment.get("hallucinations", []) if str(x).strip()] \
        if isinstance(judgment.get("hallucinations"), list) else []

    return {
        "total_points": total,
        "important_points": total_important,
        # Coverage: partial counts as half credit so it is not indistinguishable from full.
        "covered_points": len(covered),
        "partial_points": len(partial),
        "missed_points": len(missed),
        "coverage_rate": round((len(covered) + 0.5 * len(partial)) / total, 4) if total else None,
        "covered_important_points": len(covered_important),
        "partial_important_points": len(partial_important),
        "missed_important_points": missed_important,
        "important_coverage_rate": round(
            (len(covered_important) + 0.5 * len(partial_important)) / total_important, 4
        ) if total_important else None,
        "hallucination_count": len(hallucinations),
        "hallucinations": hallucinations,
        "scores": scores,
        "notes": str(judgment.get("notes") or "").strip(),
    }


def _load_transcript(sample_dir: Path, explicit: Path | None) -> str:
    path = explicit or (sample_dir / "transcript.json")
    if not path.exists():
        raise FileNotFoundError(f"transcript not found: {path}")
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            text = payload.get("text") or payload.get("transcript_text") or ""
            if not text and isinstance(payload.get("segments"), list):
                text = "\n".join(str(seg.get("text") or "").strip() for seg in payload["segments"])
            return str(text)
        raise ValueError(f"unexpected transcript JSON shape: {path}")
    return path.read_text(encoding="utf-8")


def _discover_candidates(sample_dir: Path, explicit: list[str]) -> dict[str, Path]:
    candidates: dict[str, Path] = {}
    if explicit:
        for item in explicit:
            if "=" not in item:
                raise ValueError(f"--candidate must be mode=path, got: {item}")
            mode, rel = item.split("=", 1)
            path = Path(rel)
            if not path.is_absolute():
                path = sample_dir / rel
            candidates[mode.strip()] = path
        return candidates
    for md in sorted(sample_dir.glob("*.md")):
        candidates[md.stem] = md
    return candidates


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Note Mode Evaluation (model-judged)",
        "",
        f"- Sample: `{report['sample_id']}`",
        f"- Provider / model: {report['model']['provider']} / {report['model']['model']}",
        f"- Transcript chars: {report['transcript_chars']}",
        f"- Gold points: {report['gold']['total_points']} (important >= {IMPORTANT_THRESHOLD}: {report['gold']['important_points']})",
        f"- Model calls: {report['stats']['calls']}, elapsed: {round(report['stats']['elapsed_seconds'], 1)}s",
        "",
        "> Model-produced gold set and scores are a reference, not absolute truth.",
        "> Inspect boundary samples manually before changing production defaults.",
        "",
        "## Mode comparison",
        "",
        "| Mode | Coverage | Important coverage | Missed important | Hallucinations | coverage | faithful | specific | structure | redundancy | readability |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, res in report["candidates"].items():
        s = res["scores"]
        lines.append(
            f"| {mode} | {res['coverage_rate']} | {res['important_coverage_rate']} | "
            f"{len(res['missed_important_points'])} | {res['hallucination_count']} | "
            f"{s.get('coverage')} | {s.get('faithfulness')} | {s.get('specificity')} | "
            f"{s.get('structure')} | {s.get('redundancy')} | {s.get('readability')} |"
        )
    lines.extend(["", "## Missed important points by mode", ""])
    gold_by_id = {p["id"]: p for p in report["gold"]["points"]}
    for mode, res in report["candidates"].items():
        lines.append(f"### {mode}")
        lines.append("")
        if not res["missed_important_points"]:
            lines.append("- (none)")
        for pid in res["missed_important_points"]:
            point = gold_by_id.get(pid, {})
            lines.append(f"- `{pid}` (imp {point.get('importance')}, {point.get('type')}): {point.get('text')}")
        if res["hallucinations"]:
            lines.append("")
            lines.append("**Flagged possible hallucinations:**")
            for item in res["hallucinations"]:
                lines.append(f"- {item}")
        if res.get("notes"):
            lines.append("")
            lines.append(f"_Judge note: {res['notes']}_")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample-dir", type=Path, required=True, help="Directory containing transcript.json and <mode>.md candidates")
    parser.add_argument("--transcript", type=Path, default=None, help="Explicit transcript path (json with text/segments, or plain text)")
    parser.add_argument("--candidate", action="append", default=[], help="Explicit candidate as mode=path (repeatable). Default: every *.md in sample dir")
    parser.add_argument("--output", type=Path, default=None, help="Output dir (default: reports/note_mode_eval/<sample_dir name>)")
    parser.add_argument("--provider", default=None, help="AI provider (default: env AI_PROVIDER or deepseek)")
    parser.add_argument("--model", default=None, help="Model id (default: provider default)")
    parser.add_argument("--reuse-gold", action="store_true", help="Reuse an existing gold_points.json in the output dir if present")
    parser.add_argument("--dry-run", action="store_true", help="Do not call the model; only report discovered inputs")
    args = parser.parse_args()

    sample_dir = args.sample_dir.expanduser().resolve()
    if not sample_dir.is_dir():
        print(f"sample dir not found: {sample_dir}", file=sys.stderr)
        return 2
    sample_id = sample_dir.name
    output_dir = (args.output or Path("reports/note_mode_eval") / sample_id).expanduser().resolve()

    transcript = _load_transcript(sample_dir, args.transcript.expanduser().resolve() if args.transcript else None)
    candidates = _discover_candidates(sample_dir, args.candidate)
    if not candidates:
        print(f"no candidate .md notes found in {sample_dir}", file=sys.stderr)
        return 2

    print(json.dumps({
        "sample_id": sample_id,
        "transcript_chars": len(transcript),
        "candidates": {mode: str(path) for mode, path in candidates.items()},
        "output_dir": str(output_dir),
    }, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("dry-run: no model calls made")
        return 0

    provider = _normalize_provider(args.provider)
    model = (args.model or _provider_default_model(provider)).strip()
    config = ModelConfig(provider=provider, model=model)
    client = _get_client(provider=provider)
    stats = EvalStats()

    output_dir.mkdir(parents=True, exist_ok=True)
    gold_path = output_dir / "gold_points.json"
    if args.reuse_gold and gold_path.exists():
        gold = json.loads(gold_path.read_text(encoding="utf-8")).get("points", [])
        print(f"reused gold set: {len(gold)} points")
    else:
        print("building gold key-point set (Evidence Builder)...")
        gold = build_gold_points(client, config, transcript, stats)
        gold_path.write_text(json.dumps({
            "sample_id": sample_id,
            "important_threshold": IMPORTANT_THRESHOLD,
            "total_points": len(gold),
            "important_points": sum(1 for p in gold if p["importance"] >= IMPORTANT_THRESHOLD),
            "points": gold,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"gold set: {len(gold)} points -> {gold_path}")

    candidate_results: dict[str, Any] = {}
    judge_raw: dict[str, Any] = {}
    for mode, path in candidates.items():
        if not path.exists():
            print(f"skip {mode}: file not found {path}", file=sys.stderr)
            continue
        print(f"judging candidate: {mode} ...")
        note_markdown = path.read_text(encoding="utf-8")
        judgment = judge_candidate(client, config, gold, note_markdown, stats)
        judge_raw[mode] = judgment
        candidate_results[mode] = _score_candidate(gold, judgment)

    report = {
        "sample_id": sample_id,
        "model": {"provider": provider, "model": model},
        "transcript_chars": len(transcript),
        "gold": {
            "total_points": len(gold),
            "important_points": sum(1 for p in gold if p["importance"] >= IMPORTANT_THRESHOLD),
            "points": gold,
        },
        "candidates": candidate_results,
        "stats": {"calls": stats.calls, "elapsed_seconds": round(stats.elapsed_seconds, 2), "errors": stats.errors},
    }

    (output_dir / "judge_scores.json").write_text(json.dumps({
        "report": {k: v for k, v in report.items() if k != "gold"} | {"gold_summary": {
            "total_points": report["gold"]["total_points"],
            "important_points": report["gold"]["important_points"],
        }},
        "judge_raw": judge_raw,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "comparison.md").write_text(_render_markdown(report), encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "output_dir": str(output_dir),
        "gold_points": report["gold"]["total_points"],
        "important_points": report["gold"]["important_points"],
        "modes": {
            mode: {
                "coverage_rate": res["coverage_rate"],
                "important_coverage_rate": res["important_coverage_rate"],
                "missed_important": len(res["missed_important_points"]),
                "hallucinations": res["hallucination_count"],
            }
            for mode, res in candidate_results.items()
        },
        "model_calls": stats.calls,
        "errors": stats.errors,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
