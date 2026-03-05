"""Tests for WhatsApp message segment splitting."""

from __future__ import annotations

from pathlib import Path

from pykoclaw_whatsapp.segments import ImageSegment, TextSegment, split_segments


def test_plain_text() -> None:
    assert split_segments("Hello, world!") == [TextSegment("Hello, world!")]


def test_empty_string() -> None:
    assert split_segments("") == []


def test_whitespace_only() -> None:
    assert split_segments("   \n  ") == []


def test_image_in_middle(tmp_path: Path) -> None:
    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")
    segs = split_segments(f"Here is the chart:\n{img}\nEnjoy!")
    assert len(segs) == 3
    assert isinstance(segs[0], TextSegment)
    assert "Here is the chart" in segs[0].text
    assert isinstance(segs[1], ImageSegment)
    assert segs[1].ref.source == str(img)
    assert segs[1].ref.kind == "file"
    assert isinstance(segs[2], TextSegment)
    assert "Enjoy!" in segs[2].text


def test_image_at_start(tmp_path: Path) -> None:
    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")
    segs = split_segments(f"{img}\nCaption after")
    assert isinstance(segs[0], ImageSegment)
    assert isinstance(segs[1], TextSegment)


def test_image_at_end(tmp_path: Path) -> None:
    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")
    segs = split_segments(f"Caption before:\n{img}")
    assert isinstance(segs[0], TextSegment)
    assert isinstance(segs[1], ImageSegment)


def test_multiple_images_ordered(tmp_path: Path) -> None:
    a = tmp_path / "a.png"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"PNG")
    b.write_bytes(b"JPEG")
    segs = split_segments(f"First: {a}\nSecond: {b}")
    image_segs = [s for s in segs if isinstance(s, ImageSegment)]
    assert len(image_segs) == 2
    assert image_segs[0].ref.source == str(a)
    assert image_segs[1].ref.source == str(b)


def test_nonexistent_path_stays_in_text() -> None:
    text = "See /tmp/nonexistent_xyz12345.png for details"
    segs = split_segments(text)
    assert len(segs) == 1
    assert isinstance(segs[0], TextSegment)
    assert "/tmp/nonexistent_xyz12345.png" in segs[0].text


def test_excess_newlines_collapsed() -> None:
    segs = split_segments("First\n\n\n\n\nSecond")
    assert len(segs) == 1
    assert "\n\n\n" not in segs[0].text


def test_image_only(tmp_path: Path) -> None:
    img = tmp_path / "solo.png"
    img.write_bytes(b"PNG")
    segs = split_segments(str(img))
    assert segs == [ImageSegment(ref=segs[0].ref)]  # type: ignore[union-attr]
    assert isinstance(segs[0], ImageSegment)
