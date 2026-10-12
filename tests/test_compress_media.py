"""A compressed copy beside a recording the breath-gap cut left as it was.

From the requirement:

- Only a video whose bitrate makes it worth it gets a copy; audio and
  low-bitrate video do not.
- The copy keeps the picture size and length and has sound when the source did;
  one that does not is deleted, and the original is never touched.
- It goes beside the original as <name>_压缩.mp4 and never overwrites a file.
- Above 30fps the copy is 30fps, one keyframe every five seconds.
- It happens for a file in the user's own folder whose cut was declined, not for
  an upload in FluentFlow's store and not for a file that is already a cut.
- A later pass over the folder does not take the copy for a new recording.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.core.compress_media as cm
from backend.core import debreath_job, local_folder_intake, local_intake_flow

SOURCE = {"has_video": True, "has_audio": True, "width": 2940, "height": 1718, "bps": 6_000_000, "fps": 60, "duration": 3110.3}


def _runner(source: Path, copy: dict, *, encode_ok: bool = True):
    """Answers ffprobe for the source and the copy, and 'encodes' by writing the file."""

    def run(command):
        cmd = [str(c) for c in command]
        if "-encoders" in cmd:
            return SimpleNamespace(stdout=" V..... hevc_videotoolbox", stderr="", returncode=0)
        if cmd[0].endswith("ffprobe"):
            info = SOURCE if cmd[-1] == str(source) else copy
            query = " ".join(cmd)
            if "stream=width,height" in query:
                out = f"{info['width']},{info['height']}"
            elif "a:0" in query:
                out = "audio" if info["has_audio"] else ""
            elif "v:0" in query and "codec_type" in query:
                out = "video" if info["has_video"] else ""
            elif "format=bit_rate" in query:
                out = str(info["bps"])
            elif "avg_frame_rate" in query:
                out = f"{info['fps']}/1"
            elif "format=duration" in query:
                out = str(info["duration"])
            else:
                out = ""
            return SimpleNamespace(stdout=out, stderr="", returncode=0)
        Path(cmd[-1]).write_bytes(b"copy")
        return SimpleNamespace(stdout="", stderr="" if encode_ok else "boom", returncode=0 if encode_ok else 1)

    return run


@pytest.fixture()
def source(tmp_path):
    path = tmp_path / "会议.mov"
    path.write_bytes(b"x" * 1000)
    return path


def test_only_a_high_bitrate_video_is_worth_a_copy():
    assert cm.worth_compressing(SOURCE)
    assert not cm.worth_compressing({**SOURCE, "bps": 1_900_000})
    assert not cm.worth_compressing({**SOURCE, "has_video": False})


def test_above_30fps_the_copy_is_30fps_with_a_keyframe_every_five_seconds():
    fast = cm.encode_args(SOURCE)
    assert fast[fast.index("-r") + 1] == "30" and fast[fast.index("-g") + 1] == "150"
    slow = cm.encode_args({**SOURCE, "fps": 25})
    assert "-r" not in slow and slow[slow.index("-g") + 1] == "125"


def test_a_good_copy_goes_beside_the_original_without_overwriting(source):
    (source.parent / "会议_压缩.mp4").write_bytes(b"theirs")

    done = cm.compress_beside(source, runner=_runner(source, dict(SOURCE)))

    assert done["compressed"] is True
    assert Path(done["compressed_path"]).name == "会议_压缩 (2).mp4"
    assert (source.parent / "会议_压缩.mp4").read_bytes() == b"theirs"
    assert source.read_bytes() == b"x" * 1000


@pytest.mark.parametrize("copy, why", [
    ({**SOURCE, "duration": 1500.0}, "时长"),
    ({**SOURCE, "width": 1470, "height": 859}, "尺寸"),
    ({**SOURCE, "has_audio": False}, "声音"),
])
def test_a_copy_that_cannot_stand_in_is_deleted_and_the_original_kept(source, copy, why):
    done = cm.compress_beside(source, runner=_runner(source, copy))

    assert done["compressed"] is False and why in done["compress_error"]
    assert not (source.parent / "会议_压缩.mp4").exists()
    assert source.exists()


def test_a_failed_encode_leaves_nothing_behind(source):
    done = cm.compress_beside(source, runner=_runner(source, dict(SOURCE), encode_ok=False))

    assert done["compressed"] is False
    assert not (source.parent / "会议_压缩.mp4").exists()


def _declined(path, **extra):
    return debreath_job.PreparedCut(path=path, used=False, state={"rendered": False, **extra}, artifacts={})


@pytest.mark.parametrize("managed, already_cut, expected", [
    (False, False, True),   # the user's own file, cut declined: copy
    (True, False, False),   # an upload in FluentFlow's store: no copy
    (False, True, False),   # a file that is already a cut: no copy
])
def test_the_copy_is_made_for_a_declined_cut_in_the_users_folder(monkeypatch, source, managed, already_cut, expected):
    calls = []
    monkeypatch.setattr(local_intake_flow, "cut_first_enabled", lambda: True)
    monkeypatch.setattr(local_intake_flow.debreath_job, "prepare_cut_media",
                        lambda task_id, path: _declined(path, **({"already_cut": True} if already_cut else {})))
    monkeypatch.setattr(local_intake_flow, "is_managed_path", lambda path: managed)
    monkeypatch.setattr(local_intake_flow.compress_media, "compress_beside",
                        lambda path: calls.append(path) or {"compressed": True})

    prepared = local_intake_flow.preprocess_media("t1", source)

    assert bool(calls) is expected
    assert prepared.path == source and prepared.used is False


def test_a_folder_pass_skips_the_copies(tmp_path):
    (tmp_path / "会议.mov").write_bytes(b"x")
    (tmp_path / "会议_压缩.mp4").write_bytes(b"x")

    listing = local_folder_intake.list_media(tmp_path)

    assert [p.name for p in listing.files] == ["会议.mov"]


def test_the_agent_package_says_where_the_copy_is():
    from backend.core.agent_package_core import _agent_debreath

    block = _agent_debreath({"debreath": {"rendered": False, "compressed_path": "/u/会议_压缩.mp4"}}, {})

    assert block["compressed_path"] == "/u/会议_压缩.mp4"
