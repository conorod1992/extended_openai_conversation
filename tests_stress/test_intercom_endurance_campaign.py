"""Real HA Intercom queue behavior across mixed satellite state and reload."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    async_get_intercom,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_intercom_mixed_queue_survives_reload_failure_and_expiry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    # The production expiry callback is invoked explicitly at the selected
    # boundary; no wall-clock TTL wait or lingering HA timer is needed.
    monkeypatch.setattr(intercom, "async_call_later", lambda *_args, **_kwargs: None)
    entry = _make_entry("Intercom endurance", include_ai_task=False)
    await _setup_entry(hass, entry)
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)

    satellites = [f"assist_satellite.endurance_{index}" for index in range(8)]
    for index, entity_id in enumerate(satellites):
        state = "idle" if index in {0, 1, 2, 3, 7} else "responding"
        hass.states.async_set(
            entity_id, state, {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE}
        )
    calls: list[tuple[str, str]] = []
    fail_once = True

    async def announce(call: Any) -> None:
        nonlocal fail_once
        target = str(call.data["entity_id"])
        message = str(call.data["message"])
        if target == satellites[2] and fail_once:
            fail_once = False
            raise RuntimeError("deterministic announcement failure")
        calls.append((message, target))

    hass.services.async_register("assist_satellite", "announce", announce)
    count = 3 * stress_scale
    queued = []
    for index in range(count):
        item = await manager.async_send(
            f"Household broadcast {index}",
            whole_home=True,
            origin_entity_id=satellites[0],
            source="enhanced",
        )
        queued.append(item["id"])
    await hass.async_block_till_done()
    assert all(target != satellites[0] for _, target in calls)
    assert len({(message, target) for message, target in calls}) == len(calls)
    assert fail_once is False
    assert all(
        manager.history()[index]["deliveries"][satellites[4]]["status"] == "queued_busy"
        for index in range(count)
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_get_intercom(hass) is manager
    for target in satellites[4:6]:
        hass.states.async_set(
            target, "idle", {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE}
        )
    hass.states.async_remove(satellites[6])
    await hass.async_block_till_done()
    for message_id in queued:
        manager._expire(message_id)
    await hass.async_block_till_done()

    history = manager.history()
    assert len(history) == count
    for item in history:
        assert satellites[0] not in item["targets"]
        assert item["deliveries"][satellites[4]]["status"] == "delivered"
        assert item["deliveries"][satellites[5]]["status"] == "delivered"
        assert item["deliveries"][satellites[6]]["status"] == "expired"
    assert (
        sum(item["deliveries"][satellites[2]]["status"] == "failed" for item in history)
        == 1
    )
    expected = Counter(
        (f"Household broadcast {index}", target)
        for index in range(count)
        for target in (
            satellites[1],
            satellites[2],
            satellites[3],
            satellites[4],
            satellites[5],
            satellites[7],
        )
    )
    expected[("Household broadcast 0", satellites[2])] -= 1
    assert Counter(calls) == +expected
    record(
        stress_trace,
        "summary",
        layer="real-ha",
        intercom_broadcasts=count,
        intercom_satellites=len(satellites),
        intercom_deliveries=len(calls),
        intercom_failures=1,
        intercom_expired=count,
        ha_service_calls=len(calls) + 1,
    )
