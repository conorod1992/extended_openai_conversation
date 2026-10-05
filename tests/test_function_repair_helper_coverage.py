"""Coverage for tolerant Function Tool repair helpers."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)


def _default_tools():
    return yaml.safe_load(agent_config_defaults()[CONF_FUNCTION_TOOLS])


def test_editable_function_tools_handles_missing_structured_and_yaml_values() -> None:
    assert repair.editable_function_tools({}) == []

    structured = [{"spec": {"name": "copy"}}]
    result = repair.editable_function_tools({CONF_FUNCTION_TOOLS: structured})
    assert result == structured
    assert result is not structured

    assert repair.editable_function_tools({CONF_FUNCTION_TOOLS: "null"}) == []
    malformed = "[unterminated"
    assert repair.editable_function_tools({CONF_FUNCTION_TOOLS: malformed}) == malformed


def test_function_tools_cache_key_round_trips_missing_string_and_json() -> None:
    assert repair._function_tools_cache_key({}) == ("none", "")
    assert repair._options_from_function_tools_cache_key("none", "") == {}

    raw = "[]"
    assert repair._function_tools_cache_key({CONF_FUNCTION_TOOLS: raw}) == (
        "string",
        raw,
    )
    assert repair._options_from_function_tools_cache_key("string", raw) == {
        CONF_FUNCTION_TOOLS: raw
    }

    structured = [{"spec": {"name": "example"}}]
    kind, payload = repair._function_tools_cache_key({CONF_FUNCTION_TOOLS: structured})
    assert kind == "json"
    assert repair._options_from_function_tools_cache_key(kind, payload) == {
        CONF_FUNCTION_TOOLS: structured
    }


def test_function_tools_cache_key_falls_back_to_yaml_for_non_json_value() -> None:
    raw = [{"spec": {"name": {"not", "json"}}}]
    kind, payload = repair._function_tools_cache_key({CONF_FUNCTION_TOOLS: raw})

    assert kind == "yaml"
    restored = repair._options_from_function_tools_cache_key(kind, payload)
    assert set(restored[CONF_FUNCTION_TOOLS][0]["spec"]["name"]) == {
        "not",
        "json",
    }


def test_isolate_function_tools_rejects_non_array() -> None:
    valid, invalid, issue = repair._isolate_function_tools_uncached(
        {CONF_FUNCTION_TOOLS: "not: an array"}
    )

    assert valid == []
    assert invalid == []
    assert issue == "Saved Function Tools must be a YAML/JSON array"


def test_isolate_function_tools_records_invalid_candidate_without_losing_valid_sibling() -> (
    None
):
    tools = _default_tools()
    good = deepcopy(tools[0])
    bad = deepcopy(tools[0])
    bad["spec"]["name"] = "broken"
    bad["spec"]["parameters"]["description"] = 123

    valid, invalid, issue = repair._isolate_function_tools_uncached(
        {CONF_FUNCTION_TOOLS: [good, bad]}
    )

    assert [tool["spec"]["name"] for tool in valid] == [good["spec"]["name"]]
    assert invalid[0]["index"] == 1
    assert invalid[0]["name"] == "broken"
    assert "description" in invalid[0]["validation_error"]
    assert issue == invalid[0]["validation_error"]


def test_effective_function_groups_filters_unavailable_members_but_preserves_raw() -> (
    None
):
    valid = _default_tools()[:1]
    valid_name = valid[0]["spec"]["name"]
    raw_groups = [
        {
            "id": "g",
            "name": "Group",
            "functions": [valid_name, "missing_tool"],
        },
        "malformed",
        {"id": "bad-functions", "functions": "not-a-list"},
    ]
    options = {CONF_FUNCTION_GROUPS: raw_groups}

    effective, issues, raw = repair._effective_function_groups(options, valid)

    assert raw == raw_groups
    assert raw is not raw_groups
    assert effective[0]["functions"] == [valid_name]
    assert issues == [
        {
            "id": "g",
            "name": "Group",
            "unavailable_functions": ["missing_tool"],
        }
    ]
    assert effective[1] == "malformed"
    assert effective[2]["functions"] == "not-a-list"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("[]", False),
        ({"not": "a list"}, False),
        (
            [
                {
                    "function": {
                        "type": "native",
                        "name": "definitely_missing_native_implementation",
                    }
                }
            ],
            True,
        ),
        (
            [
                {
                    "function": {
                        "type": "template",
                        "name": "definitely_missing_native_implementation",
                    }
                }
            ],
            False,
        ),
    ],
)
def test_unavailable_native_tool_preflight(value, expected) -> None:
    assert repair.has_unavailable_native_tool({CONF_FUNCTION_TOOLS: value}) is expected


def test_unavailable_native_tool_fast_string_negative_skips_yaml_parse(
    monkeypatch,
) -> None:
    def unexpected_parse(_options):
        pytest.fail("plain-string negatives should skip YAML parsing")

    monkeypatch.setattr(repair, "editable_function_tools", unexpected_parse)

    assert not repair.has_unavailable_native_tool(
        {CONF_FUNCTION_TOOLS: "- function:\n    type: template\n"}
    )


def test_isolated_function_tools_returns_defensive_copies() -> None:
    tools = _default_tools()[:1]
    options = {CONF_FUNCTION_TOOLS: tools}

    first_valid, first_invalid, first_issue = repair.isolated_function_tools(options)
    first_valid[0]["spec"]["name"] = "mutated"
    first_invalid.append({"fake": True})

    second_valid, second_invalid, second_issue = repair.isolated_function_tools(options)

    assert second_valid[0]["spec"]["name"] != "mutated"
    assert second_invalid == []
    assert first_issue == second_issue
