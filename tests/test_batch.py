"""Tests for BatchAccumulator timer-based message batching."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("neonize")

from pykoclaw_whatsapp.handler import BatchAccumulator


@pytest.mark.asyncio
async def test_timer_fires_after_window() -> None:
    flush_cb = AsyncMock()
    captured: list[tuple[float, object]] = []

    loop = asyncio.get_running_loop()

    original_call_later = loop.call_later

    def mock_call_later(delay: float, callback: object, *args: object) -> Mock:
        handle = Mock()
        handle.cancel = Mock()
        captured.append((delay, callback))
        return handle

    loop.call_later = mock_call_later  # type: ignore[assignment]
    try:
        acc = BatchAccumulator(window_seconds=5.0, loop=loop, flush_callback=flush_cb)
        await acc._add_async("chat_a")

        assert len(captured) == 1
        assert captured[0][0] == 5.0

        await acc._timer_expired("chat_a")

        flush_cb.assert_called_once_with("chat_a", False)
    finally:
        loop.call_later = original_call_later  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_multiple_messages_single_flush() -> None:
    flush_cb = AsyncMock()
    captured: list[tuple[float, object]] = []

    loop = asyncio.get_running_loop()
    original_call_later = loop.call_later

    def mock_call_later(delay: float, callback: object, *args: object) -> Mock:
        handle = Mock()
        handle.cancel = Mock()
        captured.append((delay, callback))
        return handle

    loop.call_later = mock_call_later  # type: ignore[assignment]
    try:
        acc = BatchAccumulator(window_seconds=5.0, loop=loop, flush_callback=flush_cb)
        await acc._add_async("chat_a")
        await acc._add_async("chat_a")
        await acc._add_async("chat_a")

        assert len(captured) == 1

        await acc._timer_expired("chat_a")

        flush_cb.assert_called_once_with("chat_a", False)
    finally:
        loop.call_later = original_call_later  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_independent_chat_timers() -> None:
    flush_cb = AsyncMock()
    captured: list[tuple[float, object, str]] = []

    loop = asyncio.get_running_loop()
    original_call_later = loop.call_later

    def mock_call_later(delay: float, callback: object, *args: object) -> Mock:
        handle = Mock()
        handle.cancel = Mock()
        captured.append((delay, callback, "timer"))
        return handle

    loop.call_later = mock_call_later  # type: ignore[assignment]
    try:
        acc = BatchAccumulator(window_seconds=5.0, loop=loop, flush_callback=flush_cb)
        await acc._add_async("chat_a")
        await acc._add_async("chat_b")

        assert len(captured) == 2
    finally:
        loop.call_later = original_call_later  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_hard_mention_flush() -> None:
    flush_cb = AsyncMock()
    timer_handle = Mock()
    timer_handle.cancel = Mock()

    loop = asyncio.get_running_loop()
    original_call_later = loop.call_later

    def mock_call_later(delay: float, callback: object, *args: object) -> Mock:
        return timer_handle

    loop.call_later = mock_call_later  # type: ignore[assignment]
    try:
        acc = BatchAccumulator(window_seconds=5.0, loop=loop, flush_callback=flush_cb)
        await acc._add_async("chat_a")
        await acc.flush_now("chat_a")

        timer_handle.cancel.assert_called_once()
        flush_cb.assert_called_once_with("chat_a", True)
    finally:
        loop.call_later = original_call_later  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_hard_mention_includes_accumulated() -> None:
    flush_cb = AsyncMock()
    timer_handle = Mock()
    timer_handle.cancel = Mock()

    loop = asyncio.get_running_loop()
    original_call_later = loop.call_later

    def mock_call_later(delay: float, callback: object, *args: object) -> Mock:
        return timer_handle

    loop.call_later = mock_call_later  # type: ignore[assignment]
    try:
        acc = BatchAccumulator(window_seconds=5.0, loop=loop, flush_callback=flush_cb)
        await acc._add_async("chat_a")
        await acc._add_async("chat_a")
        await acc.flush_now("chat_a")

        flush_cb.assert_called_once_with("chat_a", True)
        assert "chat_a" not in acc._timers
    finally:
        loop.call_later = original_call_later  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_concurrent_flush_blocked() -> None:
    call_order: list[str] = []

    async def slow_flush(chat_jid: str, hard_mention: bool) -> None:
        call_order.append(f"start-{chat_jid}-{hard_mention}")
        await asyncio.sleep(0.1)
        call_order.append(f"end-{chat_jid}-{hard_mention}")

    loop = asyncio.get_running_loop()
    acc = BatchAccumulator(window_seconds=5.0, loop=loop, flush_callback=slow_flush)

    t1 = asyncio.create_task(acc.flush_now("chat_a"))
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(acc._do_flush("chat_a", hard_mention=False))

    await asyncio.gather(t1, t2)

    assert call_order[0] == "start-chat_a-True"
    assert call_order[1] == "end-chat_a-True"
    assert call_order[2] == "start-chat_a-False"
    assert call_order[3] == "end-chat_a-False"


@pytest.mark.asyncio
async def test_pending_reflush() -> None:
    call_later_calls: list[tuple[float, object]] = []

    loop = asyncio.get_running_loop()
    original_call_later = loop.call_later

    def mock_call_later(delay: float, callback: object, *args: object) -> Mock:
        handle = Mock()
        handle.cancel = Mock()
        call_later_calls.append((delay, callback))
        return handle

    flush_count = 0
    flush_entered = asyncio.Event()
    flush_proceed = asyncio.Event()

    async def controlled_flush(chat_jid: str, hard_mention: bool) -> None:
        nonlocal flush_count
        flush_count += 1
        if flush_count == 1:
            flush_entered.set()
            await flush_proceed.wait()

    loop.call_later = mock_call_later  # type: ignore[assignment]
    try:
        acc = BatchAccumulator(
            window_seconds=5.0, loop=loop, flush_callback=controlled_flush
        )

        flush_task = asyncio.create_task(acc.flush_now("chat_a"))
        await asyncio.sleep(0)
        await flush_entered.wait()

        await acc._add_async("chat_a")
        assert "chat_a" in acc._pending_reflush

        flush_proceed.set()
        await flush_task

        assert "chat_a" not in acc._pending_reflush
        assert len(call_later_calls) >= 1
    finally:
        loop.call_later = original_call_later  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_empty_batch_skipped() -> None:
    """Empty batches are handled at the MessageHandler level.

    When get_new_messages_for_chat() returns empty, _handle_agent_trigger
    returns early. BatchAccumulator itself only tracks chat_jids, not
    message content, so it always invokes the flush callback. The callback
    (connection._handle_agent_trigger) is responsible for skipping empty
    batches.
    """
    flush_cb = AsyncMock()
    loop = asyncio.get_running_loop()
    acc = BatchAccumulator(window_seconds=5.0, loop=loop, flush_callback=flush_cb)

    await acc.flush_now("chat_a")
    flush_cb.assert_called_once_with("chat_a", True)


def test_module_imports() -> None:
    from pykoclaw_whatsapp.handler import BatchAccumulator as BA

    assert BA is not None
    assert callable(getattr(BA, "add", None))
    assert callable(getattr(BA, "flush_now", None))
