"""Multi-step device and durable-data contracts against genuine HA storage."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from tests.memory_sequence_contract import run_memory_sequence
from tests_real_ha.test_quiet_hours_acceptance import (
    _active_window,
    _install_control_services,
    _install_satellite_entities,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.usefixtures("real_store_io")
async def test_memory_sequence_reloads_committed_real_ha_store(hass):
    await run_memory_sequence(
        lambda: HomeAssistantMemoryStorage(hass, "sequence", "agent"),
        seed=1060,
        steps=24,
        trace=[],
    )
    assert list(
        (Path(hass.config.config_dir) / ".storage").glob(
            f"{DOMAIN}.memory.sequence.agent"
        )
    )


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("independent_change", [False, True])
async def test_unavailable_settings_failure_disable_restart_return_sequence(
    hass, monkeypatch, independent_change
):
    _, media, wake = _install_satellite_entities(hass)
    _install_control_services(hass)
    start, end = _active_window()
    manager = await async_get_quiet_hours(hass)
    await manager.async_update_config(
        {
            "enabled": True,
            "start": start,
            "end": end,
            "max_volume": 0.2,
            "wake_sound": "off",
        }
    )
    baseline = deepcopy(manager.active)
    hass.states.async_set(media, "unavailable", {"volume_level": 0.2})
    hass.states.async_set(wake, "unavailable")
    try:
        with monkeypatch.context() as failure:
            failure.setattr(
                manager._store,
                "async_save",
                AsyncMock(side_effect=OSError("controlled outage")),
            )
            with pytest.raises(OSError, match="controlled outage"):
                await manager.async_set_enabled(False)
        assert manager.config.enabled
        assert manager.active == baseline
        await manager.async_set_enabled(False)
        assert all(
            control["restoration_pending"]
            for control in manager.active["controls"].values()
        )
        await manager.async_shutdown()
        assert not manager._unsubscribers
        hass.data[DOMAIN].pop("quiet_hours_manager")
        manager = await async_get_quiet_hours(hass)
        assert not manager.config.enabled
        assert manager.active["controls"][media]["original_value"] == pytest.approx(0.6)
        assert manager.active["controls"][wake]["original_value"] is True
        hass.states.async_set(
            media, "idle", {"volume_level": 0.4 if independent_change else 0.2}
        )
        hass.states.async_set(wake, "on" if independent_change else "off")
        await manager.async_reconcile()
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(
            0.4 if independent_change else 0.6
        )
        assert hass.states.get(wake).state == "on"
        assert manager.active is None
        assert not manager._unsubscribers
        # A second reconciliation cannot reapply the disabled policy.
        await manager.async_reconcile()
        assert manager.active is None
    finally:
        await manager.async_shutdown()
        await hass.async_block_till_done()
