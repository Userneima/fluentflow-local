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

Which model gets fetched is not this script's decision: it asks
`local_stt.planned_model()`, the same resolution a real transcription goes
through, so a machine that will transcribe on medium does not download
large-v3 and then never open it.

    python scripts/stt_model.py fetch
    python scripts/stt_model.py fetch --size medium
    python scripts/stt_model.py fetch --engine faster_whisper
    python scripts/stt_model.py fetch --mirror        # 直接用镜像源
    python scripts/stt_model.py cache-paths
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Both of these are standard-library-only on purpose: the download endpoint has
# to be chosen before anything imports `huggingface_hub`, which reads it into a
# constant at import time. That is why the heavy imports below live inside the
# handlers instead of at the top of the file.
from backend.core import hf_endpoint  # noqa: E402
from backend.core.local_stt_policy import DEFAULT_LOCAL_STT_MODEL  # noqa: E402


def _format_size(total: int | None) -> str:
    if not total:
        return "大小未知"
    return f"约 {total / 1_000_000_000:.1f} GB"


def _choose_endpoint(args: argparse.Namespace) -> None:
    """Decide where the weights come from, before the download library loads."""
    if args.mirror:
        os.environ[hf_endpoint.ENDPOINT_ENV] = hf_endpoint.MIRROR_ENDPOINT
        print(f"下载源：{hf_endpoint.MIRROR_ENDPOINT}（按 --mirror 指定）")
        return
    if args.no_mirror:
        os.environ[hf_endpoint.ENDPOINT_ENV] = hf_endpoint.DEFAULT_ENDPOINT
        print(f"下载源：{hf_endpoint.DEFAULT_ENDPOINT}（按 --no-mirror 指定）")
        return
    _, explanation = hf_endpoint.apply_to_environment()
    print(explanation)


def _fetch(args: argparse.Namespace) -> int:
    _choose_endpoint(args)

    from backend.core.local_stt_assets import (
        cached_weights_path,
        download_size_bytes,
        ensure_downloaded,
        planned_model,
    )

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
        print(
            "  可以稍后重试；第一次转录时也会自动下载。"
            f"网络到 {hf_endpoint.DEFAULT_ENDPOINT} 不通时，"
            "可以加 --mirror 换用镜像源。",
            file=sys.stderr,
        )
        return 1

    print(f"\n✓ 下载完成：{path}")
    return 0


def _cache_paths(_args: argparse.Namespace) -> int:
    """One existing directory per line, for a shell to size up or delete."""
    from backend.core.local_stt_assets import cache_dirs

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
    source = fetch.add_mutually_exclusive_group()
    source.add_argument(
        "--mirror",
        action="store_true",
        help=f"直接用镜像源 {hf_endpoint.MIRROR_ENDPOINT}，不先试主站",
    )
    source.add_argument(
        "--no-mirror",
        action="store_true",
        help="只用主站，连不上就报错",
    )
    fetch.set_defaults(handler=_fetch)

    paths = sub.add_parser("cache-paths", help="列出本机上属于本产品的模型目录")
    paths.set_defaults(handler=_cache_paths)

    args = parser.parse_args(sys.argv[1:] or ["fetch"])
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
