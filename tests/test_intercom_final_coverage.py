"""Final meaningful Broadcast/intercom coverage gaps."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import IntercomManager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "expected_enabled"),
    [
        (None, False),
        ({"enabled": True}, True),
    ],
)
async def test_initialize_loads_persisted_switch_once(
    hass, stored, expected_enabled
) -> None:
    """Initialization hydrates persisted state once and is idempotent thereafter."""
    manager = IntercomManager(hass)
    load = AsyncMock(return_value=stored)
    manager._store.async_load = load

    await manager.async_initialize()
    await manager.async_initialize()

    assert manager.enabled is expected_enabled
    assert manager._loaded is True
    load.assert_awaited_once_with()


def test_target_match_without_device_uses_entity_labels(hass, monkeypatch) -> None:
    """A satellite with no device registry link can still match its own labels."""
    manager = IntercomManager(hass)
    entity = SimpleNamespace(
        entity_id="assist_satellite.porch",
        device_id=None,
        area_id=None,
        labels={"outside"},
    )
    registry = SimpleNamespace(async_get=lambda _entity_id: entity)
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: registry)

    assert manager._target_matches(
        entity.entity_id,
        entity_ids=set(),
        device_ids=set(),
        area_ids=set(),
        floor_ids=set(),
        label_ids={"outside"},
    )
