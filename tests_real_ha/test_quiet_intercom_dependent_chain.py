"""Quiet Hours and Intercom share live HA satellite availability and controls."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE, async_get_intercom,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    _config_from_data, async_get_quiet_hours,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services, _install_satellite_entities,
)


async def test_quiet_hours_queue_and_restore_share_real_satellite_state(
    hass, monkeypatch
):
    """Busy announcement defers while Quiet Hours remains applied, then delivers."""
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    monkeypatch.setattr(intercom, "async_call_later", lambda *_args, **_kwargs: None)
    entry = _make_entry("Quiet broadcast chain", include_ai_task=False)
    await _setup_entry(hass, entry)
    satellite, media, wake = _install_satellite_entities(
        hass, slug="linked", name="Linked satellite", volume=0.65, wake="on",
    )
    _install_control_services(hass)
    hass.states.async_set(
        satellite, "responding", {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
    )
    quiet = await async_get_quiet_hours(hass)
    inter = await async_get_intercom(hass)
    quiet._config = _config_from_data({
        "enabled": True, "start": "22:00", "end": "07:00",
        "max_volume": 0.20, "wake_sound": "off", "overrides": {},
    })
    deliveries = []

    async def announce(call):
        deliveries.append((call.data["message"], call.data["entity_id"]))

    hass.services.async_register("assist_satellite", "announce", announce)
    await inter.async_set_enabled(True)
    evening = datetime(2026, 10, 9, 22, 5, tzinfo=ZoneInfo("Europe/Dublin"))
    try:
        await quiet.async_reconcile(now=evening)
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(0.20)
        assert hass.states.get(wake).state == "off"
        result = await inter.async_send(
            "Linked broadcast", entity_ids=[satellite], ttl_seconds=120,
            source="linked-acceptance",
        )
        await hass.async_block_till_done()
        assert deliveries == []
        hass.states.async_set(
            satellite, "idle", {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
        )
        await hass.async_block_till_done()
        assert deliveries == [("Linked broadcast", satellite)]
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(0.20)
        await quiet.async_reconcile(now=evening + timedelta(hours=9))
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(0.65)
        assert hass.states.get(wake).state == "on"
        assert len([item for item in inter.history() if item["id"] == result["id"]]) == 1
    finally:
        await inter.async_set_enabled(False)
        await quiet.async_shutdown()
