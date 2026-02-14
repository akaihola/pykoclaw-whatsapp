"""WhatsApp connection lifecycle manager.

Ported from NanoClaw connection pattern (index.ts:777-855).
Manages Neonize client, event registration, and the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
import signal
import threading
from textwrap import dedent
from typing import Any

from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, MessageEv, QREv
from neonize.utils.jid import Jid2String

from pykoclaw.config import settings as core_settings
from pykoclaw.db import (
    DbConnection,
    get_pending_deliveries,
    mark_delivered,
    mark_delivery_failed,
)
from pykoclaw_messaging import dispatch_to_agent

from .config import WhatsAppSettings, get_config
from .handler import (
    BatchAccumulator,
    MessageHandler,
    format_xml_messages,
    get_new_messages_for_chat,
    update_agent_cursor,
)
from .queue import OutgoingQueue

log = logging.getLogger(__name__)


def _extract_reply(text: str) -> str | None:
    """Extract text wrapped in <reply> tags from agent output.

    Uses allowlist-based filtering: only text explicitly wrapped in <reply> tags
    is returned. All other text (internal monologue, reasoning) is discarded.

    Args:
        text: Raw agent output potentially containing <reply> tags.

    Returns:
        Joined non-empty reply content, or None if no valid replies found.
    """
    matches = re.findall(r"<reply>(.*?)</reply>", text, re.DOTALL)
    stripped = [m.strip() for m in matches]
    filtered = [m for m in stripped if m]
    return "\n".join(filtered) if filtered else None


class WhatsAppConnection:
    """Manages the Neonize WhatsApp client lifecycle.

    Handles connection events (QR, connect, disconnect), registers message
    handlers, and bridges the Go-thread Neonize callbacks into asyncio.
    """

    DELIVERY_POLL_INTERVAL_S = 10

    def __init__(
        self,
        *,
        db: DbConnection,
        config: WhatsAppSettings | None = None,
        extra_mcp_servers: dict[str, Any] | None = None,
    ) -> None:
        self._config = config or get_config()
        self._db = db
        self._extra_mcp_servers = extra_mcp_servers or {}
        self._outgoing_queue = OutgoingQueue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: NewClient | None = None
        self._handler: MessageHandler | None = None
        self._batch_accumulator: BatchAccumulator | None = None
        self._delivery_task: asyncio.Task[None] | None = None

    def run(self) -> None:
        """Block the main thread on neonize ``connect()`` until Ctrl-C.

        ``connect()`` is a blocking ctypes→Go call that only unblocks when
        ``client.stop()`` cancels the Go context.  The asyncio loop runs on
        a daemon thread so ``run_coroutine_threadsafe`` agent callbacks work.
        """
        self._loop = asyncio.new_event_loop()

        self._batch_accumulator = BatchAccumulator(
            window_seconds=self._config.batch_window_seconds,
            loop=self._loop,
            flush_callback=self._handle_agent_trigger,
        )

        self._handler = MessageHandler(
            db=self._db,
            outgoing_queue=self._outgoing_queue,
            trigger_name=self._config.trigger_name,
            loop=self._loop,
            batch_accumulator=self._batch_accumulator,
            agent_callback=self._handle_agent_trigger,
        )

        loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        loop_thread.start()

        self._config.auth_dir.mkdir(parents=True, exist_ok=True)
        self._client = NewClient(str(self._config.session_db))

        self._register_events(self._client)

        log.info("Starting WhatsApp connection...")

        signal.signal(signal.SIGINT, signal.SIG_DFL)
        self._client.connect()

    def _register_events(self, client: NewClient) -> None:
        @client.event(QREv)
        def on_qr(_client: NewClient, event: QREv) -> None:
            log.warning(
                "QR code received — run 'pykoclaw whatsapp auth' to authenticate"
            )

        @client.event(ConnectedEv)
        def on_connected(_client: NewClient, event: ConnectedEv) -> None:
            self._outgoing_queue.connected = True
            log.info("Connected to WhatsApp")

            if _client.me:
                self_jid = Jid2String(_client.me.JID)
                if self._handler:
                    self._handler.set_self_jid(self_jid)
                log.info("Self JID: %s", self_jid)

            self._outgoing_queue.flush(_client)

            if self._loop and self._delivery_task is None:
                asyncio.run_coroutine_threadsafe(
                    self._start_delivery_polling(), self._loop
                )

        @client.event(DisconnectedEv)
        def on_disconnected(_client: NewClient, event: DisconnectedEv) -> None:
            self._outgoing_queue.connected = False
            if self._delivery_task is not None and not self._delivery_task.done():
                self._delivery_task.cancel()
            self._delivery_task = None
            log.info("Disconnected (queued_messages=%d)", len(self._outgoing_queue))

        @client.event(MessageEv)
        def on_message(_client: NewClient, event: MessageEv) -> None:
            if self._handler:
                self._handler.on_message(_client, event)

    def _build_system_prompt(self, chat_jid: str, *, hard_mention: bool) -> str:
        trigger = self._config.trigger_name
        base = dedent(
            f"""\
            You are {trigger}, an ambient participant in a WhatsApp chat ({chat_jid}).
            
            When you choose to reply, wrap your ENTIRE reply in `<reply>` tags. Text \
            outside these tags will NOT be delivered to the chat. Tool-call reasoning \
            and internal notes must NOT be wrapped in `<reply>` tags.
            
            You observe conversations silently. In the vast majority of batches, you \
            should produce NO text output. Err heavily toward silence.
            Only reply when: (a) you are directly addressed by name or @mention, \
            (b) there is clear factual misinformation that no one has corrected, or \
            (c) you have crucial missing knowledge that would significantly help the \
            conversation.
            Do NOT volunteer opinions, make small talk, or interject with tangential \
            information. If you choose not to reply, produce no text output at all — \
            do not explain why you are staying silent.
            You may use tools silently (e.g., writing notes, updating files) even \
            when you choose not to reply. Tool use without a reply is normal and expected.
            People may refer to you by name in various forms — your full name, \
            shortened, with or without @, with punctuation, or even inflected/declined \
            forms in non-English languages. When someone addresses you by any variation \
            of your name, treat it as a direct address and reply."""
        )
        if hard_mention:
            base += (
                "\n\nThis batch contains a direct @mention of your name "
                "— you MUST reply to it using `<reply>` tags."
            )
        return base

    async def _handle_agent_trigger(
        self,
        chat_jid: str,
        hard_mention: bool = False,
    ) -> None:
        try:
            messages = get_new_messages_for_chat(self._db, chat_jid)
            if not messages:
                return

            xml_context = format_xml_messages(messages)

            system_prompt = self._build_system_prompt(
                chat_jid, hard_mention=hard_mention
            )

            prompt = (
                f"New message batch from WhatsApp chat:\n\n{xml_context}\n\n"
                f"Decide whether to reply, use tools silently, or do nothing."
            )

            result = await dispatch_to_agent(
                prompt=prompt,
                channel_prefix="wa",
                channel_id=chat_jid,
                db=self._db,
                data_dir=core_settings.data,
                system_prompt=system_prompt,
                extra_mcp_servers=self._extra_mcp_servers,
            )

            extracted = _extract_reply(result.full_text)
            if extracted:
                jid = self._build_jid(chat_jid)
                self._outgoing_queue.send(self._client, jid, extracted)
                log.info("Agent response sent to %s", chat_jid)
            else:
                log.info("Agent chose silence for %s", chat_jid)

            last_msg_ts = messages[-1][1] if messages else ""
            if last_msg_ts:
                update_agent_cursor(self._db, chat_jid, last_msg_ts)
        except Exception:
            log.exception("Error in agent trigger for %s", chat_jid)

    async def _start_delivery_polling(self) -> None:
        self._delivery_task = asyncio.create_task(self._delivery_poll_loop())

    async def _delivery_poll_loop(self) -> None:
        log.info("Delivery polling started")
        try:
            while True:
                await asyncio.sleep(self.DELIVERY_POLL_INTERVAL_S)
                try:
                    self._process_pending_deliveries()
                except Exception:
                    log.exception("Error processing delivery queue")
        except asyncio.CancelledError:
            log.info("Delivery polling stopped")

    def _process_pending_deliveries(self) -> None:
        pending = get_pending_deliveries(self._db, "wa")
        if not pending:
            return

        for delivery in pending:
            chat_jid_str = delivery.conversation.removeprefix("wa-")
            try:
                jid = self._build_jid(chat_jid_str)
                self._outgoing_queue.send(self._client, jid, delivery.message)
                mark_delivered(self._db, delivery.id)
                log.info("Delivered task result to %s", chat_jid_str)
            except Exception:
                mark_delivery_failed(self._db, delivery.id, "send failed")
                log.exception("Failed to deliver to %s", chat_jid_str)

    @staticmethod
    def _build_jid(chat_jid_str: str) -> Any:
        """Build a Neonize JID from a string like 'user@server' or 'id@g.us'."""
        from neonize.utils.jid import build_jid

        if "@" in chat_jid_str:
            user, server = chat_jid_str.split("@", 1)
            return build_jid(user, server)
        return build_jid(chat_jid_str)
