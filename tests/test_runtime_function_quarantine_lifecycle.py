"""Request-lifecycle regression coverage for runtime Function Tool quarantine."""

from unittest.mock import Mock

import yaml


def _phone_tool(*, min_length):
    return {
        "spec": {
            "name": "invalid_phone_tool",
            "description": "Phone selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "enum": ["home", "mobile"],
                        "minLength": min_length,
                    }
                },
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def test_runtime_quarantine_does_not_leak_into_next_request(monkeypatch) -> None:
    """A later clean request must not inherit an earlier quarantined tool name."""
    from custom_components.extended_openai_conversation_responses import agent_config

    broken = _phone_tool(min_length="legacy")
    logger = Mock()
    monkeypatch.setattr(quarantine, "_LOGGER", logger)
    first_tools = quarantine._runtime_configured_function_tools(
        {"functions": yaml.safe_dump([broken], sort_keys=False)}
    )

    assert first_tools == []
    assert quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.get() == frozenset(
        {"invalid_phone_tool"}
    )
    assert quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.get() is False
    logger.warning.assert_called_once()
    log_args = logger.warning.call_args.args
    assert "Extended OpenAI > Functions" in log_args[0]
    assert log_args[1] == "invalid_phone_tool"

    repaired = _phone_tool(min_length=1)
    second_tools = quarantine._runtime_configured_function_tools(
        {"functions": yaml.safe_dump([repaired], sort_keys=False)}
    )

    assert [tool["spec"]["name"] for tool in second_tools] == ["invalid_phone_tool"]
    assert quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.get() == frozenset()
    assert quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.get() is False

    captured = {}

    def validate(groups, function_tools):
        captured["groups"] = groups
        captured["function_tools"] = function_tools
        return groups

    monkeypatch.setattr(agent_config, "validate_function_groups", validate)
    groups = [
        {
            "id": "phone_tools",
            "name": "Phone tools",
            "description": "Phone-related tools.",
            "loading_mode": "always",
            "functions": ["invalid_phone_tool"],
        }
    ]

    result = quarantine._runtime_validate_function_groups(groups, second_tools)

    assert result[0]["functions"] == ["invalid_phone_tool"]
    assert captured["groups"][0]["functions"] == ["invalid_phone_tool"]
    assert captured["function_tools"] is second_tools


from custom_components.extended_openai_conversation_responses import (
    function_tool_quarantine as quarantine,
)
