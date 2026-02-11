"""WhatsApp connection lifecycle manager.

Ported from NanoClaw connection pattern (index.ts:777-855).
Manages Neonize client, event registration, and the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, MessageEv, QREv
from neonize.utils.jid import Jid2String

from pykoclaw.agent_core import query_agent
from pykoclaw.config import settings as core_settings

from .config import WhatsAppSettings, get_config
from .handler import (
    MessageHandler,
    format_xml_messages,
    get_new_messages_for_chat,
    update_agent_cursor,
)
from .queue import OutgoingQueue

log = logging.getLogger(__name__)


class WhatsAppConnection:
    """Manages the Neonize WhatsApp client lifecycle.

    Handles connection events (QR, connect, disconnect), registers message
    handlers, and bridges the Go-thread Neonize callbacks into asyncio.
    """

    def __init__(
        self,
        *,
        db: sqlite3.Connection,
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

    def run(self) -> None:
        """Start the WhatsApp connection and asyncio event loop.

        Blocks until interrupted (Ctrl+C) or logged out.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._handler = MessageHandler(
            db=self._db,
            outgoing_queue=self._outgoing_queue,
            trigger_name=self._config.trigger_name,
            loop=self._loop,
            agent_callback=self._handle_agent_trigger,
        )

        self._config.auth_dir.mkdir(parents=True, exist_ok=True)
        self._client = NewClient("pykoclaw-whatsapp")

        self._register_events(self._client)

        log.info("Starting WhatsApp connection...")

        try:
            self._loop.run_in_executor(None, self._client.connect)
            self._loop.run_forever()
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            if self._client:
                try:
                    self._client.disconnect()
                except Exception:
                    pass
            self._loop.close()

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
                self_jid = Jid2String(_client.me)
                if self._handler:
                    self._handler.set_self_jid(self_jid)
                log.info("Self JID: %s", self_jid)

            self._outgoing_queue.flush(_client)

        @client.event(DisconnectedEv)
        def on_disconnected(_client: NewClient, event: DisconnectedEv) -> None:
            self._outgoing_queue.connected = False
            log.info("Disconnected (queued_messages=%d)", len(self._outgoing_queue))

        @client.event(MessageEv)
        def on_message(_client: NewClient, event: MessageEv) -> None:
            if self._handler:
                self._handler.on_message(_client, event)

    async def _handle_agent_trigger(
        self,
        *,
        client: NewClient,
        chat_jid: str,
        trigger_text: str,
    ) -> None:
        """Process a triggered message through the agent pipeline."""
        try:
            messages = get_new_messages_for_chat(self._db, chat_jid)
            if not messages:
                return

            xml_context = format_xml_messages(messages)
            conversation_name = f"wa-{chat_jid}"
            prompt = (
                f"You are responding to a WhatsApp conversation. "
                f"Here are the recent messages:\n\n{xml_context}\n\n"
                f"Respond to the latest message."
            )

            response_parts: list[str] = []
            async for msg in query_agent(
                prompt,
                db=self._db,
                data_dir=core_settings.data,
                conversation_name=conversation_name,
                extra_mcp_servers=self._extra_mcp_servers,
            ):
                if msg.type == "text" and msg.text:
                    response_parts.append(msg.text)
                elif msg.type == "result":
                    pass

            if response_parts:
                full_response = "\n".join(response_parts)
                jid = self._build_jid(chat_jid)
                self._outgoing_queue.send(client, jid, full_response)

            last_msg_ts = messages[-1][1] if messages else ""
            if last_msg_ts:
                update_agent_cursor(self._db, chat_jid, last_msg_ts)

            log.info("Agent response sent to %s", chat_jid)
        except Exception:
            log.exception("Error in agent trigger for %s", chat_jid)

    @staticmethod
    def _build_jid(chat_jid_str: str) -> Any:
        """Build a Neonize JID from a string like 'user@server' or 'id@g.us'."""
        from neonize.utils.jid import build_jid

        if "@" in chat_jid_str:
            user, server = chat_jid_str.split("@", 1)
            return build_jid(user, server)
        return build_jid(chat_jid_str)
