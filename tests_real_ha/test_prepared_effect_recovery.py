"""Independent genuine-HA probes for the additional automation/broadcast findings."""

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pytest_homeassistant_custom_component.common import MockUser
import yaml

from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    async_get_intercom,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    remember_unacknowledged_calls,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component


def exchange_owner(hass, function, context=None, *, exposed=()):
    tool = {
        "spec": {
            "name": "probe",
            "parameters": {"type": "object", "additionalProperties": True},
        },
        "function": function,
    }
    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.probe",
        should_run_in_background=lambda delay: False,
    )

    async def execute(definition, call, llm_context, exposed):
        with execution_attempt(entity, call, definition):
            return await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
                entity, definition, call, llm_context, exposed
            )

    entity._execute_function_tool = execute
    log = conversation.ChatLog(hass, uuid4().hex)

    async def exchange(arguments, *, name="probe", chat_log=None):
        current_log = chat_log or log
        current_tool = deepcopy(tool)
        current_tool["spec"]["name"] = name
        call = llm.ToolInput(
            tool_name=name, tool_args=deepcopy(arguments), id=uuid4().hex
        )
        current_log.content.append(
            conversation.AssistantContent(agent_id=entity.entity_id, tool_calls=[call])
        )
        await async_execute_tool_exchange(
            entity,
            current_log,
            [call],
            [current_tool],
            FunctionCallBudget(10),
            context,
            list(exposed),
        )
        return call

    return entity, log, exchange


@pytest.mark.parametrize(
    "variation", ["ignored_argument", "discarded_id", "identical", "changed_definition"]
)
async def test_automation_replay(hass, variation):
    path = Path(hass.config.config_dir, "automations.yaml")
    path.write_text("[]\n", encoding="utf8")
    Path(hass.config.config_dir, "configuration.yaml").write_text(
        "automation: !include automations.yaml\n", encoding="utf8"
    )
    effects = []

    async def record(call):
        effects.append(call.data["marker"])

    hass.services.async_register("probe", "record", record)
    assert await async_setup_component(hass, "automation", {"automation": []})
    user = MockUser(id="probe-admin", is_owner=True)
    user.add_to_hass(hass)
    context = SimpleNamespace(context=Context(user_id=user.id), device_id=None)
    function = {
        "type": "composite",
        "sequence": [
            {"type": "native", "name": "add_automation"},
            {"type": "template", "value_template": "{{ noise }}"},
        ],
    }
    entity, log, exchange = exchange_owner(hass, function, context)
    config = {
        "id": "model-a",
        "alias": "Probe",
        "triggers": [{"trigger": "event", "event_type": "probe_trigger"}],
        "actions": [{"action": "probe.record", "data": {"marker": "created"}}],
    }
    first = {"automation_config": yaml.safe_dump(config), "noise": "before"}
    await exchange(first)
    remember_unacknowledged_calls(entity, log, set())
    retry = deepcopy(first)
    if variation == "ignored_argument":
        retry["noise"] = "after"
    elif variation == "discarded_id":
        retry["automation_config"] = yaml.safe_dump({**config, "id": "model-b"})
    elif variation == "changed_definition":
        retry["automation_config"] = yaml.safe_dump(
            {
                **config,
                "alias": "Different automation",
                "actions": [{"action": "probe.record", "data": {"marker": "changed"}}],
            }
        )
    blocked = False
    try:
        await exchange(retry)
    except HomeAssistantError as err:
        assert "not acknowledged" in str(err)
        blocked = True
    saved = yaml.safe_load(path.read_text(encoding="utf8"))
    assert blocked == (variation != "changed_definition")
    assert len(saved) == (1 if blocked else 2)
    assert saved[0]["id"] != "model-a"
    hass.bus.async_fire("probe_trigger")
    await hass.async_block_till_done()
    assert len(effects) == len(saved)
    if variation == "changed_definition":
        assert sorted(effects) == ["changed", "created"]
    print(
        "AUTOMATION",
        variation,
        "blocked",
        blocked,
        "persisted",
        len(saved),
        "actual triggered effects",
        effects,
    )


@pytest.mark.parametrize(
    "variation", ["whitespace_equivalent", "identical", "changed_message"]
)
async def test_broadcast_equivalence(hass, variation):
    hass.states.async_set(
        "assist_satellite.probe", "responding", {"supported_features": ANNOUNCE_FEATURE}
    )
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    entity, log, exchange = exchange_owner(
        hass, {"type": "native", "name": "send_broadcast"}
    )
    try:
        await exchange({"message": " Dinner is ready ", "whole_home": True})
        remember_unacknowledged_calls(entity, log, set())
        retry = {
            "whitespace_equivalent": "Dinner is ready",
            "identical": " Dinner is ready ",
            "changed_message": "Lunch is ready",
        }[variation]
        blocked = False
        try:
            await exchange({"message": retry, "whole_home": True})
        except HomeAssistantError as err:
            assert "not acknowledged" in str(err)
            blocked = True
        queued = [item.message for item in manager._queues["assist_satellite.probe"]]
        assert blocked == (variation != "changed_message")
        assert queued == (
            ["Dinner is ready"] if blocked else ["Dinner is ready", retry.strip()]
        )
        print("BROADCAST", variation, "blocked", blocked, "queued", queued)
        deliveries = []

        async def announce(call):
            deliveries.append(call.data["message"])

        hass.services.async_register("assist_satellite", "announce", announce)
        hass.states.async_set(
            "assist_satellite.probe", "idle", {"supported_features": ANNOUNCE_FEATURE}
        )
        await asyncio.sleep(1.3)
        await hass.async_block_till_done()
        assert deliveries == queued
        print("ACTUAL ANNOUNCEMENTS", deliveries)
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("refusal", ["empty", "disabled", "capacity"])
async def test_broadcast_admission_refusal(hass, refusal):
    hass.states.async_set(
        "assist_satellite.probe", "responding", {"supported_features": ANNOUNCE_FEATURE}
    )
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(refusal != "disabled")
    if refusal == "capacity":
        for index in range(16):
            await manager.async_send(f"occupant {index}", whole_home=True, context=Context())
    before = sum(map(len, manager._queues.values()))
    entity, log, exchange = exchange_owner(
        hass, {"type": "native", "name": "send_broadcast"}
    )
    try:
        await exchange(
            {
                "message": "  " if refusal == "empty" else "Dinner is ready",
                "whole_home": True,
            }
        )
        expected_error = "Function execution failed. Check the tool configuration and permissions."
        assert expected_error in str(tool_result_data(log.content[-1]))
        assert sum(map(len, manager._queues.values())) == before
        original = next(iter(entity._execution_tool_outcomes.values()))
        assert original == "rejected"
        remember_unacknowledged_calls(entity, log, set())
        # Remove the known admission problem before attempting the corrected action.
        if refusal in {"disabled", "capacity"}:
            await manager.async_set_enabled(False)
            await manager.async_set_enabled(True)
        await exchange({"message": "Corrected announcement", "whole_home": True})
        assert [item.message for item in manager._queues["assist_satellite.probe"]] == [
            "Corrected announcement"
        ]
    finally:
        await manager.async_shutdown()
