"""Tests for WhatsApp message handler."""

from __future__ import annotations

import sqlite3
from textwrap import dedent
from unittest.mock import Mock

import pytest

pytest.importorskip("neonize")

from pykoclaw_whatsapp.handler import (
    extract_text,
    format_xml_message,
    format_xml_messages,
    get_new_messages_for_chat,
    should_trigger,
    store_message,
    update_agent_cursor,
    update_chat_timestamp,
    update_global_cursor,
)


@pytest.fixture
def db() -> sqlite3.Connection:
    """Create in-memory database with WhatsApp tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row

    db.executescript(
        dedent("""\
            CREATE TABLE wa_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_jid TEXT NOT NULL,
                sender TEXT,
                text TEXT,
                timestamp TEXT NOT NULL,
                is_from_me INTEGER DEFAULT 0
            );
            CREATE TABLE wa_chats (
                jid TEXT PRIMARY KEY,
                name TEXT,
                last_timestamp TEXT,
                last_agent_timestamp TEXT
            );
            CREATE TABLE wa_config (
                key TEXT PRIMARY KEY,
                value TEXT
            );""")
    )
    return db


def test_format_xml_message() -> None:
    """Test XML message formatting."""
    result = format_xml_message("Alice", "2024-01-01T12:00:00Z", "Hello world")
    assert '<message sender="Alice"' in result
    assert 'time="2024-01-01T12:00:00Z"' in result
    assert ">Hello world</message>" in result


def test_format_xml_message_escapes_html() -> None:
    """Test that XML formatting escapes HTML entities."""
    result = format_xml_message("Bob", "2024-01-01", "<script>alert('xss')</script>")
    assert "&lt;script&gt;" in result
    assert "&lt;/script&gt;" in result
    assert "<script>" not in result


def test_format_xml_messages() -> None:
    """Test formatting multiple messages as XML block."""
    messages = [
        ("Alice", "2024-01-01T12:00:00Z", "Hello"),
        ("Bob", "2024-01-01T12:01:00Z", "Hi there"),
    ]
    result = format_xml_messages(messages)

    assert result.startswith("<messages>")
    assert result.endswith("</messages>")
    assert "Alice" in result
    assert "Bob" in result
    assert "Hello" in result
    assert "Hi there" in result


def test_store_message(db: sqlite3.Connection) -> None:
    """Test storing a message in the database."""
    store_message(
        db,
        chat_jid="123@s.whatsapp.net",
        sender="Alice",
        text="Test message",
        timestamp="2024-01-01T12:00:00Z",
        is_from_me=False,
    )

    rows = db.execute("SELECT * FROM wa_messages").fetchall()
    assert len(rows) == 1
    assert rows[0]["chat_jid"] == "123@s.whatsapp.net"
    assert rows[0]["sender"] == "Alice"
    assert rows[0]["text"] == "Test message"
    assert rows[0]["is_from_me"] == 0


def test_update_chat_timestamp(db: sqlite3.Connection) -> None:
    """Test updating chat timestamp."""
    update_chat_timestamp(db, "123@s.whatsapp.net", "2024-01-01T12:00:00Z")

    row = db.execute(
        "SELECT last_timestamp FROM wa_chats WHERE jid = ?", ("123@s.whatsapp.net",)
    ).fetchone()
    assert row["last_timestamp"] == "2024-01-01T12:00:00Z"


def test_update_global_cursor(db: sqlite3.Connection) -> None:
    """Test updating global timestamp cursor."""
    update_global_cursor(db, "2024-01-01T12:00:00Z")

    row = db.execute(
        "SELECT value FROM wa_config WHERE key = 'last_timestamp'"
    ).fetchone()
    assert row["value"] == "2024-01-01T12:00:00Z"


def test_update_agent_cursor(db: sqlite3.Connection) -> None:
    """Test updating per-chat agent timestamp cursor."""
    update_agent_cursor(db, "123@s.whatsapp.net", "2024-01-01T12:00:00Z")

    row = db.execute(
        "SELECT last_agent_timestamp FROM wa_chats WHERE jid = ?",
        ("123@s.whatsapp.net",),
    ).fetchone()
    assert row["last_agent_timestamp"] == "2024-01-01T12:00:00Z"


def test_get_new_messages_for_chat(db: sqlite3.Connection) -> None:
    """Test retrieving new messages for a chat."""
    store_message(
        db, "123@s.whatsapp.net", "Alice", "Message 1", "2024-01-01T12:00:00Z", False
    )
    store_message(
        db, "123@s.whatsapp.net", "Bob", "Message 2", "2024-01-01T12:01:00Z", False
    )
    store_message(
        db, "123@s.whatsapp.net", "Alice", "Message 3", "2024-01-01T12:02:00Z", False
    )

    update_agent_cursor(db, "123@s.whatsapp.net", "2024-01-01T12:00:30Z")

    messages = get_new_messages_for_chat(db, "123@s.whatsapp.net")

    assert len(messages) == 2
    assert messages[0][0] == "Bob"
    assert messages[0][2] == "Message 2"
    assert messages[1][0] == "Alice"
    assert messages[1][2] == "Message 3"


def test_should_trigger_with_mention() -> None:
    """Test trigger detection with @mention."""
    assert should_trigger("Hello @Andy how are you?", "Andy", False)
    assert not should_trigger("Hello there", "Andy", False)


def test_should_trigger_self_chat() -> None:
    """Test that self-chat always triggers."""
    assert should_trigger("Any message", "Andy", True)
    assert should_trigger("No mention here", "Andy", True)


def test_extract_text_from_conversation() -> None:
    """Test extracting text from conversation message."""
    mock_msg = Mock()
    mock_msg.Message.HasField = lambda f: f == "conversation"
    mock_msg.Message.conversation = "Hello world"

    result = extract_text(mock_msg)
    assert result == "Hello world"


def test_extract_text_from_extended_text() -> None:
    """Test extracting text from extended text message."""
    mock_msg = Mock()
    mock_msg.Message.HasField = lambda f: f == "extendedTextMessage"
    mock_msg.Message.extendedTextMessage.text = "Extended message"

    result = extract_text(mock_msg)
    assert result == "Extended message"


def test_extract_text_returns_none_for_unsupported() -> None:
    """Test that extract_text returns None for unsupported message types."""
    mock_msg = Mock()
    mock_msg.Message.HasField = lambda f: False

    result = extract_text(mock_msg)
    assert result is None
