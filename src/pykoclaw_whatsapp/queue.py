"""Outgoing message queue for disconnection resilience.

Buffers messages when WhatsApp is disconnected, flushes on reconnect.
Ported from NanoClaw's outgoing queue pattern (index.ts:383-415).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.proto.Neonize_pb2 import JID

log = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """A message waiting to be sent."""

    jid: JID
    text: str


class OutgoingQueue:
    """Buffer outgoing messages when WhatsApp is disconnected.

    Thread-safe: called from both the asyncio event loop and Go callback threads.
    Uses a simple list with sequential processing (no batching).
    """

    _queue: list[QueuedMessage] = field(default_factory=list)

    def __init__(self) -> None:
        self._queue: list[QueuedMessage] = []
        self._flushing = False
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = value

    def enqueue(self, jid: JID, text: str) -> None:
        """Add a message to the queue."""
        self._queue.append(QueuedMessage(jid=jid, text=text))
        log.info(
            "Message queued (jid=%s, len=%d, queue_size=%d)",
            getattr(jid, "User", "?"),
            len(text),
            len(self._queue),
        )

    def send(self, client: NewClient, jid: JID, text: str) -> None:
        """Send a message, queuing it if disconnected or on failure."""
        if not self._connected:
            self.enqueue(jid, text)
            return
        try:
            client.send_message(jid, text)
            log.info(
                "Message sent (jid=%s, len=%d)",
                getattr(jid, "User", "?"),
                len(text),
            )
        except Exception:
            self.enqueue(jid, text)
            log.warning(
                "Failed to send, message queued (jid=%s, queue_size=%d)",
                getattr(jid, "User", "?"),
                len(self._queue),
                exc_info=True,
            )

    def flush(self, client: NewClient) -> None:
        """Flush all queued messages. Called on reconnect."""
        if self._flushing or not self._queue:
            return
        self._flushing = True
        try:
            log.info("Flushing outgoing message queue (count=%d)", len(self._queue))
            while self._queue:
                item = self._queue.pop(0)
                self.send(client, item.jid, item.text)
        finally:
            self._flushing = False

    def __len__(self) -> int:
        return len(self._queue)
