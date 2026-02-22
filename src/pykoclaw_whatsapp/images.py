"""Detect and collect image file references in agent responses.

Scans text for file paths ending in common image extensions, checks that
the files exist on disk, and returns them for upload to Matrix.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Image extensions we support uploading.
IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".tiff",
    }
)

# Matches absolute file paths (starting with /) that end in an image
# extension.  Paths may be bare, wrapped in backticks, or in quotes.
# We capture the raw path.
IMAGE_PATH_RE = re.compile(
    r"(?:`|\"|\')?"  # optional opening backtick / quote
    r"(/[\w./_-]+)"  # absolute path (letters, digits, dots, slashes, hyphens, underscores)
    r"(?:`|\"|\')?"  # optional closing backtick / quote
)


def detect_image_paths(text: str) -> list[Path]:
    """Find existing image file paths referenced in *text*.

    Only absolute paths that exist on disk and have a recognised image
    extension are returned.  Duplicates are removed, order is preserved.
    """
    seen: set[str] = set()
    result: list[Path] = []
    for m in IMAGE_PATH_RE.finditer(text):
        raw = m.group(1)
        if raw in seen:
            continue
        seen.add(raw)
        p = Path(raw)
        if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file():
            log.info("Detected image file: %s", p)
            result.append(p)
    return result


def mime_for_path(path: Path) -> str:
    """Return the MIME type for an image path."""
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"
