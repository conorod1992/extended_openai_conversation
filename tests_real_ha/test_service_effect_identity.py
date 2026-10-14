"""Handler-visible preparation and retention through genuine native exchanges."""

from copy import deepcopy

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses import (
    tool_replay_guard as guard,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    EffectReplayBlocked,
)
from homeassistant.components import conversation
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_prepared_effect_recovery import exchange_owner


async def _service_owner(hass, schema):
    effects, validations = [], []

    def validate(data):
        validations.append(deepcopy(data))
        return schema(data)

    async def apply(call):
        effects.append((call.data["amount"], type(call.data["amount"])))
        hass.states.async_set("sensor.probe", str(call.data["amount"]))

    hass.states.async_set("sensor.probe", "ready")
    hass.services.async_register("probe", "apply", apply, schema=validate)
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": "sensor.probe"}],
    )
    return entity, log, exchange, effects, validations


def _args(amount):
    return {
        "domain": "probe",
        "service": "apply",
        "service_data": {"amount": amount, "entity_id": "sensor.probe"},
    }


@pytest.mark.parametrize("same_wrapper", [False, True])
async def test_meaningful_number_types_are_distinct(hass, same_wrapper):
    entity, log, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": vol.Any(int, float), "entity_id": [str]})
    )
    await exchange(_args(1))
    guard.remember_unacknowledged_calls(entity, log, set())
    await exchange(_args(1.0), name="probe" if same_wrapper else "other")
    assert effects == [(1, int), (1.0, float)]
    assert hass.states.get("sensor.probe").state == "1.0"


async def test_schema_preparation_runs_once_and_retains_raw_event(hass):
    events = []
    unsub = hass.bus.async_listen(
        EVENT_CALL_SERVICE, lambda event: events.append(deepcopy(event.data))
    )
    entity, log, exchange, effects, validations = await _service_owner(
        hass, vol.Schema({"amount": vol.Coerce(int), "entity_id": [str]})
    )
    try:
        await exchange(_args("1"))
        guard.remember_unacknowledged_calls(entity, log, set())
        with pytest.raises(EffectReplayBlocked):
            await exchange(_args(1), name="other")
        await hass.async_block_till_done()
        assert len(validations) == 2
        assert effects == [(1, int)]
        assert events[-1]["service_data"]["amount"] == "1"
    finally:
        unsub()


async def test_retired_conversation_refuses_replay_until_acknowledged(hass):
    entity, _, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": int, "entity_id": [str]})
    )
    logs = []
    for index in range(33):
        log = conversation.ChatLog(hass, f"failure-{index}")
        await exchange(_args(index), chat_log=log)
        guard.remember_unacknowledged_calls(entity, log, set())
        logs.append(log)
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(_args(0), chat_log=logs[0])
    assert len(effects) == 33
    guard.clear_unacknowledged_calls(entity, logs[0].conversation_id)
    await exchange(_args(0), chat_log=logs[0])
    assert len(effects) == 34
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(_args(1), chat_log=logs[1])
    assert len(effects) == 34


async def test_lost_dispatch_provenance_refuses_actual_replay(hass, monkeypatch):
    monkeypatch.setattr(guard, "_MAX_DISPATCH_RECORDS", 4)
    entity, log, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": int, "entity_id": [str]})
    )
    for index in range(3):
        await exchange(_args(index))
    guard.remember_unacknowledged_calls(entity, log, set())
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(_args(0))
    assert effects == [(0, int), (1, int), (2, int)]
    guard.clear_unacknowledged_calls(entity, log.conversation_id)
    await exchange(_args(0))
    assert len(effects) == 4


async def test_acknowledged_turns_release_provenance_without_retirement(
    hass, monkeypatch
):
    monkeypatch.setattr(guard, "_MAX_DISPATCH_RECORDS", 2)
    entity, log, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": int, "entity_id": [str]})
    )
    for index in range(5):
        await exchange(_args(index))
        guard.clear_unacknowledged_calls(entity, log.conversation_id)
        assert not entity._dispatched_tool_inputs
        assert not entity._execution_effect_receipts
    assert len(effects) == 5
    assert not getattr(entity, "_retired_replay_conversations", ())


async def test_tombstone_capacity_remains_fail_closed(hass, monkeypatch):
    monkeypatch.setattr(guard, "_MAX_AMBIGUOUS_CONVERSATIONS", 1)
    monkeypatch.setattr(guard, "_MAX_RETIRED_CONVERSATIONS", 1)
    entity, _, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": int, "entity_id": [str]})
    )
    for index in range(3):
        log = conversation.ChatLog(hass, f"overflow-{index}")
        await exchange(_args(index), chat_log=log)
        guard.remember_unacknowledged_calls(entity, log, set())
    fresh = conversation.ChatLog(hass, "fresh-after-overflow")
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(_args(9), chat_log=fresh)
    guard.clear_unacknowledged_calls(entity, fresh.conversation_id)
    await exchange(_args(9), chat_log=fresh)
    assert effects == [(0, int), (1, int), (2, int), (9, int)]
    assert len(entity._retired_replay_conversations) == 1
