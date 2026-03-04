"""Image attachment download and storage for WhatsApp.

Downloads WhatsApp media messages to disk using ``client.download_any()``
and stores them in ``{data_dir}/wa_attachments/{chat_jid}/{msg_id}.{ext}``.

Vision analysis (the ``analyze_image`` MCP tool) lives in
``pykoclaw-vision`` and is imported from there.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

# MIME types supported for vision analysis.
VISION_MIMETYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)

_EXT_MAP: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def extract_image_mimetype(event: MessageEv) -> str | None:
    """Return the MIME type if *event* carries an image, else ``None``.

    Only considers ``imageMessage`` and ``stickerMessage`` (stickers are
    WebP images).  Other media types (video, audio, document) are not
    included in Phase 1.
    """
    wa_msg = event.Message
    if wa_msg.HasField("imageMessage"):
        mime = wa_msg.imageMessage.mimetype or "image/jpeg"
        return mime.split(";")[0].strip()  # strip charset suffix if any
    if wa_msg.HasField("stickerMessage"):
        return "image/webp"
    return None


def download_and_store(
    client: NewClient,
    event: MessageEv,
    *,
    chat_jid: str,
    data_dir: Path,
) -> tuple[str, Path] | None:
    """Download a WhatsApp media message and save it to disk.

    Args:
        client: The active Neonize client.
        event: The full ``MessageEv`` (= ``neonize.proto.Neonize_pb2.Message``).
        chat_jid: The chat JID string, used for the storage sub-directory.
        data_dir: pykoclaw data directory (``settings.data``).

    Returns:
        ``(mimetype, file_path)`` on success, ``None`` on failure.
    """
    mimetype = extract_image_mimetype(event)
    if mimetype is None:
        return None

    msg_id = event.Info.ID or "unknown"
    ext = _EXT_MAP.get(mimetype, "bin")

    attachments_dir = data_dir / "wa_attachments" / chat_jid
    attachments_dir.mkdir(parents=True, exist_ok=True)
    file_path = attachments_dir / f"{msg_id}.{ext}"

    if file_path.exists():
        log.debug("Attachment already on disk: %s", file_path)
        return mimetype, file_path

    try:
        data = client.download_any(event.Message)
    except Exception:
        log.exception("download_any failed for message %s in chat %s", msg_id, chat_jid)
        return None

    if data is None:
        log.warning(
            "download_any returned None for message %s in chat %s", msg_id, chat_jid
        )
        return None

    try:
        file_path.write_bytes(data)
    except OSError:
        log.exception("Failed to write attachment to %s", file_path)
        return None

    log.info("Saved attachment: %s (%d bytes)", file_path, len(data))
    return mimetype, file_path
