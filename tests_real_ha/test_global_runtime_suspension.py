"""HA config-entry unload/reload suspends global effects without erasing preferences."""

from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses.global_runtime import (
    LOADED_ENTRIES,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    async_get_intercom,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)


@pytest.mark.parametrize("offline", [False, True])
async def test_last_loaded_entry_unload_and_reload_preserve_global_policy(
    hass, offline
):
    first = _make_entry("First", include_ai_task=False)
    second = _make_entry("Second", include_ai_task=False)
    await _setup_entry(hass, first)
    await _setup_entry(hass, second)
    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    _install_control_services(hass)
    quiet = await async_get_quiet_hours(hass)
    now = dt_util.now()
    await quiet.async_update_config(
        {
            "enabled": True,
            "start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "max_volume": 0.2,
            "wake_sound": "unchanged",
        }
    )
    broadcast = await async_get_intercom(hass)
    await broadcast.async_set_enabled(True)
    await broadcast.async_set_builtin_voice_command(False)
    identity = quiet._state_entity_id()
    registry = er.async_get(hass)
    registry.async_update_entity(identity, name="Saved Quiet Policy")
    assert hass.states.get(media).attributes["volume_level"] == 0.2
    assert await hass.config_entries.async_unload(first.entry_id)
    assert first.state is ConfigEntryState.NOT_LOADED
    assert hass.data[LOADED_ENTRIES] == {second.entry_id}
    assert await async_get_quiet_hours(hass) is quiet
    if offline:
        hass.states.async_set(media, "unavailable", {"volume_level": 0.2})
    assert await hass.config_entries.async_unload(second.entry_id)
    assert hass.data[LOADED_ENTRIES] == set()
    assert hass.states.get(identity) is None
    assert registry.async_get(identity).name == "Saved Quiet Policy"
    assert not quiet._unsubscribers and quiet._registry_unsubscribe is None
    if offline:
        stored = await quiet._store.async_load()
        assert stored["active"]["controls"][media]["original_value"] == 0.8
        hass.states.async_set(media, "idle", {"volume_level": 0.2})
    else:
        assert hass.states.get(media).attributes["volume_level"] == 0.8
    for getter in [async_get_quiet_hours, async_get_intercom]:
        with pytest.raises(HomeAssistantError, match="loaded"):
            await getter(hass)
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    resumed = await async_get_quiet_hours(hass)
    assert resumed is not quiet
    assert resumed.active["controls"][media]["original_value"] == 0.8
    assert hass.states.get(media).attributes["volume_level"] == 0.2
    assert resumed._state_entity_id() == identity
    resumed_broadcast = await async_get_intercom(hass)
    assert resumed_broadcast.enabled and not resumed_broadcast.builtin_voice_command
    assert resumed_broadcast.pending() == []
    assert await hass.config_entries.async_reload(first.entry_id)
    assert hass.data[LOADED_ENTRIES] == {first.entry_id}
    assert (await async_get_quiet_hours(hass)).active["controls"][media][
        "original_value"
    ] == 0.8
    assert await hass.config_entries.async_unload(first.entry_id)


@pytest.mark.parametrize("offline", [False, True])
async def test_crash_startup_restores_without_successful_provider_setup(
    hass, monkeypatch, offline
):
    from unittest.mock import AsyncMock

    import httpx
    from openai import AuthenticationError

    from custom_components import extended_openai_conversation_responses as integration
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_SKIP_AUTHENTICATION,
    )

    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    _install_control_services(hass)
    old = await async_get_quiet_hours(hass)
    now = dt_util.now()
    await old.async_update_config(
        {
            "enabled": True,
            "start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "max_volume": 0.2,
            "wake_sound": "unchanged",
        }
    )
    identity = old._state_entity_id()
    registry = er.async_get(hass)
    registry.async_update_entity(identity, name="Preserved startup identity")
    # Simulate an abrupt process loss: retire callbacks without restoring devices.
    old._stopping = True
    old._cancel_scheduled_callbacks()
    for attribute in ["_registry_unsubscribe", "_timezone_unsubscribe"]:
        callback = getattr(old, attribute)
        if callback is not None:
            callback()
            setattr(old, attribute, None)
    hass.data[integration.DOMAIN].pop("quiet_hours_manager")
    hass.states.async_remove(identity)
    if offline:
        hass.states.async_set(media, "unavailable", {"volume_level": 0.2})
    entry = _make_entry(
        "Failed after crash",
        include_ai_task=False,
        data={CONF_SKIP_AUTHENTICATION: False},
    )
    entry.add_to_hass(hass)
    error = AuthenticationError(
        "invalid",
        response=httpx.Response(
            401, request=httpx.Request("GET", "https://example.invalid")
        ),
        body=None,
    )
    with monkeypatch.context() as scoped:
        auth = AsyncMock(side_effect=error)
        scoped.setattr(integration, "get_authenticated_client", auth)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        auth.assert_awaited_once()
    await hass.async_block_till_done()
    assert entry.state is not ConfigEntryState.LOADED
    assert hass.data[LOADED_ENTRIES] == set()
    assert "quiet_hours_manager" not in hass.data[integration.DOMAIN]
    assert hass.states.get(identity) is None
    assert registry.async_get(identity).name == "Preserved startup identity"
    saved = await old._store.async_load()
    if offline:
        control = saved["active"]["controls"][media]
        assert control["original_value"] == 0.8 and control["restoration_pending"]
        hass.states.async_set(media, "idle", {"volume_level": 0.2})
    else:
        assert saved["active"] is None
        assert hass.states.get(media).attributes["volume_level"] == 0.8
    for getter in [async_get_quiet_hours, async_get_intercom]:
        with pytest.raises(HomeAssistantError):
            await getter(hass)
    # A later successful enable uses the true original volume again.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_SKIP_AUTHENTICATION: True}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    resumed = await async_get_quiet_hours(hass)
    assert resumed.active["controls"][media]["original_value"] == 0.8
    assert hass.states.get(media).attributes["volume_level"] == 0.2
    assert await hass.config_entries.async_unload(entry.entry_id)
