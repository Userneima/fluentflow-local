"""CI must have the tools the real-ffmpeg tests need.

Those tests skip themselves when ffmpeg is missing, and a skip in CI looks like
a pass. This turns "ffmpeg is not installed on the runner" into a failure, but
only where CI sets the ``CI`` variable; a developer machine without ffmpeg is
told by the readiness check, not by the test suite.
"""

from __future__ import annotations

import os
import shutil

import pytest


@pytest.mark.skipif(not os.environ.get("CI"), reason="only CI promises ffmpeg")
def test_ci_has_ffmpeg_on_path() -> None:
    assert shutil.which("ffmpeg"), "CI must install ffmpeg so the real-ffmpeg tests run instead of skipping"
    assert shutil.which("ffprobe"), "ffprobe ships with ffmpeg and the media probe needs it"
