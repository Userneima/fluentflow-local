from __future__ import annotations

from pathlib import Path

import pytest

from backend.core import frame_text


def test_only_the_text_no_earlier_frame_had_is_reported() -> None:
    """The reason the index reports new text rather than all of it.

    Measured on the coding class: every frame read about a thousand characters and
    nearly all of it was the same Feishu sidebar. Ranking frames by how much text
    they carry would have called a page of navigation furniture as informative as
    the slide beside it.
    """
    seen = frame_text.SeenLines()
    sidebar = ["消息 飞行社 飞书", "金冠电气", "未命名文档"]

    first = frame_text.new_lines(frame_text.FrameText(lines=[*sidebar, "第一课：原语层"]), seen)
    second = frame_text.new_lines(frame_text.FrameText(lines=[*sidebar, "docker run -p 8080"]), seen)

    assert first == ["消息 飞行社 飞书", "金冠电气", "未命名文档", "第一课：原语层"]
    assert second == ["docker run -p 8080"], "the sidebar is furniture after the first frame"


def test_whitespace_and_case_do_not_make_a_line_new() -> None:
    """A re-rendered line is the same line; OCR spacing wobbles between frames."""
    seen = frame_text.SeenLines()

    frame_text.new_lines(frame_text.FrameText(lines=["Docker Run -p 8080"]), seen)
    again = frame_text.new_lines(frame_text.FrameText(lines=["docker  run -p  8080"]), seen)

    assert again == []


def test_a_frame_with_no_text_is_reported_as_read_not_as_unreadable() -> None:
    """Zero characters and "nobody looked" must not collapse into one value.

    A webcam shot reads zero. So does a machine with no OCR installed. The first
    is a fact about the picture and the second is a fact about the machine, and
    the index says different things about them.
    """
    read = frame_text.FrameText(lines=[])
    missing = frame_text.FrameText(lines=[], available=False)

    assert read.available and read.char_count == 0
    assert not missing.available


def test_a_row_is_whole_and_in_reading_order() -> None:
    """Neither clipped nor reordered, and the reason is measured.

    Clipping at 160 characters cut rows that average 460-760, and ranking the
    longest lines first to survive that clip made the *short* lines the
    casualties — a port number is four characters and is exactly what a note
    cites a picture for. Reading order is also the truthful one: on a slide, what
    comes first is the heading.
    """
    lines = ["3.1 中断不需要理由，「不对劲」就够", "Q+$", "8080", "一段比较长的说明文字放在同一帧上面"]

    summary = frame_text.summarise(lines)

    assert summary.startswith("3.1 中断不需要理由")
    assert "8080" in summary, "the shortest line is the one worth citing a picture for"
    assert all(line in summary for line in lines)


def test_the_budget_trims_the_longest_rows_and_drops_none() -> None:
    """A ceiling on spend, not a judgement about which frames matter.

    Every frame keeps a row saying something about itself; the text that goes is
    taken from the rows that had the most to spare.
    """
    texts = ["a" * 100, "b" * 5000, "c" * 20]

    fitted, trimmed = frame_text.fit_index(texts, limit=1000)

    assert trimmed is True
    assert sum(len(text) for text in fitted) <= 1000
    assert len(fitted) == 3 and fitted[0] == texts[0] and fitted[2] == texts[2]
    assert fitted[1].endswith("…"), "the long one gave, the short ones did not"


def test_an_index_inside_its_budget_is_left_alone() -> None:
    texts = ["short", "also short"]

    assert frame_text.fit_index(texts, limit=1000) == (texts, False)


def test_a_machine_without_ocr_says_so_without_asking_for_anything(monkeypatch) -> None:
    """The product works without this, so nobody is sent to install a thing."""
    monkeypatch.setattr(frame_text, "_vision", lambda: None)

    assert not frame_text.available()
    assert "退回" in str(frame_text.unavailable_reason())
    assert frame_text.read_frame(Path("/nowhere.jpg")).available is False


def test_an_unreadable_file_is_an_empty_read_not_a_crash(tmp_path) -> None:
    """One frame that will not open must not take the note down with it."""
    if not frame_text.available():
        pytest.skip("no local text recognition on this machine")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")

    assert frame_text.read_frame(broken).lines == []


@pytest.mark.skipif(not frame_text.available(), reason="local text recognition unavailable")
def test_real_text_is_read_off_a_generated_slide(tmp_path) -> None:
    """End to end against the framework itself, not a stub of it."""
    from PIL import Image, ImageDraw

    slide = tmp_path / "slide.png"
    image = Image.new("RGB", (1200, 400), "white")
    ImageDraw.Draw(image).text((60, 160), "docker run -p 8080:8080", fill="black")
    image.save(slide)

    text = frame_text.read_frame(slide).text

    assert "8080" in text, f"expected the port to survive OCR, read: {text!r}"


def test_a_line_the_recogniser_garbled_is_not_reported_as_new() -> None:
    """The measured reason the match is fuzzy rather than exact.

    The same sidebar comes back misread differently on every frame, so exact
    matching leaves the index carrying the same heading a dozen times.
    """
    seen = frame_text.SeenLines()
    frame_text.new_lines(
        frame_text.FrameText(lines=["如何监督你没做过的事一AI Coding的七项底层能力"]), seen)

    again = frame_text.new_lines(
        frame_text.FrameText(lines=["如們m音你没做过的事一AI Coding的七项底层能力"]), seen)

    assert again == []


def test_two_headings_that_merely_look_alike_both_survive() -> None:
    """The expensive failure the threshold is set to avoid.

    These two sit next to each other on one slide's contents page and score 0.71
    against each other. A looser threshold reaches the badly garbled lines this
    one misses, and deletes one of these to get there — which is a lesson missing
    from the note, not a saving.
    """
    seen = frame_text.SeenLines()
    frame_text.new_lines(
        frame_text.FrameText(lines=['1.1 不问"怎么做"，问"业界通常怎么做"']), seen)

    sibling = frame_text.new_lines(
        frame_text.FrameText(lines=['1.2 不问"怎么做"，问"什么情况下该做"']), seen)

    assert sibling == ['1.2 不问"怎么做"，问"什么情况下该做"']
