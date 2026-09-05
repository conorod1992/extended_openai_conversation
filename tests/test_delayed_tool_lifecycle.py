"""Regression coverage for durable delayed Function Tool lifecycle wiring."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, sentinel

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import delayed_tools
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DATA_DELAYED_TOOL_MANAGER,
    DelayedToolManager,
    _DELAYED_EXECUTION_MARKER,
    _install_execution_hook,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from homeassistant.helpers import llm


def _tool(delay_schema: dict | None = None) -> dict:
    properties: dict = {"value": {"type": "integer"}}
    required = ["value"]
    if delay_schema is not None:
        properties["delay"] = delay_schema
        required.append("delay")
    return {
        "spec": {
            "name": "control_light",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
        "function": {"type": "native", "name": "execute_service_single"},
    }


def _legacy_delay_schema() -> dict:
    return {
        "type": "object",
        "properties": {"seconds": {"type": "integer"}},
        "required": ["seconds"],
    }


def _entity(hass) -> SimpleNamespace:
    return SimpleNamespace(
        hass=hass,
        entity_id="conversation.extended_openai",
        should_run_in_background=lambda execution_delay: execution_delay is not None,
    )


def _install_test_hook(monkeypatch, original: AsyncMock) -> None:
    """Install the wrapper over an AsyncMock without tripping its dynamic attrs."""
    original._extended_openai_delayed_hook = False
    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", original)
    _install_execution_hook()


async def test_async_setup_activates_delayed_tool_manager_after_entity_hardening(
    hass, monkeypatch
) -> None:
    """Normal integration startup must activate the previously dormant scheduler."""
    order: list[str] = []
    sync_helpers = (
        "apply_openai_compatibility",
        "install_persistence_transactions",
        "install_performance_optimizations",
        "install_guest_policy_fast_path",
        "install_deferred_context_summary",
        "install_debug_instrumentation",
        "install_request_rule_match_preview",
        "install_management_loading_optimizations",
        "install_input_footprint",
        "install_management_permissions",
        "install_safety_hardening",
        "install_configurable_regex_isolation",
        "install_model_search_hardening",
        "setup_provider_credentials_websocket",
    )
    for name in sync_helpers:
        monkeypatch.setattr(integration, name, MagicMock())
    monkeypatch.setattr(
        integration,
        "install_agent_maintenance_barrier",
        MagicMock(side_effect=lambda: order.append("barrier")),
    )

    async_setup_delayed_tools = AsyncMock(
        side_effect=lambda _hass: order.append("delayed")
    )
    monkeypatch.setattr(
        integration, "async_setup_delayed_tools", async_setup_delayed_tools
    )
    for name in (
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_debug_ui",
        "async_setup_management_ui",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())

    assert await integration.async_setup(hass, {}) is True
    async_setup_delayed_tools.assert_awaited_once_with(hass)
    assert order == ["barrier", "delayed"]


@pytest.mark.parametrize(
    ("function_tool", "arguments"),
    [
        (_tool(), {"value": 1}),
        (_tool({"type": "string"}), {"value": 1, "delay": "literal-value"}),
    ],
)
async def test_delay_hook_leaves_immediate_and_literal_delay_arguments_alone(
    hass, monkeypatch, function_tool, arguments
) -> None:
    """Only the documented legacy delay object opts a call into scheduling."""
    original = AsyncMock(return_value=sentinel.immediate)
    _install_test_hook(monkeypatch, original)
    monkeypatch.setattr(
        delayed_tools,
        "async_validate_function_arguments",
        AsyncMock(side_effect=lambda _hass, _spec, values: dict(values)),
    )

    tool_input = llm.ToolInput(
        id="call-1",
        tool_name="control_light",
        tool_args=arguments,
    )
    result = await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        _entity(hass), function_tool, tool_input, None, []
    )

    assert result is sentinel.immediate
    original.assert_awaited_once()


async def test_legacy_delay_object_is_scheduled_durably(hass, monkeypatch) -> None:
    """The documented legacy delay shape is handed to the durable manager."""
    original = AsyncMock(return_value=sentinel.immediate)
    _install_test_hook(monkeypatch, original)
    monkeypatch.setattr(
        delayed_tools,
        "async_validate_function_arguments",
        AsyncMock(side_effect=lambda _hass, _spec, values: dict(values)),
    )

    manager = DelayedToolManager(hass)
    manager.async_schedule = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[DATA_DELAYED_TOOL_MANAGER] = manager
    arguments = {"value": 1, "delay": {"seconds": 5}}
    tool_input = llm.ToolInput(
        id="call-2", tool_name="control_light", tool_args=arguments
    )
    entity = _entity(hass)

    result = await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        entity, _tool(_legacy_delay_schema()), tool_input, None, []
    )

    original.assert_not_awaited()
    manager.async_schedule.assert_awaited_once_with(
        entity, "control_light", arguments, None
    )
    assert result.tool_result == {"result": "Scheduled"}


async def test_recovered_delayed_execution_strips_scheduler_metadata(
    hass, monkeypatch
) -> None:
    """A recovered durable call executes the function without its legacy delay field."""
    original = AsyncMock(return_value=sentinel.immediate)
    _install_test_hook(monkeypatch, original)
    monkeypatch.setattr(
        delayed_tools,
        "async_validate_function_arguments",
        AsyncMock(side_effect=lambda _hass, _spec, values: dict(values)),
    )
    function = SimpleNamespace(execute=AsyncMock(return_value="done"))
    monkeypatch.setattr(delayed_tools, "get_function", MagicMock(return_value=function))

    context = SimpleNamespace()
    setattr(context, _DELAYED_EXECUTION_MARKER, True)
    tool_input = llm.ToolInput(
        id="call-3",
        tool_name="control_light",
        tool_args={"value": 1, "delay": {"seconds": 5}},
    )

    await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        _entity(hass), _tool(_legacy_delay_schema()), tool_input, context, []
    )

    original.assert_not_awaited()
    function.execute.assert_awaited_once()
    assert function.execute.await_args.args[2] == {"value": 1}
