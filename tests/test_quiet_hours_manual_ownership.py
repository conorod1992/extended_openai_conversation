"""An observed manual adjustment permanently ends restoration ownership."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours_runtime as runtime
from tests.test_quiet_hours_restoration_regressions import _active
from tests.test_quiet_hours_runtime import _stateful_public_manager


@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("restart", [False, True])
async def test_unchanged_target_releases_manual_adjustment_before_later_restore(hass, kind, restart):
    manager = _stateful_public_manager(hass)
    manager._config = runtime.QuietHoursConfig(enabled=True, max_volume=0.2, wake_sound="off")
    entity_id = "media_player.bedroom" if kind == "volume" else "switch.wake_sound"
    manager._active = _active(entity_id, kind)
    manager._active["controls"][entity_id]["ceiling_attempts"] = 3
    if restart:
        manager._active = manager._normalize_active(deepcopy(manager.active))
    state = SimpleNamespace(state="idle" if kind == "volume" else "on", attributes={"volume_level": 0.4}, context=SimpleNamespace(id="manual"))
    hass.states.get.side_effect = lambda _: state
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    capability = runtime.SatelliteCapabilities(
        satellite_entity_id="assist_satellite.bedroom", name="Bedroom", device_id="bedroom",
        media_player_entity_id=entity_id if kind == "volume" else None,
        wake_sound_entity_id=entity_id if kind == "switch" else None,
        media_player_source=None, wake_sound_source=None,
    )
    await manager._async_reconcile_controls_locked([capability])
    assert entity_id not in manager.active["controls"]
    # The user subsequently chooses the quiet value again. It is still theirs.
    state.attributes["volume_level"] = 0.2
    state.state = "idle" if kind == "volume" else "off"
    await manager._async_restore_locked()
    manager._async_set_volume.assert_not_awaited()
    manager._async_set_switch.assert_not_awaited()
