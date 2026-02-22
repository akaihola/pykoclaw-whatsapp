"""WhatsApp connection lifecycle manager.

Ported from NanoClaw connection pattern (index.ts:777-855).
Manages Neonize client, event registration, and the asyncio event loop.

Supports multi-agent group routing: different WhatsApp groups can be mapped
to different agent personalities, and multi-agent groups get message
prefixing and loop prevention.
"""

from __future__ import annotations

import asyncio
import logging
import re
import signal
import threading
from pathlib import Path
from textwrap import dedent
from typing import Any

from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, MessageEv, QREv
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import Jid2String

from pykoclaw.config import settings as core_settings
from pykoclaw.db import (
    DbConnection,
    get_pending_deliveries,
    init_db,
    mark_delivered,
    mark_delivery_failed,
)
from pykoclaw_messaging import dispatch_to_agent

from .config import WhatsAppSettings, get_config
from .handler import (
    BatchAccumulator,
    MessageHandler,
    find_hard_mentions,
    format_xml_messages,
    get_new_messages_for_chat,
    update_agent_cursor,
)
from .queue import OutgoingQueue
from .routing import AgentConfig, RoutingConfig, load_routing_config

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

    Supports multi-agent group routing: each group can be mapped to one or
    more agent personalities via a routing config.
    """

    DELIVERY_POLL_INTERVAL_S = 10

    def __init__(
        self,
        *,
        db: DbConnection,
        config: WhatsAppSettings | None = None,
        extra_mcp_servers: dict[str, Any] | None = None,
        routing: RoutingConfig | None = None,
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
        self._routing = routing or load_routing_config(
            self._config.agent_routes, self._config.trigger_name
        )
        self._agent_dbs: dict[str, DbConnection] = {}

    def _get_agent_db(self, agent: AgentConfig) -> DbConnection:
        """Get or lazily create a DB connection for an agent's data directory.

        If the agent has its own ``data_dir``, open its DB (creating core
        tables if needed). Otherwise fall back to the bridge DB.
        """
        if agent.data_dir is None:
            return self._db
        if agent.name not in self._agent_dbs:
            db_path = agent.data_dir / "pykoclaw.db"
            db = init_db(db_path)
            db.execute("PRAGMA journal_mode=WAL")
            self._agent_dbs[agent.name] = db
            log.info("Opened agent DB: %s → %s", agent.name, db_path)
        return self._agent_dbs[agent.name]

    def _get_agent_data_dir(self, agent: AgentConfig) -> Path:
        """Return the data directory for an agent (falls back to core settings)."""
        return agent.data_dir or core_settings.data

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
            trigger_names=self._routing.all_trigger_names,
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
        log.info(
            "Routing: %d agents (%s), %d group routes",
            len(self._routing.agents),
            ", ".join(self._routing.agents),
            len(self._routing.routes),
        )

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

    def _build_system_prompt(
        self,
        agent: AgentConfig,
        chat_jid: str,
        *,
        is_multi_agent: bool,
        other_agent_names: list[str] | None = None,
    ) -> str:
        trigger = agent.name
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
        if is_multi_agent and other_agent_names:
            others = ", ".join(other_agent_names)
            base += dedent(
                f"""

                This is a multi-agent group. Other AI agents in this chat: {others}.
                Messages prefixed with [AgentName]: are from another AI agent.
                Do NOT respond to another agent's messages — even if they address you.
                Only after a human participant sends a message should you consider
                whether to speak. Never engage in agent-to-agent dialogue."""
            )
        # NOTE: hard_mention instruction goes in the user prompt, not here.
        # system_prompt is baked into the session at creation and silently
        # ignored on resume — see .memory/session-resume-system-prompt.md.
        return base

    async def _handle_agent_trigger(
        self,
        chat_jid: str,
        hard_mention: bool = False,
    ) -> None:
        """Handle a batch flush by dispatching to all mapped agents sequentially."""
        agents = self._routing.agents_for_chat(chat_jid)
        is_multi = len(agents) > 1

        messages = get_new_messages_for_chat(self._db, chat_jid)
        if not messages:
            return

        # Determine which agents are specifically hard-mentioned in the batch
        all_text = " ".join(text for _, _, text in messages)
        mentioned_agents = find_hard_mentions(all_text, self._routing.all_trigger_names)

        for agent in agents:
            agent_hard_mention = hard_mention and (
                not mentioned_agents or agent.name in mentioned_agents
            )
            try:
                await self._dispatch_for_agent(
                    chat_jid=chat_jid,
                    agent=agent,
                    messages=messages,
                    is_multi_agent=is_multi,
                    hard_mention=agent_hard_mention,
                )
            except Exception:
                log.exception(
                    "Error in agent trigger for %s (agent=%s)", chat_jid, agent.name
                )

        # Advance cursor after all agents have processed
        last_msg_ts = messages[-1][1] if messages else ""
        if last_msg_ts:
            update_agent_cursor(self._db, chat_jid, last_msg_ts)

    async def _dispatch_for_agent(
        self,
        *,
        chat_jid: str,
        agent: AgentConfig,
        messages: list[tuple[str, str, str]],
        is_multi_agent: bool,
        hard_mention: bool,
    ) -> None:
        """Dispatch a message batch to a single agent."""
        other_names = [
            a.name
            for a in self._routing.agents_for_chat(chat_jid)
            if a.name != agent.name
        ]

        xml_context = format_xml_messages(messages)
        system_prompt = self._build_system_prompt(
            agent,
            chat_jid,
            is_multi_agent=is_multi_agent,
            other_agent_names=other_names if is_multi_agent else None,
        )

        prompt = f"New message batch from WhatsApp chat:\n\n{xml_context}\n\n"
        if hard_mention:
            prompt += (
                "This batch contains a direct @mention of your name "
                "— you MUST reply using `<reply>` tags.\n\n"
            )
        prompt += "Decide whether to reply, use tools silently, or do nothing."

        agent_db = self._get_agent_db(agent)
        agent_data_dir = self._get_agent_data_dir(agent)

        # Show "Writing..." indicator while the agent is thinking.
        self._set_chat_presence(chat_jid, composing=True)
        try:
            result = await dispatch_to_agent(
                prompt=prompt,
                channel_prefix=f"wa-{agent.name.lower()}",
                channel_id=chat_jid,
                db=agent_db,
                data_dir=agent_data_dir,
                system_prompt=system_prompt,
                extra_mcp_servers=self._extra_mcp_servers,
                model=agent.model,
            )
        finally:
            self._set_chat_presence(chat_jid, composing=False)

        extracted = _extract_reply(result.full_text)
        if extracted:
            if is_multi_agent:
                extracted = f"[{agent.name}]: {extracted}"
            jid = self._build_jid(chat_jid)
            self._outgoing_queue.send(self._client, jid, extracted)
            log.info("Agent %s response sent to %s", agent.name, chat_jid)
        else:
            log.info("Agent %s chose silence for %s", agent.name, chat_jid)

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

    def _get_all_delivery_dbs(self) -> list[DbConnection]:
        """Return all unique DBs that may contain pending deliveries.

        Includes the bridge DB plus every per-agent DB (lazily opened).
        """
        seen_ids: set[int] = {id(self._db)}
        dbs: list[DbConnection] = [self._db]
        for agent_cfg in self._routing.agents.values():
            db = self._get_agent_db(agent_cfg)
            if id(db) not in seen_ids:
                seen_ids.add(id(db))
                dbs.append(db)
        return dbs

    def _process_pending_deliveries(self) -> None:
        for db in self._get_all_delivery_dbs():
            self._process_deliveries_from_db(db)

    def _process_deliveries_from_db(self, db: DbConnection) -> None:
        pending = get_pending_deliveries(db, "wa")
        if not pending:
            return

        for delivery in pending:
            agent, chat_jid_str = self._routing.parse_conversation(
                delivery.conversation
            )
            if not agent or not chat_jid_str:
                # Legacy format fallback: wa-{jid}
                chat_jid_str = delivery.conversation.removeprefix("wa-")
                agent = self._routing.agents.get(self._routing.default_agent)

            is_multi = self._routing.is_multi_agent(chat_jid_str)

            try:
                jid = self._build_jid(chat_jid_str)
                message = delivery.message
                if is_multi and agent:
                    message = f"[{agent.name}]: {message}"
                self._outgoing_queue.send(self._client, jid, message)
                mark_delivered(db, delivery.id)
                log.info(
                    "Delivered task result to %s (agent=%s)",
                    chat_jid_str,
                    agent.name if agent else "unknown",
                )
            except Exception:
                mark_delivery_failed(db, delivery.id, "send failed")
                log.exception("Failed to deliver to %s", chat_jid_str)

    def _set_chat_presence(self, chat_jid: str, composing: bool) -> None:
        """Send a typing indicator (composing/paused) to a WhatsApp chat.

        This triggers the "Writing..." indicator in the recipient's app.
        Errors are logged and swallowed — presence is best-effort.
        """
        if not self._client:
            return
        try:
            jid = self._build_jid(chat_jid)
            state = (
                ChatPresence.CHAT_PRESENCE_COMPOSING
                if composing
                else ChatPresence.CHAT_PRESENCE_PAUSED
            )
            self._client.send_chat_presence(
                jid, state, ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT
            )
        except Exception:
            log.debug("Failed to send chat presence to %s", chat_jid)

    @staticmethod
    def _build_jid(chat_jid_str: str) -> Any:
        """Build a Neonize JID from a string like 'user@server' or 'id@g.us'."""
        from neonize.utils.jid import build_jid

        if "@" in chat_jid_str:
            user, server = chat_jid_str.split("@", 1)
            return build_jid(user, server)
        return build_jid(chat_jid_str)
