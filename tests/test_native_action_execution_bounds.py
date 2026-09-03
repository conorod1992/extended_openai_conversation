"""Regression coverage for bounded, completion-aware native HA actions."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.model_payload import (
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.resource_limits import (
    MAX_NATIVE_SERVICE_ACTIONS,
)
from custom_components.extended_openai_conversation_responses.safety_hardening import (
    _install_native_tool_guards,
)
from homeassistant.exceptions import HomeAssistantError


def _service_action() -> dict[str, object]:
    return {
        "domain": "light",
        "service": "turn_on",
        "service_data": {"entity_id": ["light.living_room"]},
    }


async def test_native_service_waits_for_home_assistant_completion(
    hass, exposed_entities, llm_context
) -> None:
    """Native execute_service must not report success before HA finishes the call."""
    _install_native_tool_guards()
    function = NativeFunction()

    result = await function.execute(
        hass,
        {"name": "execute_service"},
        {"list": [_service_action()]},
        llm_context,
        exposed_entities,
    )

    assert result == [{"success": True}]
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.await_args.kwargs["blocking"] is True


async def test_native_service_batch_limit_rejects_before_execution(
    hass, exposed_entities, llm_context
) -> None:
    """One model tool call cannot hide an unbounded number of HA actions."""
    _install_native_tool_guards()
    function = NativeFunction()
    actions = [_service_action() for _ in range(MAX_NATIVE_SERVICE_ACTIONS + 1)]

    with pytest.raises(
        HomeAssistantError,
        match=rf"at most {MAX_NATIVE_SERVICE_ACTIONS} actions",
    ):
        await function.execute(
            hass,
            {"name": "execute_service"},
            {"list": actions},
            llm_context,
            exposed_entities,
        )

    hass.services.async_call.assert_not_called()


def test_provider_schema_adds_native_batch_limit_without_mutating_saved_tool() -> None:
    """Legacy saved tools learn the runtime cap only in the provider-facing copy."""
    tool = {
        "spec": {
            "name": "execute_service",
            "description": "Execute Home Assistant services.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list": {
                        "type": "array",
                        "items": {"type": "object"},
                    }
                },
                "required": ["list"],
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    }
    original = deepcopy(tool)

    prepared = prepare_model_function_tools([tool])[0]

    assert tool == original
    assert "maxItems" not in tool["spec"]["parameters"]["properties"]["list"]
    assert (
        prepared["spec"]["parameters"]["properties"]["list"]["maxItems"]
        == MAX_NATIVE_SERVICE_ACTIONS
    )


def test_provider_schema_preserves_stricter_native_batch_limit() -> None:
    """Provider preparation must never widen a user's stricter saved constraint."""
    tool = {
        "spec": {
            "name": "execute_service",
            "parameters": {
                "type": "object",
                "properties": {
                    "list": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {"type": "object"},
                    }
                },
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    }

    prepared = prepare_model_function_tools([tool])[0]

    assert prepared["spec"]["parameters"]["properties"]["list"]["maxItems"] == 5
