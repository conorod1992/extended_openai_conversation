"""Tests for Skill loader availability and configuration protection."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    merge_agent_config,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    assemble_function_tools,
    load_function_groups,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.request_static_cache import (
    tools_for_available_skills,
)
from custom_components.extended_openai_conversation_responses.skill_availability import (
    effective_skill_loader_status,
    selected_installed_skill_names,
    skill_loader_status,
)
from custom_components.extended_openai_conversation_responses.skill_runtime_availability import (
    effective_tool_runtime_scope,
    install_skill_runtime_availability,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager


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


def _other_tool() -> dict:
    return {
        "spec": {
            "name": "other_tool",
            "description": "Another configured tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def _installed_manager(*names: str):
    return SimpleNamespace(
        get_all_skills=lambda: [SimpleNamespace(name=name) for name in names]
    )


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


def test_no_selected_skills_do_not_require_loader_for_persisted_config() -> None:
    status = skill_loader_status([], [], [], max_function_calls=0)
    assert status.available is True


def test_selected_installed_skills_are_a_stable_deduplicated_primitive() -> None:
    assert selected_installed_skill_names([], ["one"]) == ()
    assert selected_installed_skill_names(["missing"], ["one"]) == ()
    assert selected_installed_skill_names(
        ["two", "one", "two", "missing"], ["one", "two", "other"]
    ) == ("two", "one")


def test_effective_loader_requires_at_least_one_selected_installed_skill() -> None:
    zero = effective_skill_loader_status(
        ["calendar"], ["weather"], [_loader()], [], max_function_calls=1
    )
    assert zero.available is False

    one = effective_skill_loader_status(
        ["calendar"], ["calendar"], [_loader()], [], max_function_calls=1
    )
    assert one.available is True
    assert one.loadable_skills == ("calendar",)

    multiple = effective_skill_loader_status(
        ["calendar", "weather"],
        ["calendar", "weather", "unused"],
        [_loader()],
        [],
        max_function_calls=1,
    )
    assert multiple.available is True
    assert multiple.loadable_skills == ("calendar", "weather")


def test_effective_loader_accounts_for_runtime_and_group_loader_support() -> None:
    unsupported = effective_skill_loader_status(
        ["calendar"],
        ["calendar"],
        [_loader()],
        [],
        max_function_calls=1,
        function_tools_supported=False,
    )
    assert unsupported.available is False
    assert "runtime" in str(unsupported.reason)

    unavailable_group_loader = effective_skill_loader_status(
        ["calendar"],
        ["calendar"],
        [_loader()],
        [_group()],
        max_function_calls=1,
        group_loader_supported=False,
    )
    assert unavailable_group_loader.available is False
    assert unavailable_group_loader.group_id == "skills"

    always_group = effective_skill_loader_status(
        ["calendar"],
        ["calendar"],
        [_loader()],
        [_group(loading_mode="always")],
        max_function_calls=1,
        group_loader_supported=False,
    )
    assert always_group.available is True


def test_effective_runtime_scope_hides_only_unusable_skill_loader() -> None:
    loader = _loader()
    other = _other_tool()
    options = {
        "skills": ["calendar"],
        "functions": [loader, other],
        "function_groups": [],
        "max_function_calls_per_conversation": 1,
    }

    with effective_tool_runtime_scope(
        options, [loader, other], _installed_manager("weather")
    ):
        unavailable = assemble_function_tools([loader, other], [], set())
    assert [tool["spec"]["name"] for tool in unavailable.tools] == ["other_tool"]

    with effective_tool_runtime_scope(
        options, [loader, other], _installed_manager("calendar")
    ):
        available = assemble_function_tools([loader, other], [], set())
    assert [tool["spec"]["name"] for tool in available.tools] == [
        "load_skill",
        "other_tool",
    ]


def test_effective_runtime_scope_preserves_on_demand_skill_loader_semantics() -> None:
    loader = _loader()
    group = _group()
    options = {
        "skills": ["calendar"],
        "functions": [loader],
        "function_groups": [group],
        "max_function_calls_per_conversation": 1,
    }
    session = FunctionGroupRuntime().begin("conversation:skill", 30)

    with effective_tool_runtime_scope(
        options, [loader], _installed_manager("calendar")
    ):
        initial = assemble_function_tools(
            [loader], [group], session.loaded_group_ids
        )
        assert [tool["spec"]["name"] for tool in initial.tools] == [
            "load_function_groups"
        ]
        assert load_function_groups(
            session, ["skills"], [group], [loader]
        )["loaded"] == ["skills"]
        loaded = assemble_function_tools([loader], [group], session.loaded_group_ids)

    assert [tool["spec"]["name"] for tool in loaded.tools] == ["load_skill"]


def test_live_entity_assembly_uses_effective_skill_availability(
    hass, monkeypatch
) -> None:
    """The installed runtime wrapper must affect the real agent assembly path."""
    install_skill_runtime_availability()
    loader = _loader()
    other = _other_tool()
    options = {
        "skills": ["calendar"],
        "function_groups": [],
        "max_function_calls_per_conversation": 1,
    }
    manager = SkillManager(hass)
    manager._initialized = True

    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(data=options)
    entity.skill_manager = manager
    entity._function_groups_runtime = None
    entity._temporary_memory = None
    entity._knowledge = None
    entity._archive = None

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_get_configured_function_tools",
        lambda _self: [loader, other],
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        lambda _self: GuestCapabilityPolicy.unrestricted(),
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_current_memory_scope_id", lambda _self: None
    )

    missing_names = {
        tool["spec"]["name"] for tool in entity._get_function_tools()
    }
    assert "other_tool" in missing_names
    assert "load_skill" not in missing_names

    manager._skills["calendar"] = SimpleNamespace(name="calendar")
    installed_names = {
        tool["spec"]["name"] for tool in entity._get_function_tools()
    }
    assert "other_tool" in installed_names
    assert "load_skill" in installed_names


def test_zero_usable_skills_hide_only_the_canonical_loader() -> None:
    canonical = _loader()
    other = deepcopy(canonical)
    other["spec"]["name"] = "read_other_file"
    projected = tools_for_available_skills([canonical, other], False)
    assert [tool["spec"]["name"] for tool in projected] == ["read_other_file"]
    assert tools_for_available_skills([canonical, other], True) == [canonical, other]


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
