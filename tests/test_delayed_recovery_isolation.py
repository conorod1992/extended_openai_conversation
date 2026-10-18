import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import delayed_tools
from custom_components.extended_openai_conversation_responses.built_in_functions import (
    BUILT_IN_FUNCTION_PRESETS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
    bind_tool_recovery_state,
)
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("recovery_enabled", [True, False])
async def test_delayed_batch_has_same_semantics_with_or_without_request_context(
    hass, monkeypatch, recovery_enabled
):
    tool = deepcopy(
        next(
            preset["tool"]
            for preset in BUILT_IN_FUNCTION_PRESETS
            if preset["implementation"] == "execute_service"
        )
    )
    tool["spec"]["name"] = "batch"
    tool["enabled"] = True
    tool["spec"]["parameters"]["properties"]["delay"] = {
        "type": "object",
        "properties": {"seconds": {"type": "integer"}},
    }
    config = {
        "functions": [tool],
        "function_groups": [],
        "function_tool_error_recovery": recovery_enabled,
    }
    entry = SimpleNamespace(
        entry_id="entry",
        disabled_by=None,
        data={},
        subentries={
            "agent": SimpleNamespace(
                subentry_id="agent",
                subentry_type="conversation",
                title="Agent",
                data=config,
            )
        },
    )
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    monkeypatch.setattr(
        delayed_tools, "configured_function_tools_from_data", lambda _: [tool]
    )
    monkeypatch.setattr(delayed_tools, "get_exposed_entities", lambda _: [])
    monkeypatch.setattr(
        native.NativeFunction,
        "validate_service_targets",
        lambda *_args, **_kwargs: None,
    )
    executed = []

    async def call(_hass, _domain, service, **kwargs):
        entity_id = kwargs["data"]["entity_id"][0]
        if entity_id == "light.unresponsive":
            raise HomeAssistantError("First device failed")
        executed.append(entity_id)
        return {}

    monkeypatch.setattr(native, "async_call_ha_action", call)
    agent = ExtendedOpenAIAgentEntity(entry, entry.subentries["agent"])
    agent.hass = hass
    agent.entity_id = "conversation.agent"
    monkeypatch.setattr(agent, "_configured_function_tools_from_data", lambda _: [tool])
    monkeypatch.setattr(
        agent, "_resolve_live_guest_policy", GuestCapabilityPolicy.unrestricted
    )

    def manager():
        instance = delayed_tools.DelayedToolManager(hass)
        instance._setup_complete = True
        instance._started = True
        instance._store = SimpleNamespace(async_save=AsyncMock())
        monkeypatch.setattr(instance, "_resolve_agent", lambda *_: agent)
        return instance

    arguments = {
        "delay": {"seconds": 0},
        "list": [
            {
                "domain": "light",
                "service": "turn_on",
                "service_data": {"entity_id": ["light.unresponsive"]},
            },
            {
                "domain": "light",
                "service": "turn_on",
                "service_data": {"entity_id": ["light.working"]},
            },
        ],
    }
    in_process = manager()
    with bind_tool_recovery_state(ToolRecoveryState(enabled=recovery_enabled)):
        record = await in_process.async_schedule(
            agent,
            "batch",
            arguments,
            None,
            function_tool=tool,
            execution_delay={"seconds": 0},
        )
    await asyncio.gather(*tuple(in_process._tasks.values()))
    observed_in_process = list(executed)
    executed.clear()
    recovered = manager()
    recovered._records[record.call_id] = delayed_tools.DelayedToolCall.from_dict(
        record.as_dict()
    )
    with bind_tool_recovery_state(None):
        recovered._arm(record.call_id)
    await asyncio.gather(*tuple(recovered._tasks.values()))
    observed_recovered = list(executed)
    assert observed_in_process == observed_recovered
    assert observed_in_process == ["light.working"]
    assert not in_process._records and not recovered._records
