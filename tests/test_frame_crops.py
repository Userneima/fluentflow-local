"""Screenshots in a note cropped to the part that matters.

What has to hold, written from the requirement (2026-10-14, 王愉超定) rather than
the code:

- A note can cite a screenshot with a region, ``![图注](note_0003.jpg#crop=x0,y0,x1,y1)``
  in fractions of the picture's width and height. The saved note then shows just
  that region: a new picture holding only those pixels, linked where the full
  frame would have been.
- A region that is not a region (outside the picture, empty, a sliver, or the
  whole frame) leaves the full screenshot in place rather than breaking the note.
- The cropped picture is a cited frame like any other: counted as cited, served
  by address, and still recognised when an agent hands the note back with the
  addresses in it.
"""

from __future__ import annotations

import shutil

import pytest
from PIL import Image

import backend.core.agent_note as an
import backend.core.visual_note_job as vn
from backend.core.claude_vision import FrameInput

TASK = "task-frame-crops"


@pytest.fixture
def frame():
    # The session's artifact folder (conftest points it at a temporary one), so
    # the serving side finds the files the same way it does for a real task.
    folder = vn._frame_artifact_path(TASK, "x").parent
    shutil.rmtree(folder.parent, ignore_errors=True)
    folder.mkdir(parents=True)
    path = folder / "note_0003.jpg"
    picture = Image.new("RGB", (200, 100), "white")
    # The region that matters: a red block in the top-right quarter.
    for x in range(100, 200):
        for y in range(0, 50):
            picture.putpixel((x, y), (255, 0, 0))
    picture.save(path, quality=95)
    return FrameInput(filename="note_0003.jpg", path=path, timestamp_seconds=42.0, source="scene")


def test_a_cited_region_becomes_a_picture_of_just_that_region(frame):
    markdown, frames = vn.apply_crops("看板分区：\n\n![看板](note_0003.jpg#crop=0.5,0,1,0.5)", TASK, [frame])

    assert "#crop" not in markdown
    cropped = [f for f in frames if f.filename != "note_0003.jpg"]
    assert len(cropped) == 1 and f"]({cropped[0].filename})" in markdown
    with Image.open(cropped[0].path) as picture:
        assert picture.size == (100, 50)
        red, green, blue = picture.convert("RGB").getpixel((50, 25))
        assert red > 200 and green < 60 and blue < 60
    assert cropped[0].timestamp_seconds == 42.0


@pytest.mark.parametrize("region", ["0.5,0,1.4,0.5", "0.6,0.2,0.6,0.8", "0,0,0.05,0.05", "0,0,1,1", "a,b,c,d"])
def test_a_region_that_is_not_one_keeps_the_whole_screenshot(frame, region):
    markdown, frames = vn.apply_crops(f"![看板](note_0003.jpg#crop={region})", TASK, [frame])

    assert markdown == "![看板](note_0003.jpg)"
    assert [f.filename for f in frames] == ["note_0003.jpg"]


def test_a_cropped_picture_is_cited_served_and_survives_a_round_trip(frame):
    markdown, frames = vn.apply_crops("![看板](note_0003.jpg#crop=0.5,0,1,0.5)", TASK, [frame])
    cropped_name = next(f.filename for f in frames if f.filename != "note_0003.jpg")

    assert [f.filename for f in vn.cited_frames(markdown, frames)] == [cropped_name]
    served = vn._rewrite_image_targets(markdown, TASK, frames)
    assert f"file={cropped_name}" in served

    # An agent hands the note back with the served address in it; the next save
    # only knows the frames that were sent, and must still find the crop.
    handed_back = an._SERVED_FRAME.sub(r"(\1)", served)
    again, frames_again = vn.apply_crops(handed_back, TASK, [frame])
    assert [f.filename for f in vn.cited_frames(again, frames_again)] == [cropped_name]
