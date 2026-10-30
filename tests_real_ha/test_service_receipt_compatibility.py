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
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    replay_safety,
)
from homeassistant.const import EVENT_CALL_SERVICE, EVENT_SERVICE_REGISTERED
from homeassistant.core import Context, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_prepared_effect_recovery import exchange_owner
from tests_real_ha.test_service_effect_identity import _args, _service_owner


async def test_uncertain_effect_replay_uses_ha_service_name_casing(hass):
    effects = []

    async def apply(call):
        effects.append(call.data["amount"])
        hass.states.async_set("sensor.probe", str(call.data["amount"]))
        raise HomeAssistantError("handler failed after applying effect")

    hass.states.async_set("sensor.probe", "ready")
    hass.services.async_register("probe", "apply", apply)
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": "sensor.probe"}],
    )
    await exchange(_args(1))
    guard.remember_unacknowledged_calls(entity, log, set())
    retry = {**_args(1), "domain": "PROBE", "service": "APPLY"}
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(retry, name="renamed")
    assert effects == [1]
    assert hass.states.get("sensor.probe").state == "1"


async def test_failed_provider_continuation_cannot_replay_uncertain_cased_service(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_TOOLS,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say
    from tests_real_ha.test_execution_result_lifecycle_contracts import _tool
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
    )

    agent = await _agent(
        hass,
        api_mode="chat_completions",
        **{
            CONF_FUNCTION_TOOLS: [
                _tool({"type": "native", "name": "execute_service_single"})
            ],
            "function_tool_error_recovery": True,
        },
    )
    effects = []

    async def apply(call):
        effects.append(call.data["amount"])
        hass.states.async_set("sensor.probe", str(call.data["amount"]))
        raise HomeAssistantError("after effect")

    hass.states.async_set("sensor.probe", "ready")
    hass.services.async_register("probe", "apply", apply)
    async_expose_entity(hass, "conversation", "sensor.probe", True)
    _install_wire(monkeypatch, agent, [_chat_sse_text("Ready")])
    seed = await _say(hass, agent, "hello")
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("first", "effect", _args(1)),
            (
                400,
                {
                    "error": {
                        "message": "continuation failed",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    failed = await _say(hass, agent, "apply", seed.conversation_id)
    assert failed.response.error_code is not None
    assert effects == [1]
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "retry", "effect", {**_args(1), "domain": "PROBE", "service": "APPLY"}
            ),
            _chat_sse_text("Recovered"),
        ],
    )
    await _say(hass, agent, "retry", failed.conversation_id)
    assert effects == [1]
    assert hass.states.get("sensor.probe").state == "1"


@pytest.mark.parametrize("dispatcher", ["entity", "generic"])
@pytest.mark.parametrize("uncertain", [False, True])
async def test_entity_service_replay_ignores_target_order_and_duplicates(
    hass, uncertain, dispatcher
):
    import logging

    from homeassistant.components.switch import SwitchEntity
    from homeassistant.helpers.entity_component import EntityComponent

    class ProbeSwitch(SwitchEntity):
        _attr_should_poll = False
        _attr_is_on = False

        def __init__(self, name):
            self._attr_name = name
            self._attr_unique_id = name
            self.effects = 0

        async def async_toggle(self, **kwargs):
            self.effects += 1
            self._attr_is_on = not self._attr_is_on
            self.async_write_ha_state()
            if uncertain:
                raise HomeAssistantError("uncertain toggle after effect")

    component = EntityComponent(logging.getLogger(__name__), "switch", hass)
    switches = [ProbeSwitch("Replay one"), ProbeSwitch("Replay two")]
    await component.async_add_entities(switches)
    component.async_register_entity_service("toggle", {}, "async_toggle")
    ids = [item.entity_id for item in switches]
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": item} for item in ids],
    )
    arguments = {
        "domain": "switch" if dispatcher == "entity" else "homeassistant",
        "service": "toggle",
        "service_data": {"entity_id": ids},
    }
    await exchange(arguments)
    guard.remember_unacknowledged_calls(entity, log, set())
    retries = [
        {"entity_id": targets}
        for targets in [list(reversed(ids)), [ids[0], ids[1], ids[0]]]
    ]
    if dispatcher == "entity":
        retries.extend(
            {"entity_id": ids, key: []}
            for key in ("area_id", "device_id", "floor_id", "label_id")
        )
    for data in retries:
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await exchange({**arguments, "service_data": data}, name="renamed")
    assert [item.effects for item in switches] == [1, 1]
    assert [hass.states.get(item).state for item in ids] == ["on", "on"]
    # A genuinely different target set remains a separate operation.
    await exchange(
        {**arguments, "service_data": {"entity_id": [ids[0]]}}, name="renamed"
    )
    assert [item.effects for item in switches] == [2, 1]


async def test_callable_entity_service_preserves_handler_visible_selector_order(hass):
    import logging

    from homeassistant.components.switch import SwitchEntity
    from homeassistant.helpers.entity_component import EntityComponent

    class OrderedSwitch(SwitchEntity):
        _attr_should_poll = False
        _attr_is_on = False

        def __init__(self, name):
            self._attr_name = self._attr_unique_id = name

    component = EntityComponent(logging.getLogger(__name__), "switch", hass)
    switches = [OrderedSwitch("Ordered one"), OrderedSwitch("Ordered two")]
    await component.async_add_entities(switches)
    effects = []

    async def choose_first(_entity, call):
        first = call.data["entity_id"][0]
        effects.append(first)
        hass.states.async_set("sensor.chosen", first)

    component.async_register_entity_service("choose_first", {}, choose_first)
    ids = [item.entity_id for item in switches]
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": item} for item in ids],
    )
    args = {
        "domain": "switch",
        "service": "choose_first",
        "service_data": {"entity_id": ids},
    }
    await exchange(args)
    guard.remember_unacknowledged_calls(entity, log, set())
    await exchange(
        {**args, "service_data": {"entity_id": list(reversed(ids))}}, name="renamed"
    )
    assert effects == [ids[0], ids[0], ids[1], ids[1]]
    assert hass.states.get("sensor.chosen").state == ids[1]


async def test_local_request_rule_functions_do_not_retire_provider_replay_history(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_TOOLS,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.setup import async_setup_component
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say
    from tests_real_ha.test_execution_result_lifecycle_contracts import (
        _arguments,
        _tool,
    )
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
    )

    monkeypatch.setattr(outcomes, "_MAX_RETAINED_EFFECT_RECEIPTS", 2)
    monkeypatch.setattr(guard, "_MAX_DISPATCH_RECORDS", 2)
    agent = await _agent(
        hass,
        api_mode="chat_completions",
        **{
            CONF_FUNCTION_TOOLS: [
                _tool({"type": "native", "name": "execute_service_single"})
            ]
        },
    )
    assert await async_setup_component(
        hass, "counter", {"counter": {"receipt": {"initial": 0}}}
    )
    await hass.async_block_till_done()
    async_expose_entity(hass, "conversation", "counter.receipt", True)
    for _ in range(6):
        await agent._async_execute_request_rule_function("effect", _arguments(), None)
    assert hass.states.get("counter.receipt").state == "6"
    assert not replay_safety(agent).history_incomplete
    for name in (
        "_execution_effect_receipts",
        "_execution_tool_outcomes",
        "_dispatched_tool_inputs",
        "_dispatched_tool_signatures",
        "_dispatched_tool_conversations",
    ):
        assert not getattr(agent, name, {})
    _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_tool_call("fresh", "effect", _arguments()), _chat_sse_text("Done")],
    )
    fresh = await _say(hass, agent, "fresh action")
    assert fresh.response.error_code is None
    assert hass.states.get("counter.receipt").state == "7"
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("uncertain", "effect", _arguments()),
            (
                400,
                {
                    "error": {
                        "message": "lost continuation",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    failed = await _say(hass, agent, "another action", fresh.conversation_id)
    assert failed.response.error_code is not None
    for _ in range(6):
        await agent._async_execute_request_rule_function("effect", _arguments(), None)
    assert hass.states.get("counter.receipt").state == "14"
    _install_wire(
        monkeypatch, agent, [_chat_sse_tool_call("retry", "effect", _arguments())]
    )
    retry = await _say(hass, agent, "retry unresolved action", failed.conversation_id)
    assert retry.response.error_code is not None
    assert hass.states.get("counter.receipt").state == "14"
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("another-fresh", "effect", _arguments()),
            _chat_sse_text("Done"),
        ],
    )
    another = await _say(hass, agent, "new independent conversation")
    assert another.response.error_code is None
    assert hass.states.get("counter.receipt").state == "15"


async def test_nested_provider_conversation_overrides_local_function_ownership(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_TOOLS,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say
    from tests_real_ha.test_execution_result_lifecycle_contracts import _tool
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
    )

    function = {"type": "native", "name": "execute_service_single"}
    bridge_tool = _tool(function)
    bridge_tool["spec"]["name"] = "bridge_function"
    agent = await _agent(
        hass,
        api_mode="chat_completions",
        **{CONF_FUNCTION_TOOLS: [_tool(function), bridge_tool]},
    )
    effects, nested_results = [], []

    async def apply(call):
        effects.append(call.data["amount"])
        hass.states.async_set("sensor.probe", str(call.data["amount"]))

    async def bridge(call):
        nested_results.append(
            await _say(hass, agent, "nested action", seed.conversation_id)
        )

    hass.states.async_set("sensor.probe", "ready")
    hass.services.async_register("probe", "apply", apply)
    hass.services.async_register("bridge", "invoke", bridge)
    async_expose_entity(hass, "conversation", "sensor.probe", True)
    _install_wire(monkeypatch, agent, [_chat_sse_text("Ready")])
    seed = await _say(hass, agent, "hello")
    assert seed.response.error_code is None
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("nested", "effect", _args(1)),
            (
                400,
                {
                    "error": {
                        "message": "lost continuation",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    await agent._async_execute_request_rule_function(
        "bridge_function",
        {"domain": "bridge", "service": "invoke", "entity_id": "sensor.probe"},
        None,
    )
    assert len(nested_results) == 1
    assert nested_results[0].response.error_code is not None
    assert effects == [1]
    assert all(
        call.tool_name == "effect" for call in agent._dispatched_tool_inputs.values()
    )

    # Retry from a normal conversation request, after the local Function exits.
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "retry", "effect", {**_args(1), "domain": "PROBE", "service": "APPLY"}
            )
        ],
    )
    retry = await _say(hass, agent, "retry", seed.conversation_id)
    assert retry.response.error_code is not None
    assert effects == [1]
    assert hass.states.get("sensor.probe").state == "1"


async def test_unknown_execution_ownership_still_retires_fail_closed(hass, monkeypatch):
    from types import SimpleNamespace

    from homeassistant.helpers import llm

    monkeypatch.setattr(outcomes, "_MAX_RETAINED_EFFECT_RECEIPTS", 2)
    owner = SimpleNamespace()
    effects = []

    async def apply(call):
        effects.append(call.data["amount"])

    hass.services.async_register("probe", "apply", apply)
    tool = {
        "spec": {"name": "unknown"},
        "function": {"type": "native", "name": "execute_service_single"},
    }
    for value in range(3):
        call = llm.ToolInput(
            id=str(value), tool_name="unknown", tool_args={"amount": value}
        )
        with execution_attempt(owner, call, tool):
            await _async_call_ha_action_unchecked(
                hass, "probe", "apply", data={"amount": value}, blocking=True
            )
    assert replay_safety(owner).history_incomplete
    with (
        guard.bind_effect_replay_scope(owner, "fresh-provider"),
        execution_attempt(
            owner, llm.ToolInput(id="fresh", tool_name="unknown", tool_args={}), tool
        ),
        pytest.raises(HomeAssistantError, match="acknowledged"),
    ):
        await _async_call_ha_action_unchecked(
            hass, "probe", "apply", data={"amount": 9}, blocking=True
        )
    assert effects == [0, 1, 2]


@pytest.mark.parametrize(
    "domain,service", [("probe", "apply"), ("homeassistant", "toggle")]
)
async def test_custom_service_target_and_payload_order_remain_significant(
    hass, domain, service
):
    effects = []

    async def apply(call):
        effects.append((list(call.data["entity_id"]), list(call.data["payload"])))

    ids = ["sensor.first", "sensor.second"]
    for entity_id in ids:
        hass.states.async_set(entity_id, "ready")
    if domain == "homeassistant":
        # The authorization resolver still checks target-domain support even
        # when an administrator replaces the generic power-service handler.
        hass.services.async_register("sensor", service, apply)
    hass.services.async_register(domain, service, apply)
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": item} for item in ids],
    )
    args = {
        "domain": domain,
        "service": service,
        "service_data": {"entity_id": ids, "payload": ["a", "b"]},
    }
    await exchange(args)
    assert effects == [(ids, ["a", "b"])]
    guard.remember_unacknowledged_calls(entity, log, set())
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(args, name="renamed")
    await exchange(
        {
            **args,
            "service_data": {"entity_id": list(reversed(ids)), "payload": ["a", "b"]},
        },
        name="renamed",
    )
    await exchange(
        {**args, "service_data": {"entity_id": ids, "payload": ["b", "a"]}},
        name="renamed",
    )
    assert effects == [
        (ids, ["a", "b"]),
        (list(reversed(ids)), ["a", "b"]),
        (ids, ["b", "a"]),
    ]


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


@pytest.mark.parametrize(
    "representation", ["entity", "area", "generic", "generic_first"]
)
async def test_completed_toggle_recovery_uses_resolved_effect_targets(
    hass, representation
):
    import logging

    from homeassistant.components.switch import SwitchEntity
    from homeassistant.helpers import area_registry as ar, entity_registry as er
    from homeassistant.helpers.entity_component import EntityComponent

    class ProbeSwitch(SwitchEntity):
        _attr_should_poll = False
        _attr_is_on = False
        _attr_name = "Equivalent replay"
        _attr_unique_id = "equivalent-replay-switch"
        toggles = 0
        offs = 0

        async def async_toggle(self, **kwargs):
            self.toggles += 1
            self._attr_is_on = not self._attr_is_on
            self.async_write_ha_state()

        async def async_turn_off(self, **kwargs):
            self.offs += 1
            self._attr_is_on = False
            self.async_write_ha_state()

    component = EntityComponent(logging.getLogger(__name__), "switch", hass)
    switch = ProbeSwitch()
    await component.async_add_entities([switch])
    component.async_register_entity_service("toggle", {}, "async_toggle")
    component.async_register_entity_service("turn_off", {}, "async_turn_off")
    registry = er.async_get(hass)
    if registry.async_get(switch.entity_id) is None:
        registered = registry.async_get_or_create(
            "switch",
            "switch",
            switch.unique_id,
            suggested_object_id=switch.entity_id.split(".", 1)[1],
        )
        assert registered.entity_id == switch.entity_id
    area = ar.async_get(hass).async_create("Only this switch")
    registry.async_update_entity(switch.entity_id, area_id=area.id)
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": switch.entity_id}],
    )
    original = {
        "domain": "homeassistant" if representation == "generic_first" else "switch",
        "service": "toggle",
        "service_data": {"entity_id": switch.entity_id},
    }
    await exchange(original)
    guard.remember_unacknowledged_calls(entity, log, set())
    retry = {
        **original,
        "domain": "homeassistant" if representation == "generic" else "switch",
        "service_data": {"area_id": area.id}
        if representation == "area"
        else original["service_data"],
    }
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(retry)
    assert switch.toggles == 1 and switch.is_on
    await exchange({**original, "service": "turn_off"})
    assert switch.offs == 1 and not switch.is_on
    guard.clear_unacknowledged_calls(entity, log.conversation_id)
    await exchange(original)
    assert switch.toggles == 2 and switch.is_on


@pytest.mark.parametrize("unsupported_available", [True, False])
async def test_explicit_feature_refusal_does_not_alias_corrected_effect(
    hass, unsupported_available
):
    """A full refusal differs from a correction; a skipped entity still aliases it."""
    import logging

    from homeassistant.components.fan import FanEntity, FanEntityFeature
    from homeassistant.exceptions import ServiceNotSupported
    from homeassistant.helpers.entity_component import EntityComponent

    effects = []

    class ProbeFan(FanEntity):
        _attr_should_poll = False
        _attr_is_on = False

        def __init__(self, name, features, available=True):
            self._attr_name = self._attr_unique_id = name
            self._attr_supported_features = features
            self._attr_available = available

        async def async_set_percentage(self, percentage):
            effects.append((self.entity_id, percentage))
            if not unsupported_available:
                raise HomeAssistantError("failure after effect")

    component = EntityComponent(logging.getLogger(__name__), "fan", hass)
    capable = ProbeFan("Speed capable", FanEntityFeature.SET_SPEED)
    unsupported = ProbeFan("No speed", FanEntityFeature.TURN_ON, unsupported_available)
    await component.async_add_entities([capable, unsupported])
    component.async_register_entity_service(
        "set_percentage",
        {vol.Required("percentage"): vol.Coerce(int)},
        "async_set_percentage",
        [FanEntityFeature.SET_SPEED],
    )
    if not unsupported_available:
        # Native tools reject unavailable explicit targets at their earlier policy
        # boundary. Check Core's skip semantics at the prepared-dispatch boundary
        # itself: its actual partial effect must share the corrected identity.
        from custom_components.extended_openai_conversation_responses.ha_service_compat import (
            prepare_service_dispatch,
        )

        initial = {
            "entity_id": [capable.entity_id, unsupported.entity_id],
            "percentage": 25,
        }
        registry, before = await prepare_service_dispatch(
            hass, "fan", "set_percentage", {"service_data": initial}
        )
        _, after = await prepare_service_dispatch(
            hass,
            "fan",
            "set_percentage",
            {"service_data": {"entity_id": capable.entity_id, "percentage": 25}},
        )
        assert before == after
        with pytest.raises(HomeAssistantError, match="failure after effect"):
            await registry.async_call("fan", "set_percentage", initial, blocking=True)
        assert effects == [(capable.entity_id, 25)]
        return
    entity, log, exchange = exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        exposed=[{"entity_id": item.entity_id} for item in (capable, unsupported)],
    )
    invalid = {
        "domain": "fan",
        "service": "set_percentage",
        "service_data": {
            "entity_id": [capable.entity_id, unsupported.entity_id],
            "percentage": 25,
        },
    }
    if unsupported_available:
        with pytest.raises(ServiceNotSupported):
            await hass.services.async_call(
                "fan", "set_percentage", invalid["service_data"], blocking=True
            )
        assert effects == []
    await exchange(invalid)
    guard.remember_unacknowledged_calls(entity, log, set())
    corrected = {
        **invalid,
        "service_data": {"entity_id": capable.entity_id, "percentage": 25},
    }
    if unsupported_available:
        assert effects == []
        await exchange(corrected)
    else:
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await exchange(corrected)
    assert effects == [(capable.entity_id, 25)]
