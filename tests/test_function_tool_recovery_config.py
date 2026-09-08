"""Configuration contract for Function Tool error recovery."""

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOL_ERROR_RECOVERY,
)


def test_function_tool_error_recovery_defaults_off() -> None:
    defaults = agent_config_defaults()

    assert defaults[CONF_FUNCTION_TOOL_ERROR_RECOVERY] is False
    assert normalize_agent_config({})[CONF_FUNCTION_TOOL_ERROR_RECOVERY] is False


def test_function_tool_error_recovery_accepts_only_boolean() -> None:
    assert (
        normalize_agent_config({CONF_FUNCTION_TOOL_ERROR_RECOVERY: True})[
            CONF_FUNCTION_TOOL_ERROR_RECOVERY
        ]
        is True
    )

    with pytest.raises(AgentConfigError, match="must be a bool"):
        normalize_agent_config({CONF_FUNCTION_TOOL_ERROR_RECOVERY: "true"})
