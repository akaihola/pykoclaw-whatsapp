"""Tests for WhatsApp attachment download and vision analysis."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip("neonize")

from pykoclaw_whatsapp.attachments import (
    VISION_MIMETYPES,
    _DEFAULT_VISION_MODEL,
    _GEMINI_BASE_URL,
    download_and_store,
    extract_image_mimetype,
    make_analyze_image_tool,
)


# ---------------------------------------------------------------------------
# extract_image_mimetype
# ---------------------------------------------------------------------------


def _make_event(field: str, mimetype: str = "image/jpeg") -> Mock:
    """Build a fake MessageEv with one field set."""
    event = Mock()
    wa_msg = Mock()
    event.Message = wa_msg

    def has_field(name: str) -> bool:
        return name == field

    wa_msg.HasField = has_field
    wa_msg.imageMessage.mimetype = mimetype
    wa_msg.stickerMessage.mimetype = "image/webp"
    return event


def test_extract_image_mimetype_jpeg() -> None:
    event = _make_event("imageMessage", "image/jpeg")
    assert extract_image_mimetype(event) == "image/jpeg"


def test_extract_image_mimetype_png() -> None:
    event = _make_event("imageMessage", "image/png")
    assert extract_image_mimetype(event) == "image/png"


def test_extract_image_mimetype_strips_charset() -> None:
    """Mimetype may carry a charset suffix like 'image/jpeg; charset=...'."""
    event = _make_event("imageMessage", "image/jpeg; charset=utf-8")
    assert extract_image_mimetype(event) == "image/jpeg"


def test_extract_image_mimetype_sticker() -> None:
    event = _make_event("stickerMessage")
    assert extract_image_mimetype(event) == "image/webp"


def test_extract_image_mimetype_text_returns_none() -> None:
    event = _make_event("conversation")
    assert extract_image_mimetype(event) is None


def test_extract_image_mimetype_video_returns_none() -> None:
    event = _make_event("videoMessage")
    assert extract_image_mimetype(event) is None


# ---------------------------------------------------------------------------
# download_and_store
# ---------------------------------------------------------------------------


def _make_image_event(mimetype: str = "image/jpeg", msg_id: str = "abc123") -> Mock:
    event = Mock()
    event.Info.ID = msg_id
    wa_msg = Mock()
    event.Message = wa_msg

    def has_field(name: str) -> bool:
        return name == "imageMessage"

    wa_msg.HasField = has_field
    wa_msg.imageMessage.mimetype = mimetype
    return event


def test_download_and_store_saves_file(tmp_path: Path) -> None:
    client = Mock()
    client.download_any.return_value = b"\xff\xd8\xff"  # minimal JPEG header

    event = _make_image_event("image/jpeg", "msg001")
    result = download_and_store(
        client, event, chat_jid="123@s.whatsapp.net", data_dir=tmp_path
    )

    assert result is not None
    mimetype, file_path = result
    assert mimetype == "image/jpeg"
    assert file_path.exists()
    assert file_path.suffix == ".jpg"
    assert file_path.read_bytes() == b"\xff\xd8\xff"


def test_download_and_store_returns_none_for_non_image(tmp_path: Path) -> None:
    event = Mock()
    event.Message.HasField = lambda _: False
    client = Mock()

    result = download_and_store(
        client, event, chat_jid="123@s.whatsapp.net", data_dir=tmp_path
    )
    assert result is None
    client.download_any.assert_not_called()


def test_download_and_store_handles_download_failure(tmp_path: Path) -> None:
    client = Mock()
    client.download_any.side_effect = RuntimeError("network error")

    event = _make_image_event()
    result = download_and_store(
        client, event, chat_jid="123@s.whatsapp.net", data_dir=tmp_path
    )
    assert result is None


def test_download_and_store_handles_none_bytes(tmp_path: Path) -> None:
    client = Mock()
    client.download_any.return_value = None

    event = _make_image_event()
    result = download_and_store(
        client, event, chat_jid="123@s.whatsapp.net", data_dir=tmp_path
    )
    assert result is None


def test_download_and_store_skips_existing_file(tmp_path: Path) -> None:
    """If the file already exists on disk, skip the download."""
    client = Mock()
    client.download_any.return_value = b"original"

    event = _make_image_event("image/jpeg", "msg_exists")
    # First download
    download_and_store(client, event, chat_jid="chat@s.whatsapp.net", data_dir=tmp_path)
    assert client.download_any.call_count == 1

    # Second call — should not re-download
    client.download_any.return_value = b"updated"
    result = download_and_store(
        client, event, chat_jid="chat@s.whatsapp.net", data_dir=tmp_path
    )
    assert client.download_any.call_count == 1  # still 1, not 2
    assert result is not None
    _, path = result
    assert path.read_bytes() == b"original"  # original content preserved


def test_download_and_store_creates_subdirs(tmp_path: Path) -> None:
    client = Mock()
    client.download_any.return_value = b"data"

    event = _make_image_event("image/png", "msg_png")
    result = download_and_store(
        client, event, chat_jid="group-id@g.us", data_dir=tmp_path
    )

    assert result is not None
    _, path = result
    assert (tmp_path / "wa_attachments" / "group-id@g.us").is_dir()


# ---------------------------------------------------------------------------
# make_analyze_image_tool (async)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    p = tmp_path / "test.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
    return p


@pytest.mark.asyncio
async def test_analyze_image_success(sample_image: Path) -> None:
    tool_fn = make_analyze_image_tool()

    gemini_response = {
        "candidates": [{"content": {"parts": [{"text": "A test image."}]}}]
    }

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = gemini_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            result = await tool_fn.handler(
                {"path": str(sample_image), "question": "What is this?"}
            )

    assert result["content"][0]["text"] == "A test image."

    # Verify the request used the right URL and model
    call_kwargs = mock_client.post.call_args
    url = call_kwargs[0][0]
    assert _GEMINI_BASE_URL in url
    assert _DEFAULT_VISION_MODEL in url
    assert "test-key" in url

    payload = call_kwargs[1]["json"]
    parts = payload["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[1]["text"] == "What is this?"
    assert base64.standard_b64decode(parts[0]["inline_data"]["data"])


@pytest.mark.asyncio
async def test_analyze_image_custom_model(sample_image: Path) -> None:
    tool_fn = make_analyze_image_tool()

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "key", "PYKOCLAW_WA_VISION_MODEL": "gemini-custom"},
        ):
            await tool_fn.handler({"path": str(sample_image)})

    url = mock_client.post.call_args[0][0]
    assert "gemini-custom" in url


@pytest.mark.asyncio
async def test_analyze_image_file_not_found(tmp_path: Path) -> None:
    tool_fn = make_analyze_image_tool()
    result = await tool_fn.handler({"path": str(tmp_path / "missing.jpg")})
    assert "not found" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_analyze_image_api_failure(sample_image: Path) -> None:
    tool_fn = make_analyze_image_tool()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "key"}):
            result = await tool_fn.handler({"path": str(sample_image)})

    assert "failed" in result["content"][0]["text"].lower()


def test_vision_mimetypes_coverage() -> None:
    """All expected formats are in VISION_MIMETYPES."""
    for fmt in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        assert fmt in VISION_MIMETYPES
