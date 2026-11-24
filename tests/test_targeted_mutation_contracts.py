"""Behavioral contracts discovered by targeted mutation testing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses.exceptions import (
    FunctionNotFound,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
    bind_tool_recovery_state,
    correctable_validation_failure,
    recovery_tool_result,
    should_report_uncertain_effects,
    strict_execution_failures_enabled,
)
from custom_components.extended_openai_conversation_responses.function_tool_resolution import (
    configured_function_tool_for_execution,
    latest_function_tool_for_execution,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.function_tool_policy import (
    is_integration_owned_read_only_tool,
)
from homeassistant.helpers import llm


def _call(name: str, call_id: str) -> llm.ToolInput:
    return llm.ToolInput(
        id=call_id,
        tool_name=name,
        tool_args={},
        external=True,
    )


def _tool(
    name: str,
    function: dict[str, Any],
    *,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "spec": {
            "name": name,
            "parameters": parameters
            or {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "function": function,
    }


def _resolution_agent(
    current_tools: list[dict[str, Any]],
    *,
    groups: list[dict[str, Any]] | None = None,
) -> tuple[Any, Mock, Mock]:
    latest_data = {"function_groups": groups or []}
    latest_subentry = SimpleNamespace(data=latest_data)
    latest_entry = SimpleNamespace(subentries={"agent-1": latest_subentry})
    entry_lookup = Mock(return_value=latest_entry)
    resolver = Mock(return_value=current_tools)
    agent = SimpleNamespace(
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=entry_lookup)
        ),
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-1", data={"revision": 1}),
        _configured_function_tools_from_data=resolver,
    )
    return agent, entry_lookup, resolver


def test_strict_execution_failures_follow_bound_recovery_policy() -> None:
    """Strict runtime failures are enabled only for an enabled bound recovery state."""
    assert strict_execution_failures_enabled() is False

    with bind_tool_recovery_state(ToolRecoveryState(enabled=False)):
        assert strict_execution_failures_enabled() is False

    with bind_tool_recovery_state(ToolRecoveryState(enabled=True)):
        assert strict_execution_failures_enabled() is True

    assert strict_execution_failures_enabled() is False


def test_uncertain_effect_reporting_is_owned_by_the_bound_request() -> None:
    assert should_report_uncertain_effects() is False
    with bind_tool_recovery_state(
        ToolRecoveryState(enabled=False, report_uncertain_effects=True)
    ):
        assert should_report_uncertain_effects() is True
        with bind_tool_recovery_state(ToolRecoveryState(enabled=True)):
            assert should_report_uncertain_effects() is False
        assert should_report_uncertain_effects() is True
    assert should_report_uncertain_effects() is False


def test_recovery_feedback_has_bounded_text_and_stable_protocol_fields() -> None:
    """Recovery feedback preserves the model-visible boundary and result structure."""
    exactly_at_limit = "x" * 320
    exact_failure = correctable_validation_failure(ValueError(exactly_at_limit))
    assert exact_failure.message == exactly_at_limit

    over_limit = correctable_validation_failure(ValueError("x" * 321))
    assert len(over_limit.message) == 320
    assert over_limit.message.endswith("…")

    blank = correctable_validation_failure(ValueError(" \n\t "))
    assert blank.message
    assert "schema" in blank.message.lower()

    call = _call("action", "call-1")
    result = recovery_tool_result("conversation.mutation", call, over_limit)
    assert result.agent_id == "conversation.mutation"
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "action"
    assert tool_result_data(result) == {
        "result": {
            "status": "error",
            "reason": "correctable_tool_error",
            "code": "invalid_arguments",
            "stage": "pre_dispatch_validation",
            "error": over_limit.message,
        }
    }


def test_read_only_classification_rejects_malformed_function_metadata() -> None:
    """Missing or malformed function metadata must never be classified as integration-owned reads."""
    assert is_integration_owned_read_only_tool({"spec": {"name": "missing"}}) is False
    assert (
        is_integration_owned_read_only_tool(
            {"spec": {"name": "bad"}, "function": "knowledge"}
        )
        is False
    )


def test_configured_resolution_uses_live_entry_and_allows_enabled_group() -> None:
    """Live config lookup and an explicitly enabled Function Group permit dispatch."""
    current = _tool("notify", {"type": "service", "service": "notify.current"})
    groups = [
        {
            "id": "notifications",
            "name": "Notifications",
            "description": "Enabled notification tools",
            "loading_mode": "always",
            "functions": ["notify"],
            "enabled": True,
        }
    ]
    agent, entry_lookup, _resolver = _resolution_agent([current], groups=groups)

    assert configured_function_tool_for_execution(agent, "notify") is current
    entry_lookup.assert_called_once_with("entry-1")


def test_missing_resolver_fails_closed_with_requested_name() -> None:
    """A missing configured-tool resolver yields the public not-found contract."""
    agent = SimpleNamespace()

    with pytest.raises(FunctionNotFound) as caught:
        configured_function_tool_for_execution(agent, "notify")

    assert caught.value.function == "notify"


def test_missing_current_tool_preserves_requested_name() -> None:
    """A live deletion reports the Function Tool name that disappeared."""
    agent, _entry_lookup, _resolver = _resolution_agent([])

    with pytest.raises(FunctionNotFound) as caught:
        configured_function_tool_for_execution(agent, "notify")

    assert caught.value.function == "notify"


def test_truthy_non_string_tool_name_keeps_request_round_definition() -> None:
    """Malformed non-string names do not enter configured-tool resolution."""
    agent, _entry_lookup, resolver = _resolution_agent([])
    request_tool = _tool("placeholder", {"type": "service", "service": "test.call"})
    request_tool["spec"]["name"] = 123

    assert latest_function_tool_for_execution(agent, request_tool) is request_tool
    resolver.assert_not_called()


def test_ha_llm_reference_match_preserves_request_round_schema() -> None:
    """A stable saved HA reference keeps the schema emitted in this provider round."""
    reference = {
        "type": "ha_llm",
        "source_type": "platform",
        "source_id": "test",
        "api_id": "assist",
        "tool_name": "do_thing",
    }
    request_tool = _tool(
        "ha_saved",
        dict(reference),
        parameters={
            "type": "object",
            "properties": {"old_schema": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    current_tool = _tool(
        "ha_saved",
        dict(reference),
        parameters={
            "type": "object",
            "properties": {"new_schema": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    agent, entry_lookup, _resolver = _resolution_agent([current_tool])

    assert latest_function_tool_for_execution(agent, request_tool) is request_tool
    entry_lookup.assert_called_once_with("entry-1")


def test_ha_llm_reference_change_fails_closed_with_tool_name() -> None:
    """Changing the saved HA reference after provider emission invalidates the call."""
    original_reference = {
        "type": "ha_llm",
        "source_type": "platform",
        "source_id": "test",
        "api_id": "assist",
        "tool_name": "do_thing",
    }
    changed_reference = dict(original_reference, tool_name="other_thing")
    request_tool = _tool("ha_saved", original_reference)
    current_tool = _tool("ha_saved", changed_reference)
    agent, _entry_lookup, _resolver = _resolution_agent([current_tool])

    with pytest.raises(FunctionNotFound) as caught:
        latest_function_tool_for_execution(agent, request_tool)

    assert caught.value.function == "ha_saved"
