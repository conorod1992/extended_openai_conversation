"""Usage storage durability regressions."""

from typing import Any, cast

from homeassistant.core import HomeAssistant

from custom_components.extended_openai_conversation_responses.usage import async_get_usage


async def test_runtime_usage_aggregate_stores_use_atomic_writes(
    hass: HomeAssistant,
) -> None:
    """The authoritative aggregate snapshot and legacy mirror are crash-safe."""
    manager = await async_get_usage(hass, "entry-a", "agent-a")

    totals_store = cast(Any, manager._storage)
    daily_store = cast(Any, manager._daily_storage)

    assert totals_store._atomic_writes is True
    assert daily_store._atomic_writes is True
