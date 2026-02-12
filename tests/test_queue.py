"""Tests for outgoing message queue."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

pytest.importorskip("neonize")

from pykoclaw_whatsapp.queue import OutgoingQueue, QueuedMessage


def test_queued_message_creation() -> None:
    """Test QueuedMessage dataclass creation."""
    mock_jid = Mock()
    msg = QueuedMessage(jid=mock_jid, text="Hello")

    assert msg.jid is mock_jid
    assert msg.text == "Hello"


def test_outgoing_queue_initialization() -> None:
    """Test OutgoingQueue initialization."""
    queue = OutgoingQueue()

    assert not queue.connected
    assert len(queue) == 0


def test_outgoing_queue_connected_property() -> None:
    """Test connected property getter and setter."""
    queue = OutgoingQueue()

    assert not queue.connected

    queue.connected = True
    assert queue.connected

    queue.connected = False
    assert not queue.connected


def test_enqueue_adds_message() -> None:
    """Test that enqueue adds message to queue."""
    queue = OutgoingQueue()
    mock_jid = Mock()
    mock_jid.User = "123"

    queue.enqueue(mock_jid, "Test message")

    assert len(queue) == 1


def test_send_when_disconnected_queues_message() -> None:
    """Test that send queues message when disconnected."""
    queue = OutgoingQueue()
    queue.connected = False

    mock_client = Mock()
    mock_jid = Mock()
    mock_jid.User = "123"

    queue.send(mock_client, mock_jid, "Test message")

    assert len(queue) == 1
    mock_client.send_message.assert_not_called()


def test_send_when_connected_sends_immediately() -> None:
    """Test that send sends message immediately when connected."""
    queue = OutgoingQueue()
    queue.connected = True

    mock_client = Mock()
    mock_jid = Mock()
    mock_jid.User = "123"

    queue.send(mock_client, mock_jid, "Test message")

    assert len(queue) == 0
    mock_client.send_message.assert_called_once_with(mock_jid, "Test message")


def test_send_queues_on_failure() -> None:
    """Test that send queues message on send failure."""
    queue = OutgoingQueue()
    queue.connected = True

    mock_client = Mock()
    mock_client.send_message.side_effect = Exception("Send failed")
    mock_jid = Mock()
    mock_jid.User = "123"

    queue.send(mock_client, mock_jid, "Test message")

    assert len(queue) == 1


def test_flush_sends_all_queued_messages() -> None:
    """Test that flush sends all queued messages."""
    queue = OutgoingQueue()
    queue.connected = True

    mock_jid1 = Mock()
    mock_jid1.User = "123"
    mock_jid2 = Mock()
    mock_jid2.User = "456"

    queue.enqueue(mock_jid1, "Message 1")
    queue.enqueue(mock_jid2, "Message 2")

    assert len(queue) == 2

    mock_client = Mock()
    queue.flush(mock_client)

    assert len(queue) == 0
    assert mock_client.send_message.call_count == 2


def test_flush_does_nothing_when_empty() -> None:
    """Test that flush does nothing when queue is empty."""
    queue = OutgoingQueue()
    queue.connected = True

    mock_client = Mock()
    queue.flush(mock_client)

    mock_client.send_message.assert_not_called()


def test_flush_prevents_concurrent_flushes() -> None:
    """Test that flush is thread-safe via lock."""
    queue = OutgoingQueue()
    queue.connected = True

    mock_jid = Mock()
    mock_jid.User = "123"
    queue.enqueue(mock_jid, "Message")

    mock_client = Mock()
    queue.flush(mock_client)

    assert len(queue) == 0
    mock_client.send_message.assert_called_once()


def test_len_returns_queue_size() -> None:
    """Test that __len__ returns correct queue size."""
    queue = OutgoingQueue()

    assert len(queue) == 0

    mock_jid = Mock()
    mock_jid.User = "123"

    queue.enqueue(mock_jid, "Message 1")
    assert len(queue) == 1

    queue.enqueue(mock_jid, "Message 2")
    assert len(queue) == 2
