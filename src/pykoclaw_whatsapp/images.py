"""Detect and collect image references in agent responses.

Scans text for local image file paths and Markdown image URLs so channel
senders can attach images instead of leaking raw references into chat.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

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
# extension. Paths may be bare, wrapped in backticks, or in quotes.
# We capture the raw path.
IMAGE_PATH_RE = re.compile(
    r"(?:`|\"|\')?"  # optional opening backtick / quote
    r"(/[\w./_-]+)"  # absolute path
    r"(?:`|\"|\')?"  # optional closing backtick / quote
)

# Matches Markdown image syntax for remote HTTP(S) images.
IMAGE_URL_MD_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)]+)\)")


def detect_image_paths(text: str) -> list[Path]:
    """Find existing image file paths referenced in *text*.

    Only absolute paths that exist on disk and have a recognised image
    extension are returned. Duplicates are removed, order is preserved.
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


def mime_for_url(url: str) -> str:
    """Return the MIME type inferred from a URL path."""
    parsed = urlparse(url)
    mime, _ = mimetypes.guess_type(parsed.path)
    return mime or "application/octet-stream"
