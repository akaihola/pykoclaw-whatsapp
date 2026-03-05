"""Tests for WhatsApp image path detection utilities."""

from __future__ import annotations

from pathlib import Path

from pykoclaw_whatsapp.images import detect_image_paths, mime_for_path


def test_detect_no_paths() -> None:
    assert detect_image_paths("No paths here") == []


def test_detect_existing_image(tmp_path: Path) -> None:
    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")
    assert detect_image_paths(f"Here is your chart: {img}") == [img]


def test_detect_ignores_nonexistent() -> None:
    paths = detect_image_paths("See /tmp/nonexistent_xyz12345.png for details")
    assert paths == []


def test_detect_ignores_non_image_extension(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_bytes(b"text")
    assert detect_image_paths(f"File: {txt}") == []


def test_detect_multiple_images(tmp_path: Path) -> None:
    a = tmp_path / "a.png"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"PNG")
    b.write_bytes(b"JPEG")
    assert detect_image_paths(f"First: {a}\nSecond: {b}") == [a, b]


def test_detect_deduplicates(tmp_path: Path) -> None:
    img = tmp_path / "chart.png"
    img.write_bytes(b"PNG")
    assert detect_image_paths(f"{img} and again {img}") == [img]


def test_detect_various_extensions(tmp_path: Path) -> None:
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        f = tmp_path / f"img{ext}"
        f.write_bytes(b"data")
        assert detect_image_paths(str(f)) == [f], f"Expected {ext} to be detected"
        f.unlink()


def test_mime_for_path_png() -> None:
    assert mime_for_path(Path("image.png")) == "image/png"


def test_mime_for_path_jpeg() -> None:
    assert mime_for_path(Path("photo.jpg")) in ("image/jpeg", "image/jpg")


def test_mime_for_path_webp() -> None:
    assert mime_for_path(Path("anim.webp")) == "image/webp"


def test_mime_for_path_unknown_falls_back() -> None:
    assert mime_for_path(Path("file.bin")) == "application/octet-stream"
