"""Requirements for the transcription model downloading in the background.

- Neither installer waits for the model: both start `stt_model.py fetch
  --background` and go on; a failure to start it does not fail the install.
- A transcription that starts before the model is on disk reports
  `downloading_model`, fetches it (or waits for the installer's download), and
  then loads it; one with the model present reports no download.
- A failed early download does not fail the task: the load goes on as before.
- The page has words for `downloading_model`.
"""

from __future__ import annotations

from pathlib import Path

from backend.core import local_stt, local_stt_assets

ROOT = Path(__file__).resolve().parents[1]


def test_installers_start_the_model_in_the_background():
    windows = (ROOT / "launchers" / "windows" / "setup-local.ps1").read_text(encoding="utf-8-sig")
    macos = (ROOT / "launchers" / "macos" / "setup-local.sh").read_text(encoding="utf-8")
    for text in (windows, macos):
        assert '"fetch" "--background"' in text or "fetch --background" in text
    assert "Invoke-Checked $Python @((Join-Path $Repo \"scripts\\stt_model.py\")" not in windows


def _record(monkeypatch, *, present: bool, fails: bool = False):
    calls: list[str] = []
    monkeypatch.setattr(local_stt_assets, "is_downloaded", lambda plan: present)

    def ensure(plan):
        calls.append("download")
        if fails:
            raise RuntimeError("offline")
        return Path("/model.bin")

    monkeypatch.setattr(local_stt_assets, "ensure_downloaded", ensure)
    return calls


def test_a_missing_model_is_fetched_first_and_said(monkeypatch):
    calls = _record(monkeypatch, present=False)
    statuses: list[str] = []

    local_stt._download_model_if_missing("medium", engine="faster_whisper", device="cpu", on_status=statuses.append)

    assert statuses == ["downloading_model"]
    assert calls == ["download"]


def test_a_present_model_says_nothing(monkeypatch):
    calls = _record(monkeypatch, present=True)
    statuses: list[str] = []

    local_stt._download_model_if_missing("medium", engine="faster_whisper", device="cpu", on_status=statuses.append)

    assert statuses == [] and calls == []


def test_a_failed_early_download_does_not_raise(monkeypatch):
    _record(monkeypatch, present=False, fails=True)
    local_stt._download_model_if_missing("medium", engine="faster_whisper", device="cpu", on_status=None)


def test_the_page_names_the_download():
    shared = (ROOT / "frontend" / "src" / "app" / "shared.jsx").read_text(encoding="utf-8")
    fmt = (ROOT / "frontend" / "src" / "lib" / "format.js").read_text(encoding="utf-8")
    assert "downloading_model: 'dash.sttDownloadingModel'" in fmt
    assert shared.count("'dash.sttDownloadingModel'") == 2
