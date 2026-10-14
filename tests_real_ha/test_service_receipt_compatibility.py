"""Replay settlement and the isolated genuine HA prepared-service boundary."""

from copy import deepcopy
from threading import get_ident

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses import (
    execution_outcomes as outcomes,
    tool_replay_guard as guard,
)
from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    _async_call_ha_action_unchecked,
)
from homeassistant.const import EVENT_CALL_SERVICE, EVENT_SERVICE_REGISTERED
from homeassistant.core import Context, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_prepared_effect_recovery import exchange_owner
from tests_real_ha.test_service_effect_identity import _args, _service_owner


async def test_rejected_receipt_eviction_does_not_retire_safe_conversation(
    hass, monkeypatch
):
    monkeypatch.setattr(outcomes, "_MAX_RETAINED_EFFECT_RECEIPTS", 2)
    entity, log, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": int, "entity_id": [str]})
    )
    for index in range(4):
        await exchange(_args(f"invalid-{index}"))
        guard.remember_unacknowledged_calls(entity, log, set())
    assert effects == []
    # Discarded authoritative refusals cannot masquerade as unknown effects.
    await exchange(_args(5))
    assert effects == [(5, int)]
    assert hass.states.get("sensor.probe").state == "5"
    guard.clear_unacknowledged_calls(entity, log.conversation_id)
    for name in (
        "_dispatched_tool_inputs",
        "_dispatched_tool_signatures",
        "_dispatched_tool_conversations",
        "_read_only_tool_inputs",
        "_execution_tool_outcomes",
        "_execution_effect_receipts",
    ):
        assert not getattr(entity, name, {})


@pytest.mark.parametrize("failure", ["handler", "later_schema"])
@pytest.mark.parametrize("changed", [False, True])
async def test_precise_unknown_or_partial_effect_guards_only_same_operation(
    hass, failure, changed
):
    effects = []

    async def apply(call):
        effects.append(call.data["amount"])
        hass.states.async_set("sensor.probe", str(call.data["amount"]))
        if failure == "handler" and call.data["amount"] == 1:
            raise vol.Invalid("after effect").with_traceback(None)

    hass.states.async_set("sensor.probe", "ready")
    hass.services.async_register(
        "probe",
        "apply",
        apply,
        schema=vol.Schema({"amount": int, "entity_id": [str]}),
    )
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service"},
        exposed=[{"entity_id": "sensor.probe"}],
    )
    first = [_args(1)]
    if failure == "later_schema":
        first.append(_args("invalid"))
    await exchange({"list": first, "noise": "first"})
    guard.remember_unacknowledged_calls(entity, log, set())
    retry = {"list": [_args(2 if changed else 1)], "noise": "retry"}
    # The whole-call input differs, but only the prepared child controls replay.
    if changed:
        await exchange(retry)
    else:
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await exchange(retry)
    assert effects == ([1, 2] if changed else [1])
    assert hass.states.get("sensor.probe").state == ("2" if changed else "1")


async def test_opaque_service_adapter_keeps_unknown_tool_guard_until_ack(
    hass, monkeypatch
):
    effects = []

    async def apply(call):
        effects.append(call.data["amount"])
        hass.states.async_set("sensor.probe", str(call.data["amount"]))
        if call.data["amount"] == 1:
            raise vol.Invalid("after effect")

    hass.states.async_set("sensor.probe", "ready")
    hass.services.async_register("probe", "apply", apply)
    original = hass.services

    class Adapter:
        def __getattr__(self, name):
            return getattr(original, name)

        async def async_call(self, **kwargs):
            return await original.async_call(**kwargs)

    monkeypatch.setattr(hass, "services", Adapter())
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": "sensor.probe"}],
    )
    await exchange(_args(1))
    guard.remember_unacknowledged_calls(entity, log, set())
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(_args(2))
    assert effects == [1]
    assert hass.states.get("sensor.probe").state == "1"
    guard.clear_unacknowledged_calls(entity, log.conversation_id)
    await exchange(_args(2))
    assert effects == [1, 2]
    assert hass.states.get("sensor.probe").state == "2"


@pytest.mark.parametrize("mode", list(SupportsResponse))
@pytest.mark.parametrize("job_kind", ["async", "callback", "executor"])
async def test_prepared_dispatch_preserves_ha_job_events_context_and_response(
    hass, mode, job_kind
):
    observed, validations, events, registrations = [], [], [], []
    loop_thread = get_ident()
    context = Context(user_id="receipt-user")

    def validate(data):
        validations.append(deepcopy(data))
        return vol.Schema({"amount": vol.Coerce(int)})(data)

    def perform(call):
        observed.append(
            (
                call.data["amount"],
                type(call.data["amount"]),
                call.context.user_id,
                call.return_response,
                get_ident(),
            )
        )
        return (
            {"amount": call.data["amount"]}
            if mode is not SupportsResponse.NONE
            else None
        )

    async def async_handler(call):
        return perform(call)

    handler = (
        async_handler
        if job_kind == "async"
        else callback(perform)
        if job_kind == "callback"
        else perform
    )
    hass.services.async_register(
        "probe", "apply", handler, schema=validate, supports_response=mode
    )
    await hass.async_block_till_done()
    registration = hass.services.async_services_for_domain("probe")["apply"]
    unsub = hass.bus.async_listen(
        EVENT_CALL_SERVICE, lambda event: events.append(event)
    )
    unsub_registered = hass.bus.async_listen(
        EVENT_SERVICE_REGISTERED, lambda event: registrations.append(event)
    )
    entity, _, _ = exchange_owner(
        hass, {"type": "native", "name": "execute_service_single"}
    )
    from homeassistant.helpers import llm

    call = llm.ToolInput(tool_name="probe", tool_args={}, id="job")
    try:
        with execution_attempt(
            entity,
            call,
            {"function": {"type": "native", "name": "execute_service_single"}},
        ):
            result = await _async_call_ha_action_unchecked(
                hass,
                "probe",
                "apply",
                data={"amount": "1"},
                blocking=True,
                context=context,
                include_response=True,
            )
        await hass.async_block_till_done()
        assert len(validations) == 1
        assert observed[0][:4] == (
            1,
            int,
            "receipt-user",
            mode is not SupportsResponse.NONE,
        )
        assert (observed[0][4] == loop_thread) is (job_kind != "executor")
        assert result == (
            {} if mode is SupportsResponse.NONE else {"response": {"amount": 1}}
        )
        assert events[-1].data["service_data"] == {"amount": "1"}
        assert events[-1].context is context
        assert registrations == []
        assert hass.services.async_services_for_domain("probe")["apply"] is registration
        assert entity._execution_tool_outcomes[id(call)] == "completed"
    finally:
        unsub()
        unsub_registered()


async def test_schema_registration_change_refuses_both_handlers(hass):
    effects = []

    async def original(call):
        effects.append("original")

    async def replacement(call):
        effects.append("replacement")

    def validate(data):
        hass.services.async_register("probe", "apply", replacement)
        return data

    hass.services.async_register("probe", "apply", original, schema=validate)
    with pytest.raises(HomeAssistantError, match="changed during preparation"):
        await _async_call_ha_action_unchecked(hass, "probe", "apply", blocking=True)
    await hass.async_block_till_done()
    assert effects == []
    # The replacement remains usable through HA's ordinary public dispatch.
    await hass.services.async_call("probe", "apply", blocking=True)
    assert effects == ["replacement"]
