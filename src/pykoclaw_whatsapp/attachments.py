"""Image attachment download, storage, and vision analysis for WhatsApp.

Downloads WhatsApp media messages to disk using ``client.download_any()``,
stores them in ``{data_dir}/wa_attachments/{chat_jid}/{msg_id}.{ext}``,
and provides an MCP ``analyze_image`` tool that calls the Gemini vision API
via ``httpx`` (uses env ``GEMINI_API_KEY``; model defaults to
``gemini-3.1-flash-lite-preview``, overridable via
``PYKOCLAW_WA_VISION_MODEL``).
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

# Formats Claude vision can analyse.
VISION_MIMETYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)

_EXT_MAP: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

_MEDIA_TYPE_MAP: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}

# Default model for vision analysis — overridable via PYKOCLAW_WA_VISION_MODEL.
_DEFAULT_VISION_MODEL = "gemini-3.1-flash-lite-preview"

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"


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


def make_analyze_image_tool():
    """Create the ``analyze_image`` MCP tool function.

    The tool reads an image file from disk, base64-encodes it, and sends it
    to the Gemini API using ``httpx``.  It reads ``GEMINI_API_KEY`` from the
    environment and honours ``PYKOCLAW_WA_VISION_MODEL`` to override the
    default model (``gemini-3.1-flash-lite-preview``).
    """
    from textwrap import dedent

    from claude_agent_sdk import tool

    @tool(
        "analyze_image",
        dedent("""\
            Analyze an image using Claude's vision capabilities.
            Call this tool when you receive a message containing an
            <attachment type="image" path="..." /> element.
            Pass the path exactly as it appears in the attachment tag.
            Returns a text description or answer to your question."""),
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the image file on disk.",
                },
                "question": {
                    "type": "string",
                    "description": (
                        "Question or instruction about the image. "
                        "Defaults to a general description request."
                    ),
                },
            },
            "required": ["path"],
        },
    )
    async def analyze_image(args: dict) -> dict:
        import httpx

        path = Path(args["path"])
        question = args.get("question", "Describe this image in detail.")

        if not path.exists():
            return {"content": [{"type": "text", "text": f"File not found: {path}"}]}

        suffix = path.suffix.lower().lstrip(".")
        media_type = _MEDIA_TYPE_MAP.get(suffix, "image/jpeg")

        try:
            raw = path.read_bytes()
        except OSError as exc:
            return {"content": [{"type": "text", "text": f"Cannot read image: {exc}"}]}

        image_data = base64.standard_b64encode(raw).decode()

        api_key = os.environ.get("GEMINI_API_KEY", "")
        vision_model = os.environ.get("PYKOCLAW_WA_VISION_MODEL", _DEFAULT_VISION_MODEL)

        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": media_type, "data": image_data}},
                        {"text": question},
                    ]
                }
            ]
        }

        url = (
            f"{_GEMINI_BASE_URL}/v1beta/models/{vision_model}"
            f":generateContent?key={api_key}"
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.post(
                    url,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            resp.raise_for_status()
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text")
                or "No description returned."
            )
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:
            log.exception("Vision API call failed for %s", path)
            return {
                "content": [{"type": "text", "text": f"Image analysis failed: {exc}"}]
            }

    return analyze_image
