"""Tests for Skill loader availability and configuration protection."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    merge_agent_config,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.request_static_cache import (
    tools_for_available_skills,
)
from custom_components.extended_openai_conversation_responses.skill_availability import (
    skill_loader_status,
)


def _loader(*, enabled: bool = True) -> dict:
    tool = deepcopy(
        next(
            tool
            for tool in DEFAULT_CONF_FUNCTION_TOOLS
            if tool.get("spec", {}).get("name") == "load_skill"
        )
    )
    if not enabled:
        tool["enabled"] = False
    return tool


def _group(*, enabled: bool = True, loading_mode: str = "on_demand") -> dict:
    return {
        "id": "skills",
        "name": "Skills",
        "description": "Load optional Skill instructions",
        "loading_mode": loading_mode,
        "functions": ["load_skill"],
        "enabled": enabled,
    }


def test_selected_skills_require_canonical_enabled_loader() -> None:
    missing = skill_loader_status(["calendar"], [], [], max_function_calls=1)
    assert missing.available is False
    assert "load_skill" in str(missing.reason)

    disabled = skill_loader_status(
        ["calendar"], [_loader(enabled=False)], [], max_function_calls=1
    )
    assert disabled.available is False
    assert "disabled" in str(disabled.reason)

    available = skill_loader_status(
        ["calendar"], [_loader()], [], max_function_calls=1
    )
    assert available.available is True


def test_selected_skills_require_reachable_enabled_group_and_tool_budget() -> None:
    disabled_group = skill_loader_status(
        ["calendar"], [_loader()], [_group(enabled=False)], max_function_calls=1
    )
    assert disabled_group.available is False
    assert disabled_group.group_id == "skills"

    on_demand = skill_loader_status(
        ["calendar"], [_loader()], [_group()], max_function_calls=1
    )
    assert on_demand.available is True
    assert on_demand.on_demand is True

    no_budget = skill_loader_status(
        ["calendar"], [_loader()], [], max_function_calls=0
    )
    assert no_budget.available is False
    assert "per request" in str(no_budget.reason)


def test_no_selected_skills_do_not_require_loader() -> None:
    status = skill_loader_status([], [], [], max_function_calls=0)
    assert status.available is True


def test_zero_usable_skills_hide_only_the_canonical_loader() -> None:
    canonical = _loader()
    other = deepcopy(canonical)
    other["spec"]["name"] = "read_other_file"
    projected = tools_for_available_skills([canonical, other], False)
    assert [tool["spec"]["name"] for tool in projected] == ["read_other_file"]
    assert tools_for_available_skills([canonical, other], True) == [
        canonical,
        other,
    ]


def test_config_mutations_cannot_break_selected_skill_loader() -> None:
    current = normalize_agent_config(
        {
            "skills": ["calendar"],
            "functions": [_loader()],
            "max_function_calls_per_conversation": 1,
        }
    )
    disabled = _loader(enabled=False)
    with pytest.raises(AgentConfigError, match=r"skills:.*load_skill.*disabled"):
        merge_agent_config(current, {"functions": [disabled]})
    with pytest.raises(AgentConfigError, match=r"skills:.*per request"):
        merge_agent_config(current, {"max_function_calls_per_conversation": 0})


def test_config_allows_on_demand_skill_loader_but_rejects_disabled_group() -> None:
    normalized = normalize_agent_config(
        {
            "skills": ["calendar"],
            "functions": [_loader()],
            "function_groups": [_group()],
            "max_function_calls_per_conversation": 1,
        }
    )
    assert normalized["function_groups"][0]["enabled"] is True

    with pytest.raises(AgentConfigError, match=r"skills:.*Function Group.*disabled"):
        normalize_agent_config(
            {
                "skills": ["calendar"],
                "functions": [_loader()],
                "function_groups": [_group(enabled=False)],
                "max_function_calls_per_conversation": 1,
            }
        )
