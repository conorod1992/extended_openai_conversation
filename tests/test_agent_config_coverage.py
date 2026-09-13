"""Focused residual coverage for the authoritative agent configuration contract."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses import agent_config
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    normalize_agent_config,
    validate_function_groups,
    validate_function_tools,
    validate_speech_regex_replacements,
)


def _native_tool(name: str = "demo") -> dict:
    return {
        "spec": {
            "name": name,
            "description": "Demo tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def _group(
    group_id: str = "lights",
    *,
    name: str = "Lights",
    description: str = "Lighting tools",
    functions: list[str] | None = None,
) -> dict:
    return {
        "id": group_id,
        "name": name,
        "description": description,
        "loading_mode": agent_config.FUNCTION_GROUP_LOADING_MODES[0],
        "functions": ["demo"] if functions is None else functions,
    }


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda tool: tool.update(enabled="yes"), r"enabled.*boolean"),
        (lambda tool: tool.update(guest_allowed="yes"), r"guest_allowed.*boolean"),
        (lambda tool: tool.update(spec=[]), r"spec.*object"),
        (lambda tool: tool["spec"].update(unexpected=True), r"spec.*unknown fields"),
        (lambda tool: tool["spec"].update(name=""), r"spec.name.*required"),
        (lambda tool: tool["spec"].update(name="bad name"), r"spec.name.*only"),
        (lambda tool: tool["spec"].update(description=123), r"description.*string"),
        (lambda tool: tool["spec"].update(strict="yes"), r"strict.*boolean"),
        (lambda tool: tool["spec"].update(parameters=[]), r"parameters.*object"),
        (lambda tool: tool.update(function=[]), r"function.*object"),
        (
            lambda tool: tool["function"].update(type="missing"),
            r"function.type.*unrecognized",
        ),
        (
            lambda tool: tool["function"].update(name="not_a_native_implementation"),
            r"function.name.*unknown native implementation",
        ),
    ],
)
def test_function_tool_validation_rejects_malformed_metadata(mutator, match) -> None:
    tool = _native_tool()
    mutator(tool)
    with pytest.raises(AgentConfigError, match=match):
        validate_function_tools([tool])


def test_function_tool_validation_wraps_schema_and_function_errors(monkeypatch) -> None:
    tool = _native_tool()

    def reject_schema(_schema):
        raise agent_config.HomeAssistantError("Function input schema is invalid: broken")

    monkeypatch.setattr(agent_config, "validate_function_schema", reject_schema)
    with pytest.raises(AgentConfigError, match=r"parameters.*broken"):
        validate_function_tools([tool])

    monkeypatch.setattr(agent_config, "validate_function_schema", lambda _schema: None)

    class BrokenFunction:
        @staticmethod
        def validate_schema(_config):
            raise ValueError("bad implementation config")

    monkeypatch.setattr(agent_config, "get_function", lambda _name: BrokenFunction())
    with pytest.raises(AgentConfigError, match=r"configuration is invalid.*bad implementation"):
        validate_function_tools([tool])


def test_function_tools_accept_none_and_reject_non_lists() -> None:
    assert validate_function_tools(None) == []
    with pytest.raises(AgentConfigError, match="top-level value must be a list"):
        validate_function_tools({"spec": {}})
    with pytest.raises(AgentConfigError, match=r"function_tools\[0\].*object"):
        validate_function_tools(["not-a-tool"])


def test_function_groups_normalize_and_preserve_guest_allowed() -> None:
    group = _group()
    group.update(name="  Lighting  ", description="  Lighting helpers  ")
    group["guest_allowed"] = True
    group["enabled"] = False

    assert validate_function_groups([group], [_native_tool()]) == [
        {
            "id": "lights",
            "name": "Lighting",
            "description": "Lighting helpers",
            "loading_mode": agent_config.FUNCTION_GROUP_LOADING_MODES[0],
            "functions": ["demo"],
            "enabled": False,
            "guest_allowed": True,
        }
    ]


@pytest.mark.parametrize(
    ("groups", "match"),
    [
        ({}, "must be a list"),
        (["bad"], r"group.*object"),
        ([{**_group(), "unknown": True}], "unknown fields"),
        ([{**_group(), "id": "Bad ID"}], r"id.*lowercase"),
        ([_group("same"), _group("same", name="Other")], "duplicate group ID"),
        ([_group("one", name="Same"), _group("two", name=" same ")], "duplicate group name"),
        ([{**_group(), "name": " "}], r"name.*required"),
        ([{**_group(), "name": "x" * 101}], r"name.*100"),
        ([{**_group(), "description": " "}], r"description.*required"),
        ([{**_group(), "description": "x" * 501}], r"description.*500"),
        ([{**_group(), "loading_mode": "not-valid"}], r"loading_mode.*unsupported"),
        ([{**_group(), "enabled": "yes"}], r"enabled.*boolean"),
        ([{**_group(), "guest_allowed": "yes"}], r"guest_allowed.*boolean"),
        ([{**_group(), "functions": "demo"}], r"functions.*list of names"),
        ([{**_group(), "functions": ["demo", "demo"]}], "duplicate names"),
        ([{**_group(), "functions": ["missing"]}], "unknown function"),
    ],
)
def test_function_group_validation_rejects_bad_shapes(groups, match) -> None:
    with pytest.raises(AgentConfigError, match=match):
        validate_function_groups(groups, [_native_tool()])


def test_function_groups_reject_duplicate_assignment_and_excess_groups() -> None:
    with pytest.raises(AgentConfigError, match="already assigned"):
        validate_function_groups(
            [_group("one"), _group("two", name="Two")], [_native_tool()]
        )

    groups = [
        _group(f"g{index}", name=f"Group {index}", functions=[])
        for index in range(51)
    ]
    with pytest.raises(AgentConfigError, match="at most 50"):
        validate_function_groups(groups, [_native_tool()])


def test_speech_regex_validation_residual_matrix() -> None:
    cases = [
        ({}, "must be a list"),
        (["bad"], r"\[0\].*object"),
        ([{"pattern": "x", "replacement": "y", "extra": True}], "unknown fields"),
        ([{"pattern": "", "replacement": ""}], r"pattern.*required"),
        ([{"pattern": "x", "replacement": 1}], r"replacement.*string"),
        (
            [{"pattern": "x" * (agent_config.MAX_SPEECH_REGEX_PATTERN_LENGTH + 1), "replacement": ""}],
            r"pattern.*too long",
        ),
        (
            [{"pattern": "x", "replacement": "y" * (agent_config.MAX_SPEECH_REGEX_REPLACEMENT_LENGTH + 1)}],
            r"replacement.*too long",
        ),
    ]
    for value, match in cases:
        with pytest.raises(AgentConfigError, match=match):
            validate_speech_regex_replacements(value)

    with pytest.raises(AgentConfigError, match="at most"):
        validate_speech_regex_replacements(
            [{"pattern": "x", "replacement": "y"}]
            * (agent_config.MAX_SPEECH_REGEX_RULES + 1)
        )


def test_legacy_number_coercion_handles_signed_decimal_and_invalid_float_strings() -> None:
    config = {
        agent_config.CONF_MAX_TOKENS: "+42.0",
        agent_config.CONF_CONTEXT_THRESHOLD: 8.0,
        agent_config.CONF_TOP_P: "not-a-number",
    }
    agent_config._coerce_legacy_numbers(config)
    assert config[agent_config.CONF_MAX_TOKENS] == 42
    assert config[agent_config.CONF_CONTEXT_THRESHOLD] == 8
    assert config[agent_config.CONF_TOP_P] == "not-a-number"


@pytest.mark.parametrize(
    ("config", "match"),
    [
        ({agent_config.CONF_MAX_TOKENS: True}, r"max_tokens.*int"),
        ({agent_config.CONF_WEB_SEARCH: 1}, r"web_search.*bool"),
        ({agent_config.CONF_PROMPT: 1}, r"prompt.*str"),
        ({agent_config.CONF_TOP_P: "bad"}, r"top_p.*int or float"),
        ({agent_config.CONF_API_MODE: "invalid"}, r"api_mode.*unsupported"),
        ({agent_config.CONF_ARCHIVE_RETENTION_DAYS: 123}, r"unsupported archive retention"),
        ({agent_config.CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION: -1}, r"at least 0"),
        ({agent_config.CONF_CONTEXT_THRESHOLD: 0}, r"context_threshold.*at least 1"),
        ({agent_config.CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: 0}, r"must be 1 to 1440"),
        (
            {agent_config.CONF_MEMORY_AUTO_RETRIEVE_LIMIT: agent_config.MAX_MEMORY_AUTO_RETRIEVE_LIMIT + 1},
            r"memory_auto_retrieve_limit.*must be 0 to",
        ),
        ({agent_config.CONF_TOP_P: 1.1}, r"top_p.*0 to 1"),
        ({agent_config.CONF_TEMPERATURE: 2.1}, r"temperature.*0 to 2"),
        ({agent_config.CONF_VOICE_DEVICE_MAPPINGS: []}, r"voice_device_mappings.*must map"),
        ({agent_config.CONF_VOICE_DEVICE_MAPPINGS: {1: "user:x"}}, r"voice_device_mappings.*must map"),
        ({agent_config.CONF_SKILLS: "skill"}, r"skills.*list of names"),
        ({agent_config.CONF_SKILLS: [1]}, r"skills.*list of names"),
        ({agent_config.CONF_GUEST_ALLOWED_GROUP_IDS: [""]}, r"list of non-empty strings"),
    ],
)
def test_normalize_agent_config_rejects_residual_invalid_values(config, match) -> None:
    with pytest.raises(AgentConfigError, match=match):
        normalize_agent_config(config)


def test_normalize_agent_config_template_and_list_edges() -> None:
    for key in (
        agent_config.CONF_CURRENT_DATETIME_TEMPLATE,
        agent_config.CONF_EXPOSED_ENTITIES_TEMPLATE,
    ):
        with pytest.raises(AgentConfigError, match="invalid template"):
            normalize_agent_config({key: "{{ broken"})

    result = normalize_agent_config(
        {
            agent_config.CONF_CURRENT_DATETIME_TEMPLATE: "   ",
            agent_config.CONF_EXPOSED_ENTITIES_TEMPLATE: "",
            agent_config.CONF_GUEST_ALLOWED_GROUP_IDS: [" one ", "one", "two"],
        }
    )
    assert result[agent_config.CONF_GUEST_ALLOWED_GROUP_IDS] == ["one", "two"]


def test_reasoning_default_and_explicit_validation(monkeypatch) -> None:
    monkeypatch.setattr(agent_config, "get_reasoning_effort_options", lambda _model: [])
    monkeypatch.setattr(
        agent_config,
        "get_model_config",
        lambda _model: {"recommended_profile": {}},
    )
    result = normalize_agent_config({})
    assert agent_config.CONF_REASONING_EFFORT not in result

    monkeypatch.setattr(
        agent_config, "get_reasoning_effort_options", lambda _model: ["low", "high"]
    )
    with pytest.raises(AgentConfigError, match=r"reasoning_effort.*unsupported"):
        normalize_agent_config({agent_config.CONF_REASONING_EFFORT: "medium"})


def test_skills_fail_when_loader_status_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_config,
        "skill_loader_status",
        lambda *_args, **_kwargs: type(
            "Status", (), {"available": False, "reason": "loader unavailable"}
        )(),
    )
    with pytest.raises(AgentConfigError, match=r"skills.*loader unavailable"):
        normalize_agent_config({agent_config.CONF_SKILLS: ["demo"]})


def test_function_tools_supplied_in_config_are_serialized_canonically() -> None:
    result = normalize_agent_config({agent_config.CONF_FUNCTION_TOOLS: [_native_tool()]})
    assert isinstance(result[agent_config.CONF_FUNCTION_TOOLS], str)
    assert "name: demo" in result[agent_config.CONF_FUNCTION_TOOLS]
