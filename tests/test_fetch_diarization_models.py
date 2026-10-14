"""Requirements for scripts/fetch_diarization_models.py.

- Downloads both model files into the model dir under their final names.
- Writes under a temporary name and renames only when complete: a failed or
  short download leaves nothing under the final name.
- Skips files already present.
- A failed download from GitHub ends with a plain Chinese message saying there
  is no mirror, and a non-zero exit.
- Runs with the standard library only.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tarfile
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_diarization_models.py"


@pytest.fixture()
def fetch():
    spec = importlib.util.spec_from_file_location("fetch_diarization_models_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _segmentation_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, data in (
            ("sherpa-onnx-pyannote-segmentation-3-0/model.onnx", b"SEGMENTATION"),
            ("sherpa-onnx-pyannote-segmentation-3-0/README.md", b"readme"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __init__(self, body: bytes, declared: int | None = None):
        super().__init__(body)
        self.status = 200
        self.headers = {"Content-Length": str(len(body) if declared is None else declared)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _serve(monkeypatch, fetch, bodies: dict[str, object]):
    calls: list[str] = []

    def urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        body = bodies[url]
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)
    return calls


def test_downloads_both_models_under_their_final_names(monkeypatch, fetch, tmp_path):
    _serve(monkeypatch, fetch, {
        fetch.SEGMENTATION_URL: _Response(_segmentation_archive()),
        fetch.EMBEDDING_URL: _Response(b"EMBEDDING"),
    })

    assert fetch.main(["--target", str(tmp_path)]) == 0

    assert (tmp_path / "segmentation.onnx").read_bytes() == b"SEGMENTATION"
    assert (tmp_path / "embedding.onnx").read_bytes() == b"EMBEDDING"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["embedding.onnx", "segmentation.onnx"]


def test_skips_files_already_present(monkeypatch, fetch, tmp_path):
    (tmp_path / "segmentation.onnx").write_bytes(b"old")
    calls = _serve(monkeypatch, fetch, {fetch.EMBEDDING_URL: _Response(b"EMBEDDING")})

    assert fetch.main(["--target", str(tmp_path)]) == 0

    assert calls == [fetch.EMBEDDING_URL]
    assert (tmp_path / "segmentation.onnx").read_bytes() == b"old"


def test_a_short_download_leaves_nothing_under_the_final_name(monkeypatch, fetch, tmp_path, capsys):
    _serve(monkeypatch, fetch, {
        fetch.SEGMENTATION_URL: _Response(_segmentation_archive()),
        fetch.EMBEDDING_URL: _Response(b"EMBE", declared=9),
    })

    assert fetch.main(["--target", str(tmp_path)]) == 1

    assert not (tmp_path / "embedding.onnx").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["segmentation.onnx"]


def test_a_failed_github_download_says_there_is_no_mirror(monkeypatch, fetch, tmp_path, capsys):
    _serve(monkeypatch, fetch, {
        fetch.SEGMENTATION_URL: urllib.error.URLError("timed out"),
        fetch.EMBEDDING_URL: urllib.error.URLError("timed out"),
    })

    assert fetch.main(["--target", str(tmp_path)]) == 1

    out = capsys.readouterr().out
    assert "GitHub" in out
    assert "没有国内镜像" in out
    assert "fetch_diarization_models.py" in out
    assert not any(p.suffix == ".onnx" for p in tmp_path.iterdir())


def test_runs_with_the_standard_library_only(tmp_path):
    # -S: no site-packages; the script's own --help must still work.
    result = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), "--help"], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
