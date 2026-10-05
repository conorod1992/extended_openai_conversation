"""Stable-CI coverage for Usage storage failure semantics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from homeassistant.core import CoreState

from custom_components.extended_openai_conversation_responses import usage


@pytest.mark.asyncio
async def test_usage_store_flushes_explicit_save_during_shutdown(
    hass, monkeypatch
) -> None:
    parent_save = AsyncMock()
    parent_write = AsyncMock()
    monkeypatch.setattr(usage.PropagatingWriteStore, "async_save", parent_save)
    monkeypatch.setattr(
        usage.PropagatingWriteStore,
        "_async_handle_write_data",
        parent_write,
    )
    hass.state = CoreState.stopping
    store = usage._UsageStore(hass, 1, "coverage.usage")

    await store.async_save({"value": 1})

    parent_save.assert_awaited_once_with({"value": 1})
    parent_write.assert_awaited_once()
    assert usage._EXPLICIT_USAGE_SAVE.get() is False


@pytest.mark.asyncio
async def test_usage_store_does_not_force_flush_while_running(hass, monkeypatch) -> None:
    parent_save = AsyncMock()
    parent_write = AsyncMock()
    monkeypatch.setattr(usage.PropagatingWriteStore, "async_save", parent_save)
    monkeypatch.setattr(
        usage.PropagatingWriteStore,
        "_async_handle_write_data",
        parent_write,
    )
    hass.state = CoreState.running
    store = usage._UsageStore(hass, 1, "coverage.usage")

    await store.async_save({"value": 1})

    parent_save.assert_awaited_once()
    parent_write.assert_not_awaited()
    assert usage._EXPLICIT_USAGE_SAVE.get() is False


@pytest.mark.asyncio
async def test_usage_store_explicit_write_failure_propagates_and_resets_context(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        usage.PropagatingWriteStore,
        "async_save",
        AsyncMock(side_effect=OSError("disk full")),
    )
    hass.state = CoreState.running
    store = usage._UsageStore(hass, 1, "coverage.usage")

    with pytest.raises(OSError, match="disk full"):
        await store.async_save({"value": 1})

    assert usage._EXPLICIT_USAGE_SAVE.get() is False


@pytest.mark.asyncio
async def test_usage_store_delayed_write_failure_is_best_effort(
    hass, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(
        usage.PropagatingWriteStore,
        "_async_handle_write_data",
        AsyncMock(side_effect=OSError("disk full")),
    )
    store = usage._UsageStore(hass, 1, "coverage.usage")

    await store._async_handle_write_data()

    assert "Unable to persist delayed usage telemetry" in caplog.text


@pytest.mark.asyncio
async def test_usage_store_explicit_write_handler_failure_is_not_swallowed(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        usage.PropagatingWriteStore,
        "_async_handle_write_data",
        AsyncMock(side_effect=OSError("disk full")),
    )
    store = usage._UsageStore(hass, 1, "coverage.usage")
    token = usage._EXPLICIT_USAGE_SAVE.set(True)
    try:
        with pytest.raises(OSError, match="disk full"):
            await store._async_handle_write_data()
    finally:
        usage._EXPLICIT_USAGE_SAVE.reset(token)


@pytest.mark.asyncio
async def test_volatile_usage_storage_is_noop() -> None:
    store = usage._VolatileUsageStorage()

    assert await store.async_load() is None
    assert await store.async_save({"ignored": True}) is None
