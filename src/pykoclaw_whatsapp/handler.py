"""Message event handler with Go-thread → asyncio bridge.

Ported from NanoClaw message dispatch (index.ts:859-885) and XML formatting
(index.ts:204-209). Uses ``asyncio.run_coroutine_threadsafe()`` to bridge
Neonize Go-thread callbacks into the Python asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from html import escape as html_escape
from textwrap import dedent
from typing import TYPE_CHECKING

from neonize.events import MessageEv
from neonize.utils.jid import Jid2String

if TYPE_CHECKING:
    from neonize.client import NewClient

    from .queue import OutgoingQueue

log = logging.getLogger(__name__)


def extract_text(msg: MessageEv) -> str | None:
    """Extract text content from a Neonize MessageEv (text + captions only)."""
    wa_msg = msg.Message
    if wa_msg.HasField("conversation") and wa_msg.conversation:
        return wa_msg.conversation
    if wa_msg.HasField("extendedTextMessage"):
        return wa_msg.extendedTextMessage.text or None
    if wa_msg.HasField("imageMessage") and wa_msg.imageMessage.caption:
        return wa_msg.imageMessage.caption
    if wa_msg.HasField("videoMessage") and wa_msg.videoMessage.caption:
        return wa_msg.videoMessage.caption
    if wa_msg.HasField("documentWithCaptionMessage"):
        inner = wa_msg.documentWithCaptionMessage
        if hasattr(inner, "message") and hasattr(inner.message, "documentMessage"):
            doc = inner.message.documentMessage
            if hasattr(doc, "caption") and doc.caption:
                return doc.caption
    return None


def format_xml_message(sender: str, timestamp: str, content: str) -> str:
    """Format a single message as XML (NanoClaw index.ts:204-209)."""
    return (
        f'<message sender="{html_escape(sender)}"'
        f' time="{html_escape(timestamp)}">'
        f"{html_escape(content)}</message>"
    )


def format_xml_messages(messages: list[tuple[str, str, str]]) -> str:
    """Format multiple messages as XML block for agent prompt."""
    lines = [format_xml_message(s, t, c) for s, t, c in messages]
    return f"<messages>\n{chr(10).join(lines)}\n</messages>"


def store_message(
    db: sqlite3.Connection,
    chat_jid: str,
    sender: str,
    text: str,
    timestamp: str,
    is_from_me: bool,
) -> None:
    db.execute(
        dedent("""\
            INSERT INTO wa_messages (chat_jid, sender, text, timestamp, is_from_me)
            VALUES (?, ?, ?, ?, ?)"""),
        (chat_jid, sender, text, timestamp, 1 if is_from_me else 0),
    )
    db.commit()


def update_chat_timestamp(
    db: sqlite3.Connection, chat_jid: str, timestamp: str
) -> None:
    db.execute(
        dedent("""\
            INSERT INTO wa_chats (jid, last_timestamp)
            VALUES (?, ?)
            ON CONFLICT(jid) DO UPDATE SET last_timestamp = excluded.last_timestamp"""),
        (chat_jid, timestamp),
    )
    db.commit()


def update_global_cursor(db: sqlite3.Connection, timestamp: str) -> None:
    """Update global last_timestamp cursor (NanoClaw dual-cursor: index.ts:60-64)."""
    db.execute(
        dedent("""\
            INSERT INTO wa_config (key, value)
            VALUES ('last_timestamp', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value"""),
        (timestamp,),
    )
    db.commit()


def update_agent_cursor(db: sqlite3.Connection, chat_jid: str, timestamp: str) -> None:
    """Update per-chat agent timestamp cursor (NanoClaw dual-cursor: index.ts:60-64)."""
    db.execute(
        dedent("""\
            INSERT INTO wa_chats (jid, last_agent_timestamp)
            VALUES (?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                last_agent_timestamp = excluded.last_agent_timestamp"""),
        (chat_jid, timestamp),
    )
    db.commit()


def get_new_messages_for_chat(
    db: sqlite3.Connection, chat_jid: str
) -> list[tuple[str, str, str]]:
    """Get messages newer than last agent timestamp for a chat.

    Returns list of (sender, timestamp, text) tuples.
    """
    row = db.execute(
        "SELECT last_agent_timestamp FROM wa_chats WHERE jid = ?", (chat_jid,)
    ).fetchone()
    since = row["last_agent_timestamp"] if row and row["last_agent_timestamp"] else ""

    rows = db.execute(
        dedent("""\
            SELECT sender, timestamp, text FROM wa_messages
            WHERE chat_jid = ? AND timestamp > ?
            ORDER BY timestamp"""),
        (chat_jid, since),
    ).fetchall()
    return [(r["sender"], r["timestamp"], r["text"]) for r in rows]


def should_trigger(
    text: str,
    trigger_name: str,
    is_self_chat: bool,
) -> bool:
    """Check if message should trigger agent response.

    Self-chat always triggers. Otherwise requires @trigger_name mention.
    """
    if is_self_chat:
        return True
    return f"@{trigger_name}" in text


class MessageHandler:
    """Handles incoming WhatsApp messages.

    Bridges Neonize Go-thread callbacks into the asyncio event loop using
    ``asyncio.run_coroutine_threadsafe()``.
    """

    def __init__(
        self,
        *,
        db: sqlite3.Connection,
        outgoing_queue: OutgoingQueue,
        trigger_name: str,
        loop: asyncio.AbstractEventLoop,
        agent_callback: object | None = None,
    ) -> None:
        self._db = db
        self._outgoing_queue = outgoing_queue
        self._trigger_name = trigger_name
        self._loop = loop
        self._agent_callback = agent_callback
        self._self_jid: str | None = None

    def set_self_jid(self, jid_str: str) -> None:
        self._self_jid = jid_str

    def on_message(self, client: NewClient, event: MessageEv) -> None:
        """Neonize message callback — runs on a Go thread."""
        try:
            info = event.Info
            source = info.MessageSource
            chat_jid = Jid2String(source.Chat)

            if chat_jid == "status@broadcast":
                return

            timestamp = datetime.fromtimestamp(
                info.Timestamp / 1000, tz=timezone.utc
            ).isoformat()
            is_from_me = source.IsFromMe
            sender = info.Pushname or Jid2String(source.Sender)

            text = extract_text(event)
            if not text:
                return

            store_message(
                self._db,
                chat_jid=chat_jid,
                sender=sender,
                text=text,
                timestamp=timestamp,
                is_from_me=is_from_me,
            )
            update_chat_timestamp(self._db, chat_jid, timestamp)
            update_global_cursor(self._db, timestamp)

            is_self_chat = (
                self._self_jid is not None
                and chat_jid.split("@")[0] == self._self_jid.split("@")[0]
                and not source.IsGroup
            )

            if not should_trigger(text, self._trigger_name, is_self_chat):
                log.debug("Skipping message (no trigger): %s", chat_jid)
                return

            log.info(
                "Trigger matched in %s from %s: %.50s",
                chat_jid,
                sender,
                text,
            )

            if self._agent_callback is not None:
                asyncio.run_coroutine_threadsafe(
                    self._agent_callback(  # type: ignore[operator]
                        client=client,
                        chat_jid=chat_jid,
                        trigger_text=text,
                    ),
                    self._loop,
                )
        except Exception:
            log.exception("Error handling message")
