"""Tests for WhatsApp connection — session resumption, reply suppression, system prompt."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip("neonize")

from pykoclaw_messaging.dispatch import DispatchResult
from pykoclaw_whatsapp.connection import WhatsAppConnection

MOCK_TARGET = "pykoclaw_whatsapp.connection.dispatch_to_agent"


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
def connection(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> WhatsAppConnection:
    from pykoclaw_whatsapp.config import WhatsAppSettings

    monkeypatch.chdir(tmp_path)

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


def _make_result(text: str = "", session_id: str = "ses_new") -> DispatchResult:
    return DispatchResult(full_text=text, session_id=session_id)


@pytest.mark.asyncio
async def test_session_resumption(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)
    db.execute(
        "INSERT INTO conversations (name, session_id, cwd, created_at) VALUES (?, ?, ?, ?)",
        ("wa-andy-123@s.whatsapp.net", "ses_abc", "/tmp", "2024-01-01"),
    )
    db.commit()

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hi</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        await connection._handle_agent_trigger(chat_jid)

    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["channel_prefix"] == "wa-andy"
    assert call_kwargs["channel_id"] == chat_jid


@pytest.mark.asyncio
async def test_session_resumption_no_existing(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "456@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hi</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        await connection._handle_agent_trigger(chat_jid)

    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["channel_prefix"] == "wa-andy"
    assert call_kwargs["channel_id"] == chat_jid


@pytest.mark.asyncio
async def test_reply_suppression_no_text(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result(""))
    with patch(MOCK_TARGET, mock_dispatch):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_not_called()


@pytest.mark.asyncio
async def test_reply_suppression_empty_text(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result(""))
    with patch(MOCK_TARGET, mock_dispatch):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_not_called()


@pytest.mark.asyncio
async def test_reply_sent_with_text(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(
        return_value=_make_result("<reply>Hello from agent</reply>")
    )
    with patch(MOCK_TARGET, mock_dispatch):
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

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hi</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        await connection._handle_agent_trigger(chat_jid, hard_mention=True)

    call_kwargs = mock_dispatch.call_args.kwargs
    assert "MUST reply" in call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_ambient_system_prompt(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hi</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        await connection._handle_agent_trigger(chat_jid, hard_mention=False)

    call_kwargs = mock_dispatch.call_args.kwargs
    assert "silence" in call_kwargs["system_prompt"].lower()
    assert "MUST reply" not in call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_system_prompt_includes_trigger_name(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hi</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        await connection._handle_agent_trigger(chat_jid)

    call_kwargs = mock_dispatch.call_args.kwargs
    assert "Andy" in call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_monologue_filtered(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(
        return_value=_make_result("This is internal monologue without tags")
    )
    with patch(MOCK_TARGET, mock_dispatch):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_not_called()


@pytest.mark.asyncio
async def test_reply_tags_extracted(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(
        return_value=_make_result("<reply>Hello from agent</reply>")
    )
    with patch(MOCK_TARGET, mock_dispatch):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_called_once()
        sent_text = connection._outgoing_queue.send.call_args[0][2]
        assert "Hello from agent" in sent_text
        assert "<reply>" not in sent_text


@pytest.mark.asyncio
async def test_multiple_reply_tags(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(
        return_value=_make_result(
            "<reply>First reply</reply>\n<reply>Second reply</reply>"
        )
    )
    with patch(MOCK_TARGET, mock_dispatch):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_called_once()
        sent_text = connection._outgoing_queue.send.call_args[0][2]
        assert "First reply" in sent_text
        assert "Second reply" in sent_text


@pytest.mark.asyncio
async def test_whitespace_only_reply_tag(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>   \n  \t  </reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_not_called()


@pytest.mark.asyncio
async def test_reply_with_newlines(
    db: sqlite3.Connection, connection: WhatsAppConnection
) -> None:
    chat_jid = "123@s.whatsapp.net"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(
        return_value=_make_result("<reply>Line 1\nLine 2\nLine 3</reply>")
    )
    with patch(MOCK_TARGET, mock_dispatch):
        connection._outgoing_queue = Mock()
        await connection._handle_agent_trigger(chat_jid)

        connection._outgoing_queue.send.assert_called_once()
        sent_text = connection._outgoing_queue.send.call_args[0][2]
        assert "Line 1" in sent_text
        assert "Line 2" in sent_text
        assert "Line 3" in sent_text


@pytest.mark.asyncio
async def test_extract_reply_unit() -> None:
    from pykoclaw_whatsapp.connection import _extract_reply

    assert _extract_reply("plain text") is None

    assert _extract_reply("<reply>Hello</reply>") == "Hello"

    result = _extract_reply("<reply>First</reply>\n<reply>Second</reply>")
    assert result == "First\nSecond"

    assert _extract_reply("<reply>   \n  </reply>") is None

    result = _extract_reply("<reply>Line 1\nLine 2</reply>")
    assert "Line 1" in result
    assert "Line 2" in result

    result = _extract_reply("Reasoning\n<reply>Answer</reply>\nMore reasoning")
    assert result == "Answer"
    assert "Reasoning" not in result


# --- Multi-agent routing tests ---


@pytest.fixture
def multi_agent_connection(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> WhatsAppConnection:
    """Connection with two agents: Ressu (default) and Tyko, both in group-multi."""
    from pykoclaw_whatsapp.config import WhatsAppSettings
    from pykoclaw_whatsapp.routing import AgentConfig, RoutingConfig

    monkeypatch.chdir(tmp_path)

    routing = RoutingConfig(
        default_agent="Ressu",
        agents={
            "Ressu": AgentConfig(name="Ressu"),
            "Tyko": AgentConfig(name="Tyko", model="claude-opus-4-6"),
        },
        routes={
            "group-multi@g.us": ["Ressu", "Tyko"],
            "group-tyko@g.us": ["Tyko"],
        },
    )

    config = WhatsAppSettings(trigger_name="Ressu")
    conn = WhatsAppConnection(db=db, config=config, routing=routing)
    conn._client = Mock()
    return conn


@pytest.mark.asyncio
async def test_multi_agent_dispatches_to_both(
    db: sqlite3.Connection, multi_agent_connection: WhatsAppConnection
) -> None:
    """Multi-agent group dispatches to each agent sequentially."""
    chat_jid = "group-multi@g.us"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hi</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        await multi_agent_connection._handle_agent_trigger(chat_jid)

    assert mock_dispatch.call_count == 2
    prefixes = [c.kwargs["channel_prefix"] for c in mock_dispatch.call_args_list]
    assert "wa-ressu" in prefixes
    assert "wa-tyko" in prefixes


@pytest.mark.asyncio
async def test_multi_agent_message_prefixed(
    db: sqlite3.Connection, multi_agent_connection: WhatsAppConnection
) -> None:
    """In multi-agent groups, outgoing messages get [AgentName]: prefix."""
    chat_jid = "group-multi@g.us"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(
        return_value=_make_result("<reply>Hello from agent</reply>")
    )
    with patch(MOCK_TARGET, mock_dispatch):
        multi_agent_connection._outgoing_queue = Mock()
        await multi_agent_connection._handle_agent_trigger(chat_jid)

    calls = multi_agent_connection._outgoing_queue.send.call_args_list
    assert len(calls) == 2
    texts = [c[0][2] for c in calls]
    assert any(t.startswith("[Ressu]: ") for t in texts)
    assert any(t.startswith("[Tyko]: ") for t in texts)


@pytest.mark.asyncio
async def test_single_agent_no_prefix(
    db: sqlite3.Connection, multi_agent_connection: WhatsAppConnection
) -> None:
    """Single-agent groups don't get message prefixing."""
    chat_jid = "group-tyko@g.us"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hello</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        multi_agent_connection._outgoing_queue = Mock()
        await multi_agent_connection._handle_agent_trigger(chat_jid)

    assert mock_dispatch.call_count == 1
    sent_text = multi_agent_connection._outgoing_queue.send.call_args[0][2]
    assert sent_text == "Hello"
    assert not sent_text.startswith("[")


@pytest.mark.asyncio
async def test_unrouted_group_uses_default(
    db: sqlite3.Connection, multi_agent_connection: WhatsAppConnection
) -> None:
    """Groups not in the routing table use the default agent."""
    chat_jid = "unknown-group@g.us"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result("<reply>Hi</reply>"))
    with patch(MOCK_TARGET, mock_dispatch):
        await multi_agent_connection._handle_agent_trigger(chat_jid)

    assert mock_dispatch.call_count == 1
    assert mock_dispatch.call_args.kwargs["channel_prefix"] == "wa-ressu"


@pytest.mark.asyncio
async def test_multi_agent_system_prompt_mentions_others(
    db: sqlite3.Connection, multi_agent_connection: WhatsAppConnection
) -> None:
    """Multi-agent group system prompts include awareness of other agents."""
    chat_jid = "group-multi@g.us"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result(""))
    with patch(MOCK_TARGET, mock_dispatch):
        await multi_agent_connection._handle_agent_trigger(chat_jid)

    # Ressu's prompt should mention Tyko and vice versa
    ressu_call = next(
        c
        for c in mock_dispatch.call_args_list
        if c.kwargs["channel_prefix"] == "wa-ressu"
    )
    tyko_call = next(
        c
        for c in mock_dispatch.call_args_list
        if c.kwargs["channel_prefix"] == "wa-tyko"
    )
    assert "Tyko" in ressu_call.kwargs["system_prompt"]
    assert "Ressu" in tyko_call.kwargs["system_prompt"]
    assert "multi-agent" in ressu_call.kwargs["system_prompt"].lower()


@pytest.mark.asyncio
async def test_multi_agent_model_override(
    db: sqlite3.Connection, multi_agent_connection: WhatsAppConnection
) -> None:
    """Agent-specific model overrides are passed through."""
    chat_jid = "group-tyko@g.us"
    _seed_messages(db, chat_jid)

    mock_dispatch = AsyncMock(return_value=_make_result(""))
    with patch(MOCK_TARGET, mock_dispatch):
        await multi_agent_connection._handle_agent_trigger(chat_jid)

    assert mock_dispatch.call_args.kwargs["model"] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_hard_mention_specific_agent(
    db: sqlite3.Connection, multi_agent_connection: WhatsAppConnection
) -> None:
    """Hard mention of one agent in multi-agent group only flags that agent."""
    chat_jid = "group-multi@g.us"
    # Seed a message that mentions Tyko specifically
    db.execute(
        dedent("""\
            INSERT INTO wa_messages (chat_jid, sender, text, timestamp, is_from_me)
            VALUES (?, ?, ?, ?, 0)"""),
        (chat_jid, "User", "@Tyko what do you think?", "2024-01-01T12:00:00Z"),
    )
    db.execute(
        dedent("""\
            INSERT OR IGNORE INTO wa_chats (jid, last_timestamp)
            VALUES (?, ?)"""),
        (chat_jid, "2024-01-01T12:00:00Z"),
    )
    db.commit()

    mock_dispatch = AsyncMock(return_value=_make_result(""))
    with patch(MOCK_TARGET, mock_dispatch):
        await multi_agent_connection._handle_agent_trigger(chat_jid, hard_mention=True)

    # Tyko's system prompt should have "MUST reply", Ressu's should not
    tyko_call = next(
        c
        for c in mock_dispatch.call_args_list
        if c.kwargs["channel_prefix"] == "wa-tyko"
    )
    ressu_call = next(
        c
        for c in mock_dispatch.call_args_list
        if c.kwargs["channel_prefix"] == "wa-ressu"
    )
    assert "MUST reply" in tyko_call.kwargs["system_prompt"]
    assert "MUST reply" not in ressu_call.kwargs["system_prompt"]
