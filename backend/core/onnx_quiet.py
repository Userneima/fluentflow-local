"""Turn off onnxruntime's own usage reporting before anything uses it.

onnxruntime (loaded by faster-whisper's voice detector) starts a reporting
thread that sends usage events to Microsoft. When the process exits while that
thread is sending, it locks a mutex the exit has already destroyed, the
process aborts, and macOS shows "Python quit unexpectedly". On 2026-10-25 this
happened five times in eight minutes while tests ran in a loop (crash reports
in ~/Library/Logs/DiagnosticReports, thread
``Microsoft::Applications::Events::LogManagerImpl::DispatchEvent``). With the
reporting off there is nothing left to send at exit.

Called once at startup of the service and of the test run; the setting is
process-wide, so it covers every later use.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def quiet() -> bool:
    """True when onnxruntime is installed and its reporting is now off."""
    try:
        import onnxruntime
    except ImportError:
        return False
    try:
        onnxruntime.disable_telemetry_events()
    except Exception as exc:  # noqa: BLE001 - reporting stays on, nothing else changes
        logger.warning("could not turn off onnxruntime telemetry: %s", exc)
        return False
    return True
