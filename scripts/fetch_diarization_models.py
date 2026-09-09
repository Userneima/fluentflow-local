#!/usr/bin/env python3
"""Stage the speaker-diarization models locally, from ModelScope.

pyannote normally fetches these from Hugging Face the first time it runs. That
path is not usable everywhere: on 2026-09-03 this machine reached the Hugging
Face API in about a second but got **zero bytes** from the model CDN
(``us.aws.cdn.hf.co`` returned 206 and then no data), and because
``from_pretrained`` has no timeout the whole task sat in it for over an hour
with no error. ModelScope serves the same three files from pyannote's own
mirrored repos at a few MB/s.

Run this once per machine::

    venv/bin/python scripts/fetch_diarization_models.py

It writes ``config.yaml`` plus the two checkpoints into the runtime model
directory (``FLUENTFLOW_DIARIZATION_MODEL_DIR``, by default ``models/pyannote``
under the app data root). The loader prefers that directory, and with it no
Hugging Face token is needed at run time.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.runtime_paths import default_diarization_model_dir  # noqa: E402

MODELSCOPE_FILE = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"

SEGMENTATION_REPO = "pyannote/segmentation-3.0"
EMBEDDING_REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"

# The published 3.1 pipeline config, with the two model references pointed at
# the files this script just wrote. Keeping it inline rather than downloading
# it means the rewrite cannot silently miss a field the upstream config added.
CONFIG_TEMPLATE = """version: 3.1.0

pipeline:
  name: pyannote.audio.pipelines.SpeakerDiarization
  params:
    clustering: AgglomerativeClustering
    embedding: {embedding}
    embedding_batch_size: 32
    embedding_exclude_overlap: true
    segmentation: {segmentation}
    segmentation_batch_size: 32

params:
  clustering:
    method: centroid
    min_cluster_size: 12
    threshold: 0.7045654963945799
  segmentation:
    min_duration_off: 0.0
"""


def download(repo: str, remote_path: str, target: Path, timeout: float) -> int:
    """Fetch one file, and refuse to install a short one.

    The length check is the whole point of writing to a ``.part`` first. A
    connection cut mid-transfer returns a non-empty body, and installing that
    would leave a truncated checkpoint under the final name: the loader then
    reports the models present and fails every run, while a re-run of this
    script skips the file because it already exists.
    """
    url = MODELSCOPE_FILE.format(repo=repo, path=remote_path)
    started = time.time()
    partial = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed host
        if response.status != 200:
            raise RuntimeError(f"{repo}/{remote_path} returned HTTP {response.status}")
        declared = response.headers.get("Content-Length")
        expected = int(declared) if declared and declared.isdigit() else None
        written = 0
        with partial.open("wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
    if written == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"{repo}/{remote_path} returned an empty body")
    if expected is not None and written != expected:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"{repo}/{remote_path} was cut short: got {written} bytes of {expected}. "
            "Re-run this script."
        )
    partial.replace(target)
    size = target.stat().st_size
    print(f"  {target.name}: {size / 1_048_576:.1f} MB in {time.time() - started:.1f}s")
    return size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Directory to write into (default: the runtime diarization model dir)",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-file timeout in seconds")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist")
    args = parser.parse_args()

    target_dir = args.target or default_diarization_model_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"Staging diarization models in {target_dir}")

    segmentation = target_dir / "segmentation.bin"
    embedding = target_dir / "wespeaker.bin"

    for repo, path in ((SEGMENTATION_REPO, "pytorch_model.bin"), (EMBEDDING_REPO, "pytorch_model.bin")):
        destination = segmentation if repo == SEGMENTATION_REPO else embedding
        if destination.is_file() and not args.force:
            print(f"  {destination.name}: already present, skipping")
            continue
        download(repo, path, destination, args.timeout)

    config = target_dir / "config.yaml"
    config.write_text(
        CONFIG_TEMPLATE.format(embedding=embedding.as_posix(), segmentation=segmentation.as_posix()),
        encoding="utf-8",
    )
    print(f"  {config.name}: written")
    print("Done. Speaker diarization now runs from these files and needs no Hugging Face token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
