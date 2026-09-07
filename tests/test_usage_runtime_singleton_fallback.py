"""Effective-runtime Usage singleton recovery regressions."""

import asyncio
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import (
    runtime_failure_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses import usage as usage_module


async def test_concurrent_failed_initialization_keeps_one_authoritative_fallback(
    monkeypatch,
) -> None:
    """A failed persistent startup cannot leave split persistent/fallback managers."""
    hass = SimpleNamespace(data={})
    key = ("entry", "agent")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def failing_getter(_hass, entry_id: str, subentry_id: str):
        nonlocal calls
        calls += 1
        persistent_managers = hass.data.setdefault(usage_module._USAGE_MANAGERS, {})
        persistent_managers[(entry_id, subentry_id)] = object()
        started.set()
        await release.wait()
        raise OSError("usage store unavailable")

    monkeypatch.setattr(hardening, "_ORIGINAL_ASYNC_GET_USAGE", failing_getter)

    first = asyncio.create_task(hardening.async_get_usage_safely(hass, *key))
    await asyncio.wait_for(started.wait(), timeout=1)
    second = asyncio.create_task(hardening.async_get_usage_safely(hass, *key))
    await asyncio.sleep(0)

    assert calls == 1
    release.set()
    first_manager, second_manager = await asyncio.gather(first, second)

    assert first_manager is second_manager
    assert calls == 1
    assert key not in hass.data[usage_module._USAGE_MANAGERS]
    assert hass.data[hardening._VOLATILE_USAGE_MANAGERS][key] is first_manager
