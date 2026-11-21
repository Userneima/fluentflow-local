"""scripts/run_service.py takes the launching window's environment from a
file, deletes the file, and starts the service on the given port."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reads_and_deletes_the_environment_file(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("run_service", ROOT / "scripts" / "run_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    started = {}
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, host, port: started.update(app=app, host=host, port=port)))
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps({"FLUENTFLOW_TEST_FROM_WINDOW": "yes"}), encoding="utf-8")
    monkeypatch.delenv("FLUENTFLOW_TEST_FROM_WINDOW", raising=False)

    assert module.main(["--env-file", str(env_file), "--port", "8123"]) == 0

    assert os.environ.pop("FLUENTFLOW_TEST_FROM_WINDOW") == "yes"
    assert not env_file.exists()
    assert started == {"app": "backend.local_main:app", "host": "127.0.0.1", "port": 8123}
