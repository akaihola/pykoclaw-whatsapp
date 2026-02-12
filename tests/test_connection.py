"""Tests for WhatsApp connection — session resumption, reply suppression, system prompt."""

from __future__ import annotations

import sqlite3
from textwrap import dedent
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip("neonize")

from pykoclaw.models import Conversation
from pykoclaw_whatsapp.connection import WhatsAppConnection


@pytest.fixture
def db() -> sqlite3.Connection:
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
            );
            CREATE TABLE conversations (
                name TEXT PRIMARY KEY,
                session_id TEXT,
                cwd TEXT,
                created_at TEXT NOT NULL
            );""")
    )
    return db


@pytest.fixture
def connection(db: sqlite3.Connection) -> WhatsAppConnection:
    from pykoclaw_whatsapp.config import WhatsAppSettings

    config = WhatsAppSettings(trigger_name="Andy")
    conn = WhatsAppConnection(db=db, config=config)
    conn._client = Mock()
    return conn


def _seed_messages(db: sqlite3.Connection, chat_jid: str, count: int = 1) -> None:
    for i in range(count):
        db.execute(
            dedent("""\
                INSERT INTO wa_messages (chat_jid, sender, text, timestamp, is_from_me)
                VALUES (?, ?, ?, ?, 0)"""),
            (chat_jid, f"User{i}", f"Message {i}", f"2024-01-01T12:0{i}:00Z"),
        )
        db.execute(
            dedent("""\
                INSERT OR IGNORE INTO wa_chats (jid, last_timestamp)
                VALUES (?, ?)"""),
            (chat_jid, f"2024-01-01T12:0{i}:00Z"),
        )
    db.commit()


async def _fake_agent_text(*_args: object, **kwargs: object):
    from pykoclaw.agent_core import AgentMessage

    yield AgentMessage(type="text", text="Hello from agent")
    yield AgentMessage(type="result", session_id="ses_new")


async def _fake_agent_result_only(*_args: object, **kwargs: object):
    from pykoclaw.agent_core import AgentMessage

    yield AgentMessage(type="result", session_id="ses_new")


async def _fake_agent_empty_text(*_args: object, **kwargs: object):
    from pykoclaw.agent_core import AgentMessage

    yield AgentMessage(type="text", text="")
    yield AgentMessage(type="result", session_id="ses_new")


@pytest.mark.asyncio
async def test_session_resumption(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)
    db.execute(
        "INSERT INTO conversations (name, session_id, cwd, created_at) VALUES (?, ?, ?, ?)",
        (f"wa-{chat_jid}", "ses_abc", "/tmp", "2024-01-01"),
    )
    db.commit()

    with patch(
        "pykoclaw_whatsapp.connection.query_agent", side_effect=_fake_agent_text
    ) as mock_query:
        await connection._handle_agent_trigger(chat_jid)

        call_kwargs = mock_query.call_args[1]
        assert call_kwargs["resume_session_id"] == "ses_abc"


@pytest.mark.asyncio
async def test_session_resumption_no_existing(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "456@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    with patch(
        "pykoclaw_whatsapp.connection.query_agent", side_effect=_fake_agent_text
    ) as mock_query:
        await connection._handle_agent_trigger(chat_jid)

        call_kwargs = mock_query.call_args[1]
        assert call_kwargs["resume_session_id"] is None


@pytest.mark.asyncio
async def test_reply_suppression_no_text(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    with patch(
        "pykoclaw_whatsapp.connection.query_agent",
        side_effect=_fake_agent_result_only,
    ):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_not_called()


@pytest.mark.asyncio
async def test_reply_suppression_empty_text(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    with patch(
        "pykoclaw_whatsapp.connection.query_agent",
        side_effect=_fake_agent_empty_text,
    ):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_not_called()


@pytest.mark.asyncio
async def test_reply_sent_with_text(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    with patch(
        "pykoclaw_whatsapp.connection.query_agent", side_effect=_fake_agent_text
    ):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_called_once()
        sent_text = connection._outgoing_queue.send.call_args[0][2]
        assert "Hello from agent" in sent_text


@pytest.mark.asyncio
async def test_hard_mention_system_prompt(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    with patch(
        "pykoclaw_whatsapp.connection.query_agent", side_effect=_fake_agent_text
    ) as mock_query:
        await connection._handle_agent_trigger(chat_jid, hard_mention=True)

        call_kwargs = mock_query.call_args[1]
        assert "MUST reply" in call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_ambient_system_prompt(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    with patch(
        "pykoclaw_whatsapp.connection.query_agent", side_effect=_fake_agent_text
    ) as mock_query:
        await connection._handle_agent_trigger(chat_jid, hard_mention=False)

        call_kwargs = mock_query.call_args[1]
        assert "silence" in call_kwargs["system_prompt"].lower()
        assert "MUST reply" not in call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_system_prompt_includes_trigger_name(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    with patch(
        "pykoclaw_whatsapp.connection.query_agent", side_effect=_fake_agent_text
    ) as mock_query:
        await connection._handle_agent_trigger(chat_jid)

        call_kwargs = mock_query.call_args[1]
        assert "Andy" in call_kwargs["system_prompt"]
