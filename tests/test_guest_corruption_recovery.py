"""Corrupt Guest state cannot silently restore unrestricted access."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock
import pytest
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    StorageRepairRequired,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"broken": True},
        {"schedule": []},
        {"schedule": {"active_from": "bad"}},
        {
            "schedule": {
                "active_from": "2026-01-02T00:00:00+00:00",
                "active_until": "2026-01-01T00:00:00+00:00",
            }
        },
        {"schedule": {"active_from": "2026-01-01T00:00:00+00:00", "source": 7}},
    ],
)
async def test_malformed_guest_state_blocks_policy_and_preserves_repair_evidence(
    hass, data
):
    manager = GuestModeManager(hass, "entry", "agent")
    await manager._store.async_save(data)
    from pathlib import Path

    path = Path(manager._store.path)
    evidence = path.read_bytes()
    for _ in range(2):
        with pytest.raises(StorageRepairRequired):
            await manager.async_initialize()
        assert not manager._initialized
        with pytest.raises(HomeAssistantError):
            manager.is_active()
        assert path.read_bytes() == evidence
    await manager._store.async_save(
        {"schedule": {"active_from": "2026-01-01T00:00:00+00:00"}}
    )
    await manager.async_initialize()
    assert manager.is_active()
    await manager.async_disable_trusted()
    fresh = GuestModeManager(hass, "entry", "agent")
    await fresh.async_initialize()
    assert not fresh.is_active()


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"schedule": None},
        {"schedule": {"active_from": "2026-01-01T00:00:00+00:00"}},
    ],
)
async def test_legacy_and_inactive_guest_state_remain_compatible(hass, data):
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = Mock(async_load=AsyncMock(return_value=data))
    await manager.async_initialize()
    assert manager.is_active() is bool(data and data.get("schedule"))


async def test_portable_guest_replacement_cannot_introduce_an_inverted_interval(hass):
    manager = GuestModeManager(hass, "entry", "agent")
    await manager.async_initialize()
    await manager.async_update_trusted(indefinite=True)
    committed = await manager.async_backup_data()
    damaged = deepcopy(committed)
    damaged["schedule"]["active_until"] = "2025-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="interval"):
        replacement = manager.validate_backup_data(damaged)
        await manager.async_replace_backup(replacement)
    fresh = GuestModeManager(hass, "entry", "agent")
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == committed and fresh.is_active()


async def test_guest_read_failure_never_publishes_an_unrestricted_cached_manager(hass):
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = Mock(async_load=AsyncMock(side_effect=OSError("read failed")))
    with pytest.raises(OSError):
        await manager.async_initialize()
    with pytest.raises(HomeAssistantError):
        manager.is_active()
    manager._store.async_load.side_effect = None
    manager._store.async_load.return_value = {
        "schedule": {"active_from": "2026-01-01T00:00:00+00:00"}
    }
    await manager.async_initialize()
    assert manager.is_active()
