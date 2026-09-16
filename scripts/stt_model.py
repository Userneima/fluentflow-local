#!/usr/bin/env python3
"""The local transcription model: fetch it, or say where it lives.

`fetch` exists because otherwise the download happens inside the user's first
transcription: the installer says it is finished, the user drops in a video,
and then waits several gigabytes with a progress bar that is measuring
something else entirely.

`cache-paths` exists because the uninstaller must not guess. The models sit in
the Hugging Face cache, which every other tool on the machine shares, so the
directories to remove are the ones this product actually downloads and nothing
that merely looks similar.

Which model gets fetched is not this script's decision either: it asks
`local_stt.planned_model()`, the same resolution a real transcription goes
through, so a machine that will transcribe on medium does not download
large-v3 and then never open it.

    python scripts/stt_model.py fetch
    python scripts/stt_model.py fetch --size medium
    python scripts/stt_model.py fetch --engine faster_whisper
    python scripts/stt_model.py cache-paths
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.local_stt_policy import DEFAULT_LOCAL_STT_MODEL  # noqa: E402
from backend.core.local_stt_assets import (  # noqa: E402
    cache_dirs,
    cached_weights_path,
    download_size_bytes,
    ensure_downloaded,
    planned_model,
)


def _format_size(total: int | None) -> str:
    if not total:
        return "大小未知"
    return f"约 {total / 1_000_000_000:.1f} GB"


def _fetch(args: argparse.Namespace) -> int:
    plan = planned_model(args.size or DEFAULT_LOCAL_STT_MODEL, engine=args.engine)

    lane = "Apple 芯片加速（mlx-whisper）" if plan.engine == "mlx" else "faster-whisper"
    print(f"转录路径：{lane}")
    if plan.downgraded:
        print(f"模型：{plan.model_size}（这台机器跑不动 {plan.requested_size}，已自动改用它）")
    else:
        print(f"模型：{plan.model_size}")
    print(f"来源：{plan.repo_id}")

    existing = cached_weights_path(plan)
    if existing is not None:
        print(f"\n✓ 已经下载过了：{existing}")
        return 0

    print(f"\n需要下载 {_format_size(download_size_bytes(plan))}，请保持网络畅通…\n")

    try:
        path = ensure_downloaded(plan)
    except Exception as exc:  # noqa: BLE001 - a download failure is the user's to read
        print(f"\n✗ 下载失败：{exc}", file=sys.stderr)
        print("  可以稍后重试；第一次转录时也会自动下载。", file=sys.stderr)
        return 1

    print(f"\n✓ 下载完成：{path}")
    return 0


def _cache_paths(_args: argparse.Namespace) -> int:
    """One existing directory per line, for a shell to size up or delete."""
    for path in cache_dirs():
        print(path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="本地转录模型")
    sub = parser.add_subparsers(dest="command")

    fetch = sub.add_parser("fetch", help="下载这台机器会用到的转录模型")
    fetch.add_argument("--size", default=None, help="模型尺寸（默认取产品当前默认值）")
    fetch.add_argument(
        "--engine",
        default=None,
        choices=("auto", "mlx", "faster_whisper"),
        help="强制使用某条转录路径（默认自动选择）",
    )
    fetch.set_defaults(handler=_fetch)

    paths = sub.add_parser("cache-paths", help="列出本机上属于本产品的模型目录")
    paths.set_defaults(handler=_cache_paths)

    args = parser.parse_args(sys.argv[1:] or ["fetch"])
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
