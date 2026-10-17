"""Actual HA effects across Script retries and generic delegate refusals."""

from copy import deepcopy
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from custom_components.extended_openai_conversation_responses import (
    tool_replay_guard as guard,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.functions.base import (
    copy_runtime_function_config,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from homeassistant.components import conversation
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.setup import async_setup_component


def _exchange_owner(hass, function, exposed):
    """Use real exchange/receipt boundaries, retaining hydrated Script templates."""
    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.replay_probe",
        should_run_in_background=lambda _: False,
    )
    entity._execute_function_tool = lambda *args: (
        ExtendedOpenAIBaseLLMEntity._execute_function_tool(entity, *args)
    )
    log = conversation.ChatLog(hass, uuid4().hex)

    async def exchange(arguments):
        tool = {
            "spec": {
                "name": "probe",
                "parameters": {"type": "object", "additionalProperties": True},
            },
            "function": copy_runtime_function_config(function),
        }
        call = llm.ToolInput(
            tool_name="probe", tool_args=deepcopy(arguments), id=uuid4().hex
        )
        log.content.append(
            conversation.AssistantContent(agent_id=entity.entity_id, tool_calls=[call])
        )
        await async_execute_tool_exchange(
            entity,
            log,
            [call],
            [tool],
            FunctionCallBudget(10),
            SimpleNamespace(context=Context()),
            exposed,
        )

    return entity, log, exchange


@pytest.mark.parametrize(
    "first,second",
    [
        ("counter.one", "counter.one"),
        ("counter.one", ["counter.one"]),
        (["counter.one"], "counter.one"),
        (["counter.one", "counter.two"], ["counter.two", "counter.one"]),
        (["counter.one", "counter.one"], ["counter.one"]),
        ("counter.one", "counter.two"),
    ],
    ids=[
        "identical",
        "string-list",
        "list-string",
        "selector-order",
        "duplicate-selector",
        "different-effect",
    ],
)
@pytest.mark.parametrize("abort", [False, True], ids=["completed", "partial-abort"])
async def test_script_retry_matches_actual_service_effect(hass, first, second, abort):
    assert await async_setup_component(
        hass,
        "counter",
        {
            "counter": {
                "one": {"initial": 10, "step": 2},
                "two": {"initial": 10, "step": 2},
            }
        },
    )
    await hass.async_block_till_done()
    definition = {
        "spec": {
            "name": "probe",
            "parameters": {"type": "object", "additionalProperties": True},
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": "counter.decrement",
                    "target": {"entity_id": "{{ target }}"},
                },
                {
                    "if": "{{ fail }}",
                    "then": [{"stop": "controlled abort after effect", "error": True}],
                },
            ],
        },
    }
    function = configured_function_tools_from_data({"functions": [definition]})[0][
        "function"
    ]
    entity, log, exchange = _exchange_owner(
        hass, function, [{"entity_id": "counter.one"}, {"entity_id": "counter.two"}]
    )
    await exchange({"target": first, "fail": abort})
    first_states = {
        name: hass.states.get(name).state for name in ("counter.one", "counter.two")
    }
    assert first_states["counter.one"] == "8"
    # This is the same recovery boundary used when a provider continuation fails.
    guard.remember_unacknowledged_calls(entity, log, set())
    if second == "counter.two":
        await exchange({"target": second, "fail": False})
        assert hass.states.get("counter.two").state == "8"
    else:
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await exchange({"target": second, "fail": False})
        assert {
            name: hass.states.get(name).state for name in first_states
        } == first_states
    guard.clear_unacknowledged_calls(entity, log.conversation_id)
    await exchange({"target": first, "fail": False})
    assert hass.states.get("counter.one").state == "6"


@pytest.mark.parametrize("service", ["turn_on", "turn_off", "toggle"])
@pytest.mark.parametrize(
    "domain,mixed,indirect",
    [
        ("fan", False, False),
        ("homeassistant", False, False),
        ("homeassistant", True, False),
        ("homeassistant", False, True),
        ("homeassistant", True, True),
    ],
    ids=[
        "direct-refusal",
        "generic-refusal",
        "generic-partial",
        "generic-area-refusal",
        "generic-area-partial",
    ],
)
async def test_power_refusal_correction_preserves_partial_effect_guard(
    hass, domain, mixed, indirect, service
):
    assert await async_setup_component(hass, "homeassistant", {})
    effects = []

    class ProbeFan(FanEntity):
        _attr_should_poll = False
        _attr_is_on = False
        _enable_turn_on_off_backwards_compatibility = False

        def __init__(self, name, features):
            self._attr_name = self._attr_unique_id = name
            self._attr_supported_features = features

        async def async_turn_on(self, **kwargs):
            effects.append(self.entity_id)

        async def async_turn_off(self, **kwargs):
            effects.append(self.entity_id)

        async def async_toggle(self, **kwargs):
            effects.append(self.entity_id)

    fan_component = EntityComponent(logging.getLogger(__name__), "fan", hass)
    power_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    required = (
        FanEntityFeature.TURN_ON
        if service == "turn_on"
        else FanEntityFeature.TURN_OFF
        if service == "turn_off"
        else power_features
    )
    capable = ProbeFan("Capable", power_features)
    unsupported = ProbeFan("Unsupported", FanEntityFeature.SET_SPEED)
    await fan_component.async_add_entities([capable, unsupported])
    fan_component.async_register_entity_service(
        service, {}, f"async_{service}", [required]
    )
    other = ProbeFan("Other domain", power_features)
    other_component = EntityComponent(logging.getLogger(__name__), "light", hass)
    await other_component.async_add_entities([other])
    other_component.async_register_entity_service(
        service, {}, f"async_{service}", [required]
    )
    selected = [capable.entity_id, unsupported.entity_id] + (
        [other.entity_id] if mixed else []
    )
    entity, log, exchange = _exchange_owner(
        hass,
        {"type": "native", "name": "execute_service_single"},
        [{"entity_id": item} for item in selected],
    )

    def args(ids):
        return {
            "domain": domain,
            "service": service,
            "service_data": ids if isinstance(ids, dict) else {"entity_id": ids},
        }

    initial = selected
    if indirect:
        from homeassistant.helpers import area_registry, entity_registry

        area = area_registry.async_get(hass).async_create("Delegate refusal area")
        for item in selected:
            entity_registry.async_get(hass).async_update_entity(item, area_id=area.id)
        initial = {"area_id": area.id}
    await exchange(args(initial))
    await hass.async_block_till_done()
    assert effects == ([other.entity_id] if mixed else [])
    guard.remember_unacknowledged_calls(entity, log, set())
    corrected = [item for item in selected if item != unsupported.entity_id]
    if mixed:
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await exchange(args(corrected))
        assert effects == [other.entity_id]
        guard.clear_unacknowledged_calls(entity, log.conversation_id)
        await exchange(args(corrected))
        assert effects.count(other.entity_id) == 2
        assert effects.count(capable.entity_id) == 1
    else:
        await exchange(args(corrected))
        assert effects == [capable.entity_id]
