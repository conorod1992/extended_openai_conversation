"""Effective-runtime Usage singleton recovery regressions."""

import asyncio
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import usage as usage_module


async def test_failed_initialization_replaces_published_manager_with_shared_fallback(
    monkeypatch,
) -> None:
    """A failed published persistent manager cannot survive beside the fallback."""
    hass = SimpleNamespace(data={})
    key = ("entry", "agent")
    published_manager = object()
    calls = 0

    async def failing_getter(_hass, entry_id: str, subentry_id: str):
        nonlocal calls
        calls += 1
        persistent_managers = hass.data.setdefault(usage_module._USAGE_MANAGERS, {})
        persistent_managers[(entry_id, subentry_id)] = published_manager
        raise OSError("usage store unavailable")

    monkeypatch.setattr(usage_module, "async_get_durable_usage", failing_getter)

    manager = await usage_module.async_get_usage(hass, *key)
    same_manager = await usage_module.async_get_usage(hass, *key)

    assert manager is same_manager
    assert calls == 1
    assert key not in hass.data[usage_module._USAGE_MANAGERS]
    assert hass.data[usage_module._VOLATILE_USAGE_MANAGERS][key] is manager
    assert key in hass.data[usage_module._USAGE_GETTER_LOCKS]


async def test_effective_getter_serializes_durable_selection(monkeypatch) -> None:
    """Concurrent callers cannot race persistent and fallback manager selection."""
    hass = SimpleNamespace(data={})
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def durable_getter(_hass, _entry_id: str, _subentry_id: str):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        entered.set()
        await release.wait()
        active -= 1
        return object()

    monkeypatch.setattr(usage_module, "async_get_durable_usage", durable_getter)

    first = asyncio.create_task(usage_module.async_get_usage(hass, "entry", "agent"))
    await entered.wait()
    second = asyncio.create_task(usage_module.async_get_usage(hass, "entry", "agent"))
    await asyncio.sleep(0)

    assert max_active == 1
    release.set()
    await asyncio.gather(first, second)
    assert max_active == 1
