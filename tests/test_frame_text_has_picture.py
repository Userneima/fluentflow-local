"""Whether a file has a picture to read text from (frame_text.has_picture):
an audio file's cover art does not count, so a speechless song with album art
is not read as a video, nor corrected from on-screen subtitles."""

import shutil
import subprocess

import pytest

from backend.core import frame_text

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


def _run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def test_cover_art_is_not_a_picture_but_a_video_is(tmp_path):
    cover, audio, song, video = (tmp_path / n for n in ("cover.png", "a.m4a", "song.m4a", "v.mp4"))
    _run("-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", str(cover))
    _run("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-c:a", "aac", str(audio))
    _run("-i", str(audio), "-i", str(cover), "-map", "0", "-map", "1", "-c", "copy",
         "-disposition:v:0", "attached_pic", str(song))
    _run("-f", "lavfi", "-i", "color=blue:s=64x64:d=1", str(video))

    assert frame_text.has_picture(audio) is False
    assert frame_text.has_picture(song) is False
    assert frame_text.has_picture(video) is True
