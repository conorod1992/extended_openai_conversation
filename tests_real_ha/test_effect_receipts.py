"""Per-effect settlement at Home Assistant's genuine service/schema boundary."""

from types import SimpleNamespace

import pytest
import voluptuous as vol

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    _async_call_ha_action_unchecked,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    bind_effect_replay_scope,
    remember_unacknowledged_calls,
    was_unacknowledged_equivalent,
)


async def test_service_rejection_settles_only_current_effect_and_corrected_retry(hass):
    calls = []

    async def handler(call):
        calls.append(call.data["amount"])

    hass.services.async_register(
        "receipt_probe",
        "apply",
        handler,
        schema=vol.Schema({vol.Required("amount"): int}),
    )
    entity = SimpleNamespace()
    tool = {
        "spec": {"name": "effect"},
        "function": {"type": "native", "name": "execute_service_single"},
    }
    first = llm.ToolInput(tool_name="effect", tool_args={}, id="first")
    with execution_attempt(entity, first, tool):
        with pytest.raises(HomeAssistantError):
            await _async_call_ha_action_unchecked(
                hass,
                "receipt_probe",
                "apply",
                data={"amount": "invalid"},
                blocking=True,
            )
    rejected = entity._execution_effect_receipts[id(first)]
    assert len(rejected) == 1
    assert rejected[0].outcome == "rejected"
    assert calls == []
    log = SimpleNamespace(
        conversation_id="conversation",
        content=[
            conversation.AssistantContent(
                agent_id="conversation.probe", tool_calls=[first]
            )
        ],
    )
    remember_unacknowledged_calls(entity, log, set())
    corrected = llm.ToolInput(tool_name="effect", tool_args={}, id="corrected")
    assert not was_unacknowledged_equivalent(entity, log, corrected)
    with (
        bind_effect_replay_scope(entity, "conversation"),
        execution_attempt(entity, corrected, tool),
    ):
        await _async_call_ha_action_unchecked(
            hass, "receipt_probe", "apply", data={"amount": 1}, blocking=True
        )
    assert calls == [1]
    assert entity._execution_tool_outcomes[id(corrected)] == "completed"


async def test_completed_handler_then_schema_rejection_remains_partial(hass):
    calls = []

    async def handler(call):
        calls.append(call.data["amount"])

    hass.services.async_register(
        "receipt_probe",
        "apply",
        handler,
        schema=vol.Schema({vol.Required("amount"): int}),
    )
    entity = SimpleNamespace()
    tool = {
        "spec": {"name": "effect"},
        "function": {"type": "native", "name": "execute_service"},
    }
    call = llm.ToolInput(tool_name="effect", tool_args={}, id="batch")
    with execution_attempt(entity, call, tool):
        await _async_call_ha_action_unchecked(
            hass, "receipt_probe", "apply", data={"amount": 1}, blocking=True
        )
        with pytest.raises(HomeAssistantError):
            await _async_call_ha_action_unchecked(
                hass,
                "receipt_probe",
                "apply",
                data={"amount": "invalid"},
                blocking=True,
            )
    effects = entity._execution_effect_receipts[id(call)]
    assert [r.outcome for r in effects] == ["completed", "rejected"]
    assert calls == [1]
    assert entity._execution_tool_outcomes[id(call)] == "partial"
    log = SimpleNamespace(
        conversation_id="conversation",
        content=[
            conversation.AssistantContent(
                agent_id="conversation.probe", tool_calls=[call]
            )
        ],
    )
    remember_unacknowledged_calls(entity, log, set())
    assert was_unacknowledged_equivalent(
        entity,
        log,
        llm.ToolInput(tool_name="effect", tool_args={"changed": True}, id="retry"),
    )
    retry = llm.ToolInput(
        tool_name="another_wrapper", tool_args={}, id="another-wrapper"
    )
    with (
        bind_effect_replay_scope(entity, log.conversation_id),
        execution_attempt(entity, retry, tool),
    ):
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await _async_call_ha_action_unchecked(
                hass, "receipt_probe", "apply", data={"amount": 1}, blocking=True
            )
    assert calls == [1]
