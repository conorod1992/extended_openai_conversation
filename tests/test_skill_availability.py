"""Tests for Skill loader availability and configuration protection."""

from copy import deepcopy
import re
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import skill_availability
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
    FunctionToolAdmission,
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

    available = skill_loader_status(["calendar"], [_loader()], [], max_function_calls=1)
    assert available.available is True
    assert ExtendedOpenAIAgentEntity._skill_loader_reachable(
        None, [_loader()], FunctionToolAdmission()
    )
    assert not ExtendedOpenAIAgentEntity._skill_loader_reachable(
        None, [_loader()], FunctionToolAdmission(function_calls_available=False)
    )

    loader = _loader()
    parameters = loader["spec"]["parameters"]
    parameters["properties"]["file"]["type"] = "integer"
    bad_type = skill_loader_status(["calendar"], [loader], [], max_function_calls=1)
    assert bad_type.available is False
    assert "schema" in str(bad_type.reason)

    parameters["properties"]["file"]["type"] = ["null", "string"]
    assert skill_loader_status(["calendar"], [loader], [], max_function_calls=1).available

    del parameters["properties"]["file"]
    parameters["additionalProperties"] = False
    assert not skill_loader_status(["calendar"], [loader], [], max_function_calls=1).available
    parameters["patternProperties"] = {"^other$": {"type": "string"}}
    assert not skill_loader_status(["calendar"], [loader], [], max_function_calls=1).available
    del parameters["patternProperties"]
    parameters["additionalProperties"] = {"type": "integer"}
    assert not skill_loader_status(["calendar"], [loader], [], max_function_calls=1).available
    for pattern, usable in (("^other$", False), ("^oth.*$", False), ("^file$", True), ("^fi.*$", True)):
        parameters["patternProperties"] = {pattern: {"type": "string"}}
        assert skill_loader_status(["calendar"], [loader], []).available is usable
    parameters["patternProperties"] = {"^other$": {"type": "string"}}
    parameters["additionalProperties"] = {"type": "string"}
    assert skill_loader_status(["calendar"], [loader], [], max_function_calls=1).available
    parameters["properties"]["name"]["maxLength"] = 0
    assert not skill_loader_status(["calendar"], [loader], [], max_function_calls=1).available

    for schema, available in [
        ({"maxLength": 0}, False),
        ({"minLength": 9}, False),
        ({"const": "README.md"}, False),
        ({"enum": ["notes.md", "SKILL.md"], "maxLength": 8}, True),
    ]:
        loader = _loader()
        loader["spec"]["parameters"]["properties"]["file"].update(schema)
        status = skill_loader_status(["calendar"], [loader], [], max_function_calls=1)
        assert status.available is available, schema


@pytest.mark.parametrize(
    ("pattern", "matches"),
    [("(?i)^f\u0131le$", True), ("(?i)^f[^\u0131]le$", False)],
    ids=["casefolded-literal", "casefolded-negation"],
)
@pytest.mark.parametrize(
    "additional", [False, {"type": "integer"}], ids=["closed", "integer-extra"]
)
def test_skill_loader_pattern_membership_matches_runtime(pattern, matches, additional):
    from custom_components.extended_openai_conversation_responses.function_execution import (
        patterned_object_keys,
    )

    loader = _loader()
    loader["spec"]["strict"] = False
    parameters = loader["spec"]["parameters"]
    del parameters["properties"]["file"]
    parameters["patternProperties"] = {pattern: {"type": "string"}}
    parameters["additionalProperties"] = additional
    assert ("file" in patterned_object_keys({"file": "SKILL.md"}, parameters)) is matches
    assert skill_loader_status(["calendar"], [loader], []).available is matches

    for patterns, available in (
        ([r"^(?:fi)+le$"], True),
        ([r"^f.*l.*e$"], True),
        (["(?i)^f(?:i|\u0131)le$"], True),
        ([r"^other(?:key)+$"], False),
        ([r"^file$", r"^(?:x*)+$"], True),
    ):
        parameters["patternProperties"] = dict.fromkeys(patterns, {"type": "string"})
        assert ("file" in patterned_object_keys({"file": "SKILL.md"}, parameters)) is available
        assert skill_loader_status(["calendar"], [loader], []).available is available, patterns

    parameters["patternProperties"][r"("] = {"type": "string"}
    assert not skill_loader_status(["calendar"], [loader], []).available


def test_skill_loader_leaves_complex_pattern_membership_uncertified():
    loader = _loader()
    parameters = loader["spec"]["parameters"]
    del parameters["properties"]["file"]
    parameters["patternProperties"] = {r"^(?:x*)+$": {"type": "string"}}
    parameters["additionalProperties"] = False

    assert not skill_loader_status(["calendar"], [loader], []).available


@pytest.mark.parametrize(
    ("pattern", "available"),
    [(r"(?i)^fıle$", True), (r"(?i)^f[^ı]le$", False)],
)
def test_skill_name_pattern_uses_function_runtime_semantics(pattern, available):
    loader = _loader()
    loader["spec"]["parameters"]["properties"]["name"]["pattern"] = pattern
    assert skill_loader_status(["file"], [loader], []).available is available


@pytest.mark.parametrize("assertion", ["const", "enum"])
def test_loader_object_assertion_limits_available_skills(assertion):
    loader = _loader()
    loader["spec"]["strict"] = False
    permitted = {"name": "weather", "file": "SKILL.md"}
    loader["spec"]["parameters"][assertion] = (
        permitted if assertion == "const" else [permitted]
    )
    status = effective_skill_loader_status(
        ["calendar", "weather"], ["calendar", "weather"], [loader], []
    )
    assert status.loadable_skills == ("weather",)


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

    no_budget = skill_loader_status(["calendar"], [_loader()], [], max_function_calls=0)
    assert no_budget.available is False
    assert "per request" in str(no_budget.reason)


@pytest.mark.parametrize(
    "parameters",
    [
        pytest.param(False, id="malformed-parameters"),
        pytest.param({"properties": []}, id="malformed-properties"),
        pytest.param(
            {"properties": {"name": False, "file": {"type": "string"}}},
            id="disabled-name",
        ),
    ],
)
def test_unusable_loader_schema_keeps_selected_skill_unavailable(parameters):
    loader = _loader()
    loader["spec"]["parameters"] = parameters
    status = skill_loader_status(["calendar"], [loader], [], max_function_calls=3)
    assert not status.available
    assert "schema" in str(status.reason)


def test_no_selected_skills_do_not_require_loader_for_persisted_config() -> None:
    status = skill_loader_status([], [], [], max_function_calls=0)
    assert status.available is True


def test_selected_installed_skills_are_a_stable_deduplicated_primitive() -> None:
    assert selected_installed_skill_names([], ["one"]) == ()
    assert selected_installed_skill_names(["missing"], ["one"]) == ()
    assert selected_installed_skill_names(
        ["two", "one", "two", "missing"], ["one", "two", "other"]
    ) == ("two", "one")


def test_effective_loader_requires_at_least_one_selected_installed_skill(
    monkeypatch,
) -> None:
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
    for restriction in (
        {"enum": ["weather"]}, {"const": "weather"}, {"maxLength": 7},
        {"pattern": "^weather$"},
    ):
        loader = _loader()
        loader["spec"]["parameters"]["properties"]["name"].update(restriction)
        assert not skill_loader_status(["calendar"], [loader], []).available
        assert not skill_loader_status(["calendar", "weather"], [loader], []).available
        assert skill_loader_status(["weather"], [loader], []).available
        status = effective_skill_loader_status(
            ["calendar", "weather"], ["calendar", "weather"], [loader], []
        )
        assert status.available
        assert status.loadable_skills == ("weather",)
        assert not effective_skill_loader_status(
            ["calendar"], ["calendar", "weather"], [loader], []
        ).available
    loader = _loader()
    loader["spec"]["parameters"]["maxProperties"] = 1
    assert not skill_loader_status(["calendar"], [loader], []).available
    loader["spec"]["parameters"]["maxProperties"] = 2
    assert skill_loader_status(["calendar"], [loader], []).available
    loader["spec"]["parameters"]["properties"]["file"]["pattern"] = "^README"
    assert not skill_loader_status(["calendar"], [loader], []).available
    loader = _loader()
    parameters = loader["spec"]["parameters"]
    pattern = (
        "^(?:calendar|" + "|".join(f"installed-skill-{i}" for i in range(65)) + ")$"
    )
    assert len(pattern) > 1024
    parameters["properties"]["name"]["pattern"] = pattern
    assert skill_loader_status(["calendar"], [loader], []).available
    assert not skill_loader_status(["weather"], [loader], []).available
    compile_pattern = re.compile
    now = 0.0

    def compile_after_delay(*args, **kwargs):
        nonlocal now
        now += 0.02
        return compile_pattern(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(skill_availability, "monotonic", lambda: now)
        patch.setattr(re, "compile", compile_after_delay)
        status = effective_skill_loader_status(
            ["weather", "calendar"], ["weather", "calendar"], [loader], []
        )
        assert status.available
        assert status.loadable_skills == ("calendar",)
        assert now == 0.02
    parameters.update({"additionalProperties": False, "minProperties": 3})
    assert skill_loader_status(["calendar"], [loader], []).available
    parameters["properties"].pop("offset")
    assert not skill_loader_status(["calendar"], [loader], []).available
    for pattern, available in (
        (r"^(?:name|file)(?![\s\S])", False),
        (r"^(?:name|file)\Z", False),
        (r"^(?:name|file)$", True),
        (r"^(?:name|file|offset)(?![\s\S])", True),
        (r"^extra-", True),
    ):
        parameters["patternProperties"] = {pattern: {"type": "string"}}
        assert skill_loader_status(["calendar"], [loader], []).available is available
    parameters.pop("patternProperties")
    parameters["additionalProperties"] = True
    assert skill_loader_status(["calendar"], [loader], []).available


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
        initial = assemble_function_tools([loader], [group], session.loaded_group_ids)
        assert [tool["spec"]["name"] for tool in initial.tools] == [
            "load_function_groups"
        ]
        assert load_function_groups(session, ["skills"], [group], [loader])[
            "loaded"
        ] == ["skills"]
        loaded = assemble_function_tools([loader], [group], session.loaded_group_ids)

    assert [tool["spec"]["name"] for tool in loaded.tools] == ["load_skill"]


def test_live_entity_assembly_uses_effective_skill_availability(
    hass, monkeypatch
) -> None:
    """The installed runtime wrapper must affect the real agent assembly path."""
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

    missing_names = {tool["spec"]["name"] for tool in entity._get_function_tools()}
    assert "other_tool" in missing_names
    assert "load_skill" not in missing_names

    manager._skills["calendar"] = SimpleNamespace(name="calendar")
    installed_names = {tool["spec"]["name"] for tool in entity._get_function_tools()}
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


def test_closed_loader_rejects_a_required_key_it_cannot_accept():
    tool = _loader()
    parameters = tool["spec"]["parameters"]
    parameters["additionalProperties"] = False
    parameters["required"] = ["name", "file", "required_key"]
    assert not skill_loader_status(["calendar"], [tool], []).available
    parameters["patternProperties"] = {"^required_key$": {}}
    assert skill_loader_status(["calendar"], [tool], []).available


def test_legacy_unlimited_budget_preserves_skill_access():
    assert skill_loader_status(
        ["calendar"], [_loader()], [], max_function_calls=-1
    ).available
    assert not skill_loader_status(
        ["calendar"], [_loader()], [], max_function_calls=0
    ).available
