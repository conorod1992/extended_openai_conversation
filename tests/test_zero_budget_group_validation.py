"""Saved capability validation follows effective zero-budget tool exposure."""

import pytest
from homeassistant.exceptions import HomeAssistantError
from custom_components.extended_openai_conversation_responses.request import (
    conversation_tools_required,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_routed_request_options,
)


@pytest.mark.parametrize(
    "enabled,budget,conditional,required",
    [
        (True, 0, False, False),
        (False, 0, False, False),
        (True, 1, False, True),
        (True, 0, True, True),
    ],
)
def test_zero_budget_groups_follow_runtime_policy(
    enabled, budget, conditional, required
):
    options = {
        "chat_model": "gpt-6-astra",
        "reasoning_effort": "medium",
        "api_mode": "chat_completions",
        "max_function_calls_per_conversation": budget,
        "continue_conversation": "conditional" if conditional else "ha_default",
        "function_groups": [
            {"enabled": enabled, "loading_mode": "on_demand", "functions": ["probe"]}
        ],
    }
    assert conversation_tools_required(options) is required
    if required:
        with pytest.raises(HomeAssistantError, match="function/tool calling"):
            validate_routed_request_options(options, {})
    else:
        validate_routed_request_options(options, {})
