"""Tests for WhatsApp message handler."""

from __future__ import annotations

import asyncio
import sqlite3
from textwrap import dedent
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("neonize")

from pykoclaw_whatsapp.handler import (
    BatchAccumulator,
    MessageHandler,
    extract_text,
    format_xml_message,
    format_xml_messages,
    get_new_messages_for_chat,
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


def _make_handler(
    db: sqlite3.Connection,
    trigger_name: str = "Andy",
    self_jid: str | None = None,
) -> tuple[MessageHandler, Mock]:
    from neonize.utils.jid import Jid2String

    loop = Mock(spec=asyncio.AbstractEventLoop)
    loop.call_later = Mock(return_value=Mock())

    batch_acc = Mock(spec=BatchAccumulator)
    batch_acc.add = Mock()
    batch_acc.flush_now = AsyncMock()

    future = Mock()
    loop_run = Mock(return_value=future)

    handler = MessageHandler(
        db=db,
        outgoing_queue=Mock(),
        trigger_name=trigger_name,
        loop=loop,
        batch_accumulator=batch_acc,
    )
    if self_jid:
        handler.set_self_jid(self_jid)

    return handler, batch_acc


def _make_message_event(
    chat_jid: str,
    text: str,
    sender: str = "User",
    is_from_me: bool = False,
    is_group: bool = False,
    timestamp_ms: int = 1704067200000,
) -> Mock:
    from neonize.utils.jid import build_jid

    mock_event = Mock()

    user_part, server_part = chat_jid.split("@", 1)
    chat_jid_obj = build_jid(user_part, server_part)

    sender_user = sender.replace(" ", "")
    sender_jid_obj = build_jid(sender_user, "s.whatsapp.net")

    mock_event.Info.MessageSource.Chat = chat_jid_obj
    mock_event.Info.MessageSource.Sender = sender_jid_obj
    mock_event.Info.MessageSource.IsFromMe = is_from_me
    mock_event.Info.MessageSource.IsGroup = is_group
    mock_event.Info.Timestamp = timestamp_ms
    mock_event.Info.Pushname = sender

    mock_event.Message.HasField = lambda f: f == "conversation"
    mock_event.Message.conversation = text

    return mock_event


def test_non_mention_enters_batch(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    event = _make_message_event("123@s.whatsapp.net", "Hello everyone")
    handler.on_message(client, event)

    batch_acc.add.assert_called_once_with("123@s.whatsapp.net")
    batch_acc.flush_now.assert_not_called()


def test_hard_mention_flushes(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    event = _make_message_event("123@s.whatsapp.net", "Hey @Andy what do you think?")
    handler.on_message(client, event)

    batch_acc.add.assert_not_called()


def test_hard_mention_case_insensitive(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    event = _make_message_event("123@s.whatsapp.net", "hey @andy check this out")
    handler.on_message(client, event)

    batch_acc.add.assert_not_called()


def test_hard_mention_name_at_start(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    for text in [
        "Andy what do you think?",
        "Andy, check this out",
        "andy: look at this",
        "Andy! is this right?",
    ]:
        batch_acc.reset_mock()
        event = _make_message_event("123@s.whatsapp.net", text)
        handler.on_message(client, event)
        batch_acc.add.assert_not_called(), f"Expected flush for: {text!r}"


def test_hard_mention_name_after_full_stop(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    for text in [
        "Something happened. Andy what do you think?",
        "Ok cool. Andy, check this",
    ]:
        batch_acc.reset_mock()
        event = _make_message_event("123@s.whatsapp.net", text)
        handler.on_message(client, event)
        batch_acc.add.assert_not_called(), f"Expected flush for: {text!r}"


def test_name_mid_sentence_is_soft(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    for text in [
        "I told Andy about it yesterday",
        "What Andy said was interesting",
    ]:
        batch_acc.reset_mock()
        event = _make_message_event("123@s.whatsapp.net", text)
        handler.on_message(client, event)
        batch_acc.add.assert_called_once(), f"Expected batch for: {text!r}"


def test_self_chat_immediate(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db, self_jid="555@s.whatsapp.net")
    client = Mock()

    event = _make_message_event(
        "555@s.whatsapp.net", "Talking to myself", is_group=False
    )
    handler.on_message(client, event)

    batch_acc.add.assert_not_called()


def test_is_from_me_skipped(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    event = _make_message_event("123@s.whatsapp.net", "My own message", is_from_me=True)
    handler.on_message(client, event)

    batch_acc.add.assert_not_called()
    batch_acc.flush_now.assert_not_called()


def test_is_hard_mention_unit() -> None:
    from pykoclaw_whatsapp.handler import _is_hard_mention

    assert _is_hard_mention("@Andy", "Andy")
    assert _is_hard_mention("hey @andy!", "Andy")
    assert _is_hard_mention("Andy what?", "Andy")
    assert _is_hard_mention("Andy, hi", "Andy")
    assert _is_hard_mention("andy: yo", "Andy")
    assert _is_hard_mention("Ok. Andy check this", "Andy")
    assert not _is_hard_mention("I told Andy about it", "Andy")
    assert not _is_hard_mention("Andyman is here", "Andy")


def test_status_broadcast_skipped(db: sqlite3.Connection) -> None:
    handler, batch_acc = _make_handler(db)
    client = Mock()

    event = _make_message_event("status@broadcast", "Status update")
    handler.on_message(client, event)

    batch_acc.add.assert_not_called()
    batch_acc.flush_now.assert_not_called()
