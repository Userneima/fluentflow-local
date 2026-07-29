"""Generate a read-only FluentFlow historical snapshot report.

This script intentionally does not read browser storage directly and does not
modify application data. It summarizes:
- optional exported browser localStorage JSON
- backend uvicorn logs

Outputs:
- reports/fluentflow_history_snapshot.json
- reports/fluentflow_history_snapshot.md
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_LOCALSTORAGE = Path("exports/fluentflow_localstorage.json")
DEFAULT_LOG = Path("logs/uvicorn.log")
DEFAULT_OUT_DIR = Path("reports")

TIERS = ("resume_ready", "needs_context", "internal_only", "unavailable")

BROWSER_EXPORT_SCRIPT = r"""(() => {
  const payload = {
    exported_at: new Date().toISOString(),
    source: "browser_localStorage",
    fluentflow_history: JSON.parse(localStorage.getItem("fluentflow_history") || "[]"),
    fluentflow_lark_exports: JSON.parse(localStorage.getItem("fluentflow_lark_exports") || "[]")
  };

  const json = JSON.stringify(payload, null, 2);
  const filename = "fluentflow_localstorage.json";

  // Try browser download first.
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);

  // Also copy to clipboard when allowed, so you can paste it into a file.
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(json).then(
      () => console.log(`Downloaded and copied ${filename}`),
      () => console.log(`Downloaded ${filename}; clipboard copy was blocked`)
    );
  } else {
    console.log(json);
    console.log(`Downloaded ${filename}; clipboard API unavailable, JSON also printed above.`);
  }
})();"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"File not found: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report parser error, do not crash.
        return None, f"Failed to parse JSON: {exc}"
    if not isinstance(data, dict):
        return None, "Expected a JSON object"
    return data, None


def coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n


def text_len(value: Any) -> int:
    return len(value) if isinstance(value, str) else 0


def stat(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "sum": 0,
            "avg": None,
            "min": None,
            "max": None,
        }
    return {
        "count": len(values),
        "sum": round(sum(values), 3),
        "avg": round(mean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def ratio(numerator: int | float, denominator: int | float) -> dict[str, Any]:
    if not denominator:
        return {"value": "unavailable", "numerator": numerator, "denominator": denominator}
    return {
        "value": round(float(numerator) / float(denominator), 4),
        "numerator": numerator,
        "denominator": denominator,
    }


def metric(
    value: Any,
    *,
    source: str,
    confidence: str,
    resume_usable: bool,
    tier: str,
    explanation: str,
) -> dict[str, Any]:
    if tier not in TIERS:
        raise ValueError(f"Unknown tier: {tier}")
    return {
        "value": value,
        "source": source,
        "confidence": confidence,
        "resume_usable": resume_usable,
        "tier": tier,
        "explanation": explanation,
    }


def unavailable(source: str, reason: str) -> dict[str, Any]:
    return metric(
        "unavailable",
        source=source,
        confidence="low",
        resume_usable=False,
        tier="unavailable",
        explanation=reason,
    )


def extract_localstorage(data: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if data is None:
        return [], []
    history = coerce_list(data.get("fluentflow_history"))
    exports = coerce_list(data.get("fluentflow_lark_exports"))
    return (
        [x for x in history if isinstance(x, dict)],
        [x for x in exports if isinstance(x, dict)],
    )


def summarize_localstorage(
    metrics: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    lark_exports: list[dict[str, Any]],
    *,
    localstorage_path: Path,
    localstorage_error: str | None,
) -> dict[str, Any]:
    source = str(localstorage_path)
    if localstorage_error:
        keys = [
            "history_task_count",
            "completed_task_count",
            "failed_task_count",
            "task_success_rate",
            "processed_file_count",
            "unique_filename_count",
            "total_source_duration_seconds",
            "total_transcript_length_chars",
            "total_markdown_note_length_chars",
            "stt_elapsed_seconds_stats",
            "stt_duration_ratio_stats",
            "localstorage_lark_export_record_count",
            "localstorage_lark_export_url_count",
            "localstorage_lark_export_unique_url_count",
            "history_lark_url_count",
            "history_lark_error_count",
            "history_failure_reasons",
        ]
        for key in keys:
            metrics[key] = unavailable(source, localstorage_error)
        return {"history": [], "lark_exports": []}

    total = len(history)
    completed = [h for h in history if h.get("status") == "completed"]
    failed = [h for h in history if h.get("status") == "failed"]
    source_durations = [n for h in completed if (n := num(h.get("audioDurationSec"))) is not None and n > 0]
    transcript_chars = sum(text_len(h.get("transcriptText")) for h in completed)
    note_chars = sum(text_len(h.get("summary")) for h in completed)
    stt_values = [n for h in completed if (n := num(h.get("sttElapsedSec"))) is not None and n > 0]
    stt_ratios = [
        stt / dur
        for h in completed
        if (stt := num(h.get("sttElapsedSec"))) is not None
        and stt > 0
        and (dur := num(h.get("audioDurationSec"))) is not None
        and dur > 0
    ]
    filenames = [str(h.get("name") or "").strip() for h in history if str(h.get("name") or "").strip()]
    completed_filenames = [
        str(h.get("name") or "").strip()
        for h in completed
        if str(h.get("name") or "").strip()
    ]
    export_urls = [
        str(e.get("url") or "").strip()
        for e in lark_exports
        if str(e.get("url") or "").strip()
    ]
    history_urls = [
        str(h.get("larkUrl") or "").strip()
        for h in history
        if str(h.get("larkUrl") or "").strip()
    ]
    history_errors = [
        str(h.get("larkError") or h.get("error") or "").strip()
        for h in history
        if str(h.get("larkError") or h.get("error") or "").strip()
    ]
    failed_reasons = [
        str(h.get("error") or h.get("larkError") or "failed_without_reason").strip()
        for h in failed
    ]

    metrics["history_task_count"] = metric(
        total,
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="needs_context",
        explanation="Browser localStorage history length. It may be cleared or browser-specific, so it is a historical snapshot, not a durable event log.",
    )
    metrics["completed_task_count"] = metric(
        len(completed),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="Completed entries in browser localStorage history. Usable only with the caveat that it is client-side historical data.",
    )
    metrics["failed_task_count"] = metric(
        len(failed),
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="internal_only",
        explanation="Failed entries in browser localStorage history. Useful for internal reliability review.",
    )
    metrics["task_success_rate"] = metric(
        ratio(len(completed), total),
        source=source,
        confidence="medium" if total else "low",
        resume_usable=bool(total),
        tier="needs_context" if total else "unavailable",
        explanation="completed_task_count / total_history_task_count from browser localStorage. This is not backend-verified.",
    )
    metrics["processed_file_count"] = metric(
        len(completed),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="Count of completed history entries. Treat as processed files only if each completed history item corresponds to one upload.",
    )
    metrics["unique_filename_count"] = metric(
        len(set(completed_filenames or filenames)),
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="internal_only",
        explanation="Unique filenames in localStorage. Filename reuse and retries can undercount or overcount real unique sources.",
    )
    metrics["total_source_duration_seconds"] = metric(
        round(sum(source_durations), 3),
        source=source,
        confidence="medium",
        resume_usable=bool(source_durations),
        tier="needs_context" if source_durations else "unavailable",
        explanation="Sum of audioDurationSec from completed localStorage entries. Missing or zero durations are excluded.",
    )
    metrics["total_transcript_length_chars"] = metric(
        transcript_chars,
        source=source,
        confidence="medium",
        resume_usable=transcript_chars > 0,
        tier="needs_context" if transcript_chars > 0 else "unavailable",
        explanation="Sum of transcriptText character lengths in completed browser history entries.",
    )
    metrics["total_markdown_note_length_chars"] = metric(
        note_chars,
        source=source,
        confidence="medium",
        resume_usable=note_chars > 0,
        tier="needs_context" if note_chars > 0 else "unavailable",
        explanation="Sum of summary Markdown character lengths in completed browser history entries.",
    )
    metrics["stt_elapsed_seconds_stats"] = metric(
        stat(stt_values),
        source=source,
        confidence="medium",
        resume_usable=bool(stt_values),
        tier="needs_context" if stt_values else "unavailable",
        explanation="Statistics over sttElapsedSec values stored in browser history.",
    )
    metrics["stt_duration_ratio_stats"] = metric(
        stat(stt_ratios),
        source=source,
        confidence="medium",
        resume_usable=bool(stt_ratios),
        tier="needs_context" if stt_ratios else "unavailable",
        explanation="Statistics over sttElapsedSec / audioDurationSec for completed tasks with both fields.",
    )
    metrics["localstorage_lark_export_record_count"] = metric(
        len(lark_exports),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="Count of browser localStorage lark export records. This list is capped by the frontend and can be cleared.",
    )
    metrics["localstorage_lark_export_url_count"] = metric(
        len(export_urls),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="Count of non-empty URLs in fluentflow_lark_exports.",
    )
    metrics["localstorage_lark_export_unique_url_count"] = metric(
        len(set(export_urls)),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="Unique non-empty URLs in fluentflow_lark_exports.",
    )
    metrics["history_lark_url_count"] = metric(
        len(history_urls),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="Count of non-empty larkUrl values attached to history entries.",
    )
    metrics["history_lark_error_count"] = metric(
        len(history_errors),
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="internal_only",
        explanation="Count of stored larkError/error strings in browser history.",
    )
    metrics["history_failure_reasons"] = metric(
        failed_reasons,
        source=source,
        confidence="low" if failed_reasons else "low",
        resume_usable=False,
        tier="internal_only" if failed_reasons else "unavailable",
        explanation="Failure reason strings visible in browser history. Current frontend often stores failed status without detailed reason.",
    )

    return {"history": history, "lark_exports": lark_exports}


def count_status(log_text: str, endpoint: str) -> dict[str, int]:
    pattern = re.compile(rf'POST {re.escape(endpoint)} HTTP/1\.1" (?P<status>\d{{3}})')
    counts: dict[str, int] = {}
    for match in pattern.finditer(log_text):
        status = match.group("status")
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = sum(counts.values())
    return counts


def extract_failure_snippets(log_text: str) -> list[str]:
    lines = log_text.splitlines()
    snippets: list[str] = []
    for idx, line in enumerate(lines):
        if "Processing failed" not in line:
            continue
        start = max(0, idx - 1)
        end = min(len(lines), idx + 80)
        for j in range(idx + 1, min(len(lines), idx + 80)):
            if lines[j].startswith("INFO:") or lines[j].startswith("---- "):
                end = j
                break
        snippets.append("\n".join(lines[start:end]))
    return snippets[:10]


def extract_failure_reason_counts(snippets: list[str]) -> dict[str, int]:
    reason_counts: dict[str, int] = {}
    exc_re = re.compile(r"(?P<reason>(?:[\w.]+)?(?:Error|Exception|Timeout)):\s*(?P<detail>.+)")
    for snippet in snippets:
        matches = list(exc_re.finditer(snippet))
        if matches:
            last = matches[-1]
            reason = f"{last.group('reason')}: {last.group('detail')[:180]}"
        else:
            reason = "unknown_processing_failure"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return reason_counts


def summarize_logs(
    metrics: dict[str, dict[str, Any]],
    *,
    log_path: Path,
    log_text: str | None,
) -> dict[str, Any]:
    source = str(log_path)
    if log_text is None:
        keys = [
            "log_process_request_count",
            "log_process_status_counts",
            "log_export_lark_request_count",
            "log_export_lark_success_count",
            "log_export_lark_failure_count",
            "log_export_success_rate",
            "log_regenerate_summary_request_count",
            "log_regenerate_summary_success_count",
            "log_regenerate_summary_failure_count",
            "log_regenerate_success_rate",
            "log_summarize_transcript_file_request_count",
            "log_processing_failed_count",
            "log_failure_snippets",
        ]
        for key in keys:
            metrics[key] = unavailable(source, f"File not found: {log_path}")
        return {}

    process = count_status(log_text, "/process")
    export = count_status(log_text, "/export-lark")
    regen = count_status(log_text, "/regenerate-summary")
    subtitle = count_status(log_text, "/summarize-transcript-file")
    processing_failed_count = len(re.findall(r"Processing failed", log_text))
    snippets = extract_failure_snippets(log_text)
    failure_reason_counts = extract_failure_reason_counts(snippets)

    metrics["log_process_request_count"] = metric(
        process.get("total", 0),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="HTTP request count in uvicorn.log. Because /process streams SSE, HTTP 200 only proves the request was accepted, not that the whole task succeeded.",
    )
    metrics["log_process_status_counts"] = metric(
        process,
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="internal_only",
        explanation="HTTP status distribution for /process in uvicorn.log.",
    )
    metrics["log_export_lark_request_count"] = metric(
        export.get("total", 0),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="HTTP request count for standalone /export-lark. It does not include all automatic export outcomes embedded inside /process.",
    )
    metrics["log_export_lark_success_count"] = metric(
        export.get("200", 0),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="HTTP 200 count for standalone /export-lark requests.",
    )
    metrics["log_export_lark_failure_count"] = metric(
        sum(v for k, v in export.items() if k != "total" and not k.startswith("2")),
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="internal_only",
        explanation="Non-2xx HTTP count for standalone /export-lark requests.",
    )
    metrics["log_export_success_rate"] = metric(
        ratio(export.get("200", 0), export.get("total", 0)),
        source=source,
        confidence="medium" if export.get("total", 0) else "low",
        resume_usable=bool(export.get("total", 0)),
        tier="needs_context" if export.get("total", 0) else "unavailable",
        explanation="export_lark_200_count / export_lark_request_count from uvicorn.log. It covers standalone export endpoint requests only.",
    )
    metrics["log_regenerate_summary_request_count"] = metric(
        regen.get("total", 0),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="HTTP request count for /regenerate-summary in uvicorn.log.",
    )
    metrics["log_regenerate_summary_success_count"] = metric(
        regen.get("200", 0),
        source=source,
        confidence="medium",
        resume_usable=True,
        tier="needs_context",
        explanation="HTTP 200 count for /regenerate-summary in uvicorn.log.",
    )
    metrics["log_regenerate_summary_failure_count"] = metric(
        sum(v for k, v in regen.items() if k != "total" and not k.startswith("2")),
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="internal_only",
        explanation="Non-2xx HTTP count for /regenerate-summary in uvicorn.log.",
    )
    metrics["log_regenerate_success_rate"] = metric(
        ratio(regen.get("200", 0), regen.get("total", 0)),
        source=source,
        confidence="medium" if regen.get("total", 0) else "low",
        resume_usable=bool(regen.get("total", 0)),
        tier="needs_context" if regen.get("total", 0) else "unavailable",
        explanation="regenerate_summary_200_count / regenerate_summary_request_count from uvicorn.log.",
    )
    metrics["log_summarize_transcript_file_request_count"] = metric(
        subtitle.get("total", 0),
        source=source,
        confidence="medium",
        resume_usable=bool(subtitle.get("total", 0)),
        tier="needs_context" if subtitle.get("total", 0) else "internal_only",
        explanation="HTTP request count for /summarize-transcript-file in uvicorn.log.",
    )
    metrics["log_processing_failed_count"] = metric(
        processing_failed_count,
        source=source,
        confidence="medium",
        resume_usable=False,
        tier="internal_only",
        explanation="Count of logger.exception('Processing failed') messages in uvicorn.log.",
    )
    metrics["log_failure_snippets"] = metric(
        snippets,
        source=source,
        confidence="low" if snippets else "low",
        resume_usable=False,
        tier="internal_only" if snippets else "unavailable",
        explanation="Short snippets around processing failures. Useful for diagnosis, not resume claims.",
    )
    metrics["log_failure_reason_counts"] = metric(
        failure_reason_counts,
        source=source,
        confidence="low" if failure_reason_counts else "low",
        resume_usable=False,
        tier="internal_only" if failure_reason_counts else "unavailable",
        explanation="Best-effort extraction of exception reason lines from Processing failed tracebacks.",
    )
    return {
        "process": process,
        "export_lark": export,
        "regenerate_summary": regen,
        "summarize_transcript_file": subtitle,
        "processing_failed_count": processing_failed_count,
        "failure_reason_counts": failure_reason_counts,
    }


def generate_summary_for_resume(metrics: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []

    def usable(key: str) -> dict[str, Any] | None:
        item = metrics.get(key)
        if not item:
            return None
        if not item.get("resume_usable"):
            return None
        if item.get("confidence") not in {"high", "medium"}:
            return None
        if item.get("value") == "unavailable":
            return None
        return item

    if item := usable("completed_task_count"):
        lines.append(
            f"浏览器历史快照显示已记录 {item['value']} 个 completed 任务（口径：localStorage 客户端历史，可被清空或覆盖）。"
        )
    if item := usable("processed_file_count"):
        lines.append(
            f"基于客户端历史记录，累计处理 {item['value']} 个上传文件条目（口径：completed history entries，不等同于后端持久事件数）。"
        )
    if item := usable("total_source_duration_seconds"):
        seconds = float(item["value"])
        lines.append(
            f"客户端历史中累计音视频/字幕时长约 {seconds / 3600:.2f} 小时（口径：completed entries 的 audioDurationSec 求和）。"
        )
    if item := usable("total_transcript_length_chars"):
        lines.append(
            f"客户端历史中累计转写文本约 {item['value']} 字符（口径：completed entries 的 transcriptText 长度求和）。"
        )
    if item := usable("total_markdown_note_length_chars"):
        lines.append(
            f"客户端历史中累计生成 Markdown 笔记约 {item['value']} 字符（口径：completed entries 的 summary 长度求和）。"
        )
    if item := usable("stt_elapsed_seconds_stats"):
        value = item["value"]
        if isinstance(value, dict) and value.get("count"):
            lines.append(
                f"客户端历史中 {value['count']} 个任务记录了 STT 耗时，平均 {value['avg']} 秒（口径：sttElapsedSec，仅覆盖有记录的任务）。"
            )
    if item := usable("localstorage_lark_export_unique_url_count"):
        lines.append(
            f"浏览器导出历史中记录 {item['value']} 个唯一飞书文档 URL（口径：fluentflow_lark_exports，本地最多保留 50 条）。"
        )
    if item := usable("log_process_request_count"):
        lines.append(
            f"后端历史日志显示累计发起 {item['value']} 次 /process 请求（注意：SSE HTTP 200 不等于成功完成任务）。"
        )
    if item := usable("log_export_lark_request_count"):
        lines.append(
            f"后端历史日志显示累计发起 {item['value']} 次独立 /export-lark 请求（口径：standalone export endpoint）。"
        )
    if item := usable("log_export_success_rate"):
        value = item["value"]
        if isinstance(value, dict) and value.get("value") != "unavailable":
            pct = value["value"] * 100
            lines.append(
                f"后端历史日志中独立飞书导出请求 HTTP 200 率为 {pct:.1f}%（{value['numerator']} / {value['denominator']}，不含 /process 内嵌自动导出）。"
            )
    if item := usable("log_regenerate_summary_request_count"):
        lines.append(
            f"后端历史日志显示累计发起 {item['value']} 次 /regenerate-summary 请求，说明工具支持笔记重新生成与提示词迭代。"
        )

    if not lines:
        lines.append("当前没有 high/medium 且 resume_usable=true 的指标；需要先导出 localStorage 或补充未来埋点后再生成简历口径。")
    return lines


def group_metrics(metrics: dict[str, dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped = {tier: {} for tier in TIERS}
    for key, value in metrics.items():
        grouped[value.get("tier", "internal_only")][key] = value
    return grouped


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def fmt_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = text.replace("\n", "<br>")
    if len(text) > 600:
        text = text[:600] + "...(truncated)"
    return text


def markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    grouped = group_metrics(metrics)
    lines: list[str] = []
    lines.append("# FluentFlow 历史任务快照报告")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append("- 报告性质：historical snapshot, not event log")
    lines.append("- 说明：本报告只读取导出的浏览器 localStorage JSON 与后端日志，不修改现有数据。")
    lines.append("")
    lines.append("## 输入来源")
    lines.append("")
    for name, meta in report["inputs"].items():
        lines.append(f"- `{name}`：`{meta['path']}`，exists={meta['exists']}")
        if meta.get("error"):
            lines.append(f"  - error: {meta['error']}")
    lines.append("")
    lines.append("## localStorage 导出 Console 脚本")
    lines.append("")
    lines.append("在 FluentFlow 页面打开浏览器 DevTools Console，粘贴并执行下面脚本。它会下载并尽量复制 `fluentflow_localstorage.json`，包含 `fluentflow_history` 和 `fluentflow_lark_exports`。")
    lines.append("")
    lines.append("```javascript")
    lines.append(BROWSER_EXPORT_SCRIPT)
    lines.append("```")
    lines.append("")
    lines.append("## 指标分级")
    lines.append("")
    tier_titles = {
        "resume_ready": "resume_ready：可以直接写进简历",
        "needs_context": "needs_context：可以写，但必须说明口径",
        "internal_only": "internal_only：只适合内部判断",
        "unavailable": "unavailable：当前无法统计",
    }
    for tier in TIERS:
        lines.append(f"### {tier_titles[tier]}")
        lines.append("")
        items = grouped[tier]
        if not items:
            lines.append("_无_")
            lines.append("")
            continue
        lines.append("| metric | value | source | confidence | resume_usable | explanation |")
        lines.append("|---|---:|---|---|---|---|")
        for key, item in items.items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{key}`",
                        fmt_value(item["value"]),
                        f"`{item['source']}`",
                        item["confidence"],
                        str(item["resume_usable"]).lower(),
                        item["explanation"].replace("|", "\\|"),
                    ]
                )
                + " |"
            )
        lines.append("")
    lines.append("## summary_for_resume")
    lines.append("")
    for text in report["summary_for_resume"]:
        lines.append(f"- {text}")
    lines.append("")
    lines.append("## 验证建议")
    lines.append("")
    lines.append("- 用 `rg -c 'POST /process' logs/uvicorn.log` 抽查日志请求数。")
    lines.append("- 用 `rg -c 'POST /export-lark HTTP/1.1\" 200' logs/uvicorn.log` 抽查独立飞书导出 HTTP 200 数。")
    lines.append("- 打开导出的 `exports/fluentflow_localstorage.json`，检查 history/export 数组长度是否与报告一致。")
    lines.append("- 不要把 `/process` 请求数写成成功任务数；它只是日志请求量。")
    lines.append("")
    return "\n".join(lines)


def build_report(localstorage_path: Path, log_path: Path) -> dict[str, Any]:
    localstorage_data, localstorage_error = read_json(localstorage_path)
    history, lark_exports = extract_localstorage(localstorage_data)
    log_text = read_text(log_path)
    metrics: dict[str, dict[str, Any]] = {}

    local_summary = summarize_localstorage(
        metrics,
        history,
        lark_exports,
        localstorage_path=localstorage_path,
        localstorage_error=localstorage_error,
    )
    log_summary = summarize_logs(metrics, log_path=log_path, log_text=log_text)

    report = {
        "generated_at": now_iso(),
        "report_type": "historical_snapshot_not_event_log",
        "inputs": {
            "localstorage": {
                "path": str(localstorage_path),
                "exists": localstorage_path.exists(),
                "error": localstorage_error,
            },
            "uvicorn_log": {
                "path": str(log_path),
                "exists": log_path.exists(),
                "error": None if log_text is not None else f"File not found: {log_path}",
            },
        },
        "raw_counts": {
            "localstorage_history_entries": len(local_summary.get("history", [])),
            "localstorage_lark_export_entries": len(local_summary.get("lark_exports", [])),
            "log_summary": log_summary,
        },
        "metrics": metrics,
    }
    report["metric_groups"] = group_metrics(metrics)
    report["summary_for_resume"] = generate_summary_for_resume(metrics)
    return report


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "fluentflow_history_snapshot.json"
    md_path = out_dir / "fluentflow_history_snapshot.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--localstorage",
        type=Path,
        default=DEFAULT_LOCALSTORAGE,
        help=f"Path to exported browser localStorage JSON (default: {DEFAULT_LOCALSTORAGE})",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help=f"Path to uvicorn log file (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Report output directory (default: {DEFAULT_OUT_DIR})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args.localstorage, args.log)
    json_path, md_path = write_report(report, args.out_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
