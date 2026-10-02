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


async def test_native_intercom_real_stability_expiry_and_playback_acknowledgement(
    hass,
    monkeypatch,
    stress_trace,
):
    """Actual HA timers and software playback enforce existing queue semantics."""
    import asyncio
    from time import monotonic

    from homeassistant.components.assist_pipeline.pipeline import KEY_ASSIST_PIPELINE
    from homeassistant.components.assist_satellite.entity import AssistSatelliteState
    from tests_real_ha.test_assist_streaming_speech_processing import _speech_agent
    from tests_stress.test_native_audio_delivery import (
        _install_audio_entities,
        _SoftwareSatellite,
        _test_wav,
    )

    agent = await _speech_agent(hass)
    satellite = _SoftwareSatellite("intercom-timing", announce=True)
    entities, _ = await _install_audio_entities(
        hass, _test_wav(), satellites=[satellite]
    )
    store = hass.data[KEY_ASSIST_PIPELINE].pipeline_store
    pipeline = await store.async_create_item(
        {
            "name": "Native timed Intercom",
            "language": "en",
            "conversation_language": "en",
            "conversation_engine": agent.entity_id,
            "stt_engine": entities["stt"].entity_id,
            "stt_language": "en",
            "tts_engine": entities["tts"].entity_id,
            "tts_language": "en",
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": False,
        }
    )
    store.async_set_preferred_item(pipeline.id)
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    assert intercom.IDLE_STABILITY_SECONDS == 0.5
    expiries = {}
    original_expire = manager._expire

    def observed_expire(message_id):
        expiries[message_id] = monotonic()
        original_expire(message_id)

    monkeypatch.setattr(manager, "_expire", observed_expire)

    def status(message_id):
        row = next(item for item in manager.history() if item["id"] == message_id)
        return row["deliveries"][satellite.entity_id]["status"]

    async def until(predicate, timeout=12):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.02)

    async def send(message):
        return await manager.async_send(
            message,
            entity_ids=[satellite.entity_id],
            ttl_seconds=5,
            source="native-timing",
        )

    satellite._set_state(AssistSatelliteState.RESPONDING)
    began = monotonic()
    expired = await send("Expire while this native satellite is busy")
    await until(lambda: status(expired["id"]) == "expired")
    assert expiries[expired["id"]] - began >= 5
    assert satellite.announcements == [] and manager._queues == {}

    held = await send("Playback waits for a real software acknowledgement")
    satellite.announce_release = asyncio.Event()
    satellite._set_state(AssistSatelliteState.IDLE)
    await until(lambda: status(held["id"]) == "waiting_idle", timeout=5)
    unstable_at = monotonic()
    await asyncio.sleep(0.1)
    satellite._set_state(AssistSatelliteState.RESPONDING)
    assert monotonic() - unstable_at < intercom.IDLE_STABILITY_SECONDS
    await asyncio.sleep(intercom.IDLE_STABILITY_SECONDS + 0.1)
    assert satellite.announcements == []
    assert status(held["id"]) == "queued_busy"
    stable_at = monotonic()
    satellite._set_state(AssistSatelliteState.IDLE)
    await asyncio.wait_for(satellite.announce_started.wait(), 10)
    assert monotonic() - stable_at >= intercom.IDLE_STABILITY_SECONDS
    assert status(held["id"]) == "delivering"
    assert satellite.state == AssistSatelliteState.RESPONDING
    assert len(satellite.announcements) == 1

    queued = await send("This queued message expires behind acknowledged playback")
    await until(lambda: queued["id"] in expiries and held["id"] in expiries)
    # Production intentionally keeps already delivering work over its TTL.
    assert status(held["id"]) == "delivering"
    assert status(queued["id"]) == "expired"
    assert len(satellite.announcements) == 1
    satellite.announce_release.set()
    await until(lambda: status(held["id"]) == "delivered")
    assert satellite.state == AssistSatelliteState.IDLE
    assert manager._queues == {}

    healthy = await send(
        "Healthy native delivery after expiry and delayed acknowledgement"
    )
    await until(lambda: status(healthy["id"]) == "delivered")
    await until(lambda: healthy["id"] in expiries)
    assert [item.message for item in satellite.announcements] == [
        held["message"],
        healthy["message"],
    ]
    assert status(expired["id"]) == status(queued["id"]) == "expired"
    assert status(held["id"]) == status(healthy["id"]) == "delivered"
    assert satellite.state == AssistSatelliteState.IDLE
    assert manager._queues == {} and not manager._draining
    await manager.async_set_enabled(False)
    record(
        stress_trace,
        "summary",
        layer="native-ha-timers",
        intercom_timing_cases=1,
        intercom_idle_flaps=1,
        actual_expiry_callbacks=len(expiries),
        delayed_playback_acknowledgements=1,
        intercom_deliveries=2,
        intercom_expired=2,
        intercom_timing_recoveries=1,
    )
