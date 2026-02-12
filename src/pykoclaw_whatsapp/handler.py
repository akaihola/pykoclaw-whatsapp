"""Message event handler with Go-thread → asyncio bridge.

Ported from NanoClaw message dispatch (index.ts:859-885) and XML formatting
(index.ts:204-209). Uses ``asyncio.run_coroutine_threadsafe()`` to bridge
Neonize Go-thread callbacks into the Python asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
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


class BatchAccumulator:
    """Per-chat message batch accumulator with timer-based flushing.

    Accumulates messages in per-chat batches. After the first message in a
    batch, a timer fires after ``window_seconds``. Hard mentions flush
    immediately via :meth:`flush_now`. A per-chat :class:`asyncio.Lock`
    prevents concurrent agent calls for the same chat.
    """

    def __init__(
        self,
        *,
        window_seconds: float,
        loop: asyncio.AbstractEventLoop,
        flush_callback: Callable[[str, bool], Awaitable[None]],
    ) -> None:
        self._window = window_seconds
        self._loop = loop
        self._flush_callback = flush_callback
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending_reflush: set[str] = set()

    def _get_lock(self, chat_jid: str) -> asyncio.Lock:
        if chat_jid not in self._locks:
            self._locks[chat_jid] = asyncio.Lock()
        return self._locks[chat_jid]

    def add(self, chat_jid: str) -> None:
        """Schedule a batch timer for *chat_jid* (called from Go thread).

        First message starts the timer. Subsequent messages within the window
        do NOT reset it (debounce, not throttle).  If the chat is currently
        being flushed (lock held), the chat is marked for re-flush.
        """
        asyncio.run_coroutine_threadsafe(self._add_async(chat_jid), self._loop)

    async def _add_async(self, chat_jid: str) -> None:
        lock = self._get_lock(chat_jid)
        if lock.locked():
            self._pending_reflush.add(chat_jid)
            return
        if chat_jid not in self._timers:
            handle = self._loop.call_later(
                self._window,
                lambda jid=chat_jid: asyncio.ensure_future(self._timer_expired(jid)),
            )
            self._timers[chat_jid] = handle

    async def flush_now(self, chat_jid: str) -> None:
        """Immediately flush *chat_jid*'s batch (hard mention / self-chat)."""
        if chat_jid in self._timers:
            self._timers.pop(chat_jid).cancel()
        await self._do_flush(chat_jid, hard_mention=True)

    async def _timer_expired(self, chat_jid: str) -> None:
        self._timers.pop(chat_jid, None)
        await self._do_flush(chat_jid, hard_mention=False)

    async def _do_flush(self, chat_jid: str, *, hard_mention: bool) -> None:
        lock = self._get_lock(chat_jid)
        async with lock:
            await self._flush_callback(chat_jid, hard_mention)
        if chat_jid in self._pending_reflush:
            self._pending_reflush.discard(chat_jid)
            handle = self._loop.call_later(
                self._window,
                lambda jid=chat_jid: asyncio.ensure_future(self._timer_expired(jid)),
            )
            self._timers[chat_jid] = handle


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
        batch_accumulator: BatchAccumulator,
        agent_callback: object | None = None,
    ) -> None:
        self._db = db
        self._outgoing_queue = outgoing_queue
        self._trigger_name = trigger_name
        self._loop = loop
        self._batch_accumulator = batch_accumulator
        self._agent_callback = agent_callback
        self._self_jid: str | None = None

    def set_self_jid(self, jid_str: str) -> None:
        self._self_jid = jid_str

    def on_message(self, client: NewClient, event: MessageEv) -> None:
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

            if is_from_me:
                return

            is_self_chat = (
                self._self_jid is not None
                and chat_jid.split("@")[0] == self._self_jid.split("@")[0]
                and not source.IsGroup
            )

            is_hard_mention = f"@{self._trigger_name}".lower() in text.lower()

            if is_self_chat or is_hard_mention:
                asyncio.run_coroutine_threadsafe(
                    self._batch_accumulator.flush_now(chat_jid),
                    self._loop,
                )
            else:
                self._batch_accumulator.add(chat_jid)
        except Exception:
            log.exception("Error handling message")
