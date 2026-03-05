"""Split agent text into interleaved text and image segments.

Walks the text linearly, identifying image file paths in document order,
and yields ``TextSegment`` or ``ImageSegment`` objects so the caller can
send them as separate WhatsApp messages in the correct order.

Note: Mermaid diagram rendering is not supported on WhatsApp (unlike Matrix).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .images import IMAGE_EXTENSIONS, IMAGE_PATH_RE

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImageRef:
    """A reference to an image that should be sent as a WhatsApp image message."""

    kind: Literal["file"]
    """The source type — always ``'file'`` for WhatsApp."""

    source: str
    """The absolute file path."""


@dataclass(frozen=True, slots=True)
class TextSegment:
    """A plain-text segment to send as a text message."""

    text: str


@dataclass(frozen=True, slots=True)
class ImageSegment:
    """An image segment to read from disk and send as a WhatsApp image message."""

    ref: ImageRef


Segment = TextSegment | ImageSegment


def split_segments(text: str) -> list[Segment]:
    """Split *text* into an ordered list of text and image segments.

    Absolute image file paths are detected in document order.  Text between
    them becomes ``TextSegment`` entries.  Empty text segments (only
    whitespace) are dropped.  Paths that do not exist on disk are left
    as-is inside the surrounding text.
    """
    markers: list[tuple[int, int, ImageRef]] = []

    for m in IMAGE_PATH_RE.finditer(text):
        raw = m.group(1)
        p = Path(raw)
        if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file():
            markers.append((m.start(), m.end(), ImageRef("file", raw)))

    segments: list[Segment] = []
    pos = 0
    for start, end, ref in markers:
        _maybe_add_text(segments, text[pos:start])
        segments.append(ImageSegment(ref))
        pos = end

    _maybe_add_text(segments, text[pos:])
    return segments


def _maybe_add_text(segments: list[Segment], chunk: str) -> None:
    """Append a ``TextSegment`` if *chunk* has non-whitespace content."""
    cleaned = re.sub(r"\n{3,}", "\n\n", chunk).strip()
    if cleaned:
        segments.append(TextSegment(cleaned))
