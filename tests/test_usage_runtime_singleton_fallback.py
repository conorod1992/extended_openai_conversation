"""Effective-runtime Usage singleton recovery regressions."""

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import (
    runtime_failure_hardening as hardening,
)
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

    monkeypatch.setattr(hardening, "_ORIGINAL_ASYNC_GET_USAGE", failing_getter)

    manager = await hardening.async_get_usage_safely(hass, *key)
    same_manager = await hardening.async_get_usage_safely(hass, *key)

    assert manager is same_manager
    assert calls == 1
    assert key not in hass.data[usage_module._USAGE_MANAGERS]
    assert hass.data[hardening._VOLATILE_USAGE_MANAGERS][key] is manager
