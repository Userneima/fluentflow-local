#!/usr/bin/env python3
"""Download the two speaker-diarization models sherpa-onnx needs.

Run once per machine (the macOS and Windows installers run it for you)::

    .venv/bin/python scripts/fetch_diarization_models.py

It writes ``segmentation.onnx`` (pyannote segmentation 3.0, exported to ONNX
by the sherpa-onnx project, about 6 MB) and ``embedding.onnx`` (3D-Speaker
ERes2Net base, Chinese, 16 kHz, about 40 MB) into the runtime model directory:
``FLUENTFLOW_DIARIZATION_MODEL_DIR``, by default ``models/speaker`` under the
app data root. Files already there are skipped. Each file is written under a
temporary name, checked against a pinned SHA-256 and renamed only once it
passes, so an interrupted or altered download never sits under the final name.

Each file has a host in China beside GitHub, serving the same bytes (checked
2026-11-21): the segmentation model on ModelScope, the embedding model on
hf-mirror.com. ``backend.core.download_source`` probes GitHub first and puts
the mirror first when GitHub is unreachable or slow; the other is still tried
when the first fails.

Standard library only: it has to run before anything else is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import download_source  # noqa: E402
from backend.core.runtime_paths import default_diarization_model_dir, load_project_env  # noqa: E402

load_project_env()

SEGMENTATION_FILENAME = "segmentation.onnx"
EMBEDDING_FILENAME = "embedding.onnx"

SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
# The member of that archive that is the model itself.
SEGMENTATION_MEMBER = "model.onnx"
# The same model.onnx, unpacked, on ModelScope.
SEGMENTATION_MIRROR_URL = (
    "https://modelscope.cn/models/pengzhendong/sherpa-onnx-pyannote-segmentation-3-0/resolve/master/model.onnx"
)
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)
# The sherpa-onnx author's own copy on Hugging Face, through the mirror.
EMBEDDING_MIRROR_URL = (
    "https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/"
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)

# SHA-256 of the model files as written to disk, whichever host they came from.
SHA256 = {
    SEGMENTATION_FILENAME: "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    EMBEDDING_FILENAME: "f1ee61be04000b50392a63442e5dd9feaea7e81dddd494d3647cf1c3d88a1498",
}


class DownloadError(RuntimeError):
    pass


def _download(url: str, destination: Path, timeout: float) -> None:
    """Fetch ``url`` into ``destination`` and refuse a short or empty body."""
    request = urllib.request.Request(url, headers={"User-Agent": "FluentFlow-Local"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed host
            status = getattr(response, "status", 200)
            if status != 200:
                raise DownloadError(f"HTTP {status}")
            declared = response.headers.get("Content-Length")
            expected = int(declared) if declared and declared.isdigit() else None
            written = 0
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DownloadError(str(getattr(exc, "reason", exc))) from exc
    if written == 0:
        raise DownloadError("empty response")
    if expected is not None and written != expected:
        raise DownloadError(f"cut short: got {written} bytes of {expected}")


def _extract_member(archive: Path, member_name: str, destination: Path) -> None:
    """Copy one file out of a tar archive by its base name, without extracting paths."""
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            if member.isfile() and Path(member.name).name == member_name:
                source = tar.extractfile(member)
                if source is None:
                    break
                with source, destination.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                if destination.stat().st_size == 0:
                    raise DownloadError(f"{member_name} in the archive is empty")
                return
    raise DownloadError(f"{member_name} not found in the downloaded archive")


def _check(path: Path, name: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != SHA256[name]:
        raise DownloadError(f"checksum mismatch ({digest.hexdigest()[:12]}…)")


def _from_sources(target: Path, timeout: float, sources: dict[str, object]) -> None:
    """Try each source, fastest first, until one yields a file that checks out."""
    errors: list[str] = []
    for url in download_source.order(list(sources)):
        with tempfile.TemporaryDirectory(dir=target.parent, prefix=".fetch-") as scratch:
            partial = Path(scratch) / target.name
            try:
                sources[url](url, Path(scratch), partial, timeout)  # type: ignore[operator]
                _check(partial, target.name)
            except (DownloadError, tarfile.TarError, OSError) as exc:
                # A host that answered with an error page or a broken archive
                # is skipped like one that did not answer.
                errors.append(f"{url.split('/')[2]}: {exc}")
                continue
            partial.replace(target)
            return
    raise DownloadError("; ".join(errors))


def _direct(url: str, scratch: Path, partial: Path, timeout: float) -> None:
    _download(url, partial, timeout)


def _from_archive(url: str, scratch: Path, partial: Path, timeout: float) -> None:
    archive = scratch / "segmentation.tar.bz2"
    _download(url, archive, timeout)
    _extract_member(archive, SEGMENTATION_MEMBER, partial)


def fetch_segmentation(target: Path, timeout: float) -> None:
    _from_sources(target, timeout, {SEGMENTATION_URL: _from_archive, SEGMENTATION_MIRROR_URL: _direct})


def fetch_embedding(target: Path, timeout: float) -> None:
    _from_sources(target, timeout, {EMBEDDING_URL: _direct, EMBEDDING_MIRROR_URL: _direct})


def fetch_all(target_dir: Path, timeout: float = 300.0, force: bool = False) -> list[str]:
    """Fetch whichever model files are missing; return the names that failed."""
    target_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for name, fetch in ((SEGMENTATION_FILENAME, fetch_segmentation), (EMBEDDING_FILENAME, fetch_embedding)):
        target = target_dir / name
        if target.is_file() and target.stat().st_size > 0 and not force:
            print(f"  {name}：已存在，跳过")
            continue
        started = time.time()
        try:
            fetch(target, timeout)
        except DownloadError as exc:
            print(f"  {name}：下载失败（{exc}）")
            failed.append(name)
            continue
        size_mb = target.stat().st_size / 1_048_576
        print(f"  {name}：{size_mb:.1f} MB，用时 {time.time() - started:.1f} 秒")
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Directory to write into (default: the runtime diarization model dir)",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-file network timeout in seconds")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist")
    args = parser.parse_args(argv)

    target_dir = args.target or default_diarization_model_dir()
    print(f"讲话人区分模型目录：{target_dir}")
    failed = fetch_all(target_dir, timeout=args.timeout, force=args.force)
    if failed:
        print(
            "讲话人区分模型没有下载完（GitHub 和国内镜像都试过了）。"
            "请检查网络或代理，然后重新运行这个脚本："
            f"{Path(sys.executable).name} scripts/fetch_diarization_models.py"
        )
        return 1
    print("完成。讲话人区分可以用了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
