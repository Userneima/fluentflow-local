"""onnxruntime's usage reporting is off in the service and in test runs.

Requirement (owner, 2026-10-25): running FluentFlow or its tests must not make
macOS show "Python quit unexpectedly". The aborts came from onnxruntime's
reporting thread sending events while the process exited (backend/core/onnx_quiet.py).
"""

from __future__ import annotations

import pytest

from backend.core import onnx_quiet


def test_reporting_is_turned_off(monkeypatch):
    onnxruntime = pytest.importorskip("onnxruntime")
    calls = []
    monkeypatch.setattr(onnxruntime, "disable_telemetry_events", lambda: calls.append("off"))

    assert onnx_quiet.quiet() is True
    assert calls == ["off"]


def test_a_failure_to_turn_it_off_does_not_stop_startup(monkeypatch):
    onnxruntime = pytest.importorskip("onnxruntime")

    def broken():
        raise RuntimeError("no telemetry provider")

    monkeypatch.setattr(onnxruntime, "disable_telemetry_events", broken)

    assert onnx_quiet.quiet() is False


def test_the_service_turns_it_off_at_startup():
    """Read from the source rather than importing the service a second time:
    reloading it would register its routes and listeners again."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "backend" / "local_main.py").read_text(encoding="utf-8"))
    calls = [
        node.value.func for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
    ]
    assert any(f.attr == "quiet" and getattr(f.value, "id", "") == "onnx_quiet" for f in calls)
