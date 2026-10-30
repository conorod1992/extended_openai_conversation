"""Public Function mutations preserve raw owners and provider request settings."""

from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    services,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    repair_revision,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_groups_mutation import _group
from tests.test_management_loading_performance import _hass_with_agent
from tests.test_request_rules import MemoryStore


def tool(name="healthy"):
    return {
        "spec": {
            "name": name,
            "description": f"Use {name}",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
        "enabled": True,
    }


async def test_duplicate_group_service_rejects_ambiguous_identity_without_writing(
    mutation_agent, monkeypatch
):
    hass, entry, subentry = mutation_agent
    group = _group("ambiguous", ["healthy"], "always")
    subentry.data = {
        "functions": yaml.safe_dump([tool()]),
        "function_groups": [deepcopy(group), deepcopy(group)],
    }
    before = deepcopy(subentry.data)
    writes = []
    monkeypatch.setattr(
        hass.config_entries,
        "async_update_subentry",
        lambda *args, **kwargs: writes.append(kwargs),
    )
    with pytest.raises(HomeAssistantError, match="ambiguous"):
        await services.async_set_function_groups_enabled(
            hass, entry.entry_id, subentry.subentry_id, ["ambiguous"], False
        )
    assert subentry.data == before
    assert writes == []


@pytest.fixture
async def mutation_agent(hass, monkeypatch):
    local_hass, entry, subentry = _hass_with_agent()
    rules = RequestRules(MemoryStore())
    await rules.async_initialize()

    async def retained_rules(*_args):
        return rules

    monkeypatch.setattr(management_ui, "async_get_request_rules", retained_rules)
    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda *_args: (entry.entry_id, subentry.subentry_id),
    )
    subentry.data = {
        "functions": "# Authored layout stays owned by Tools\n"
        + yaml.safe_dump([tool()], sort_keys=False),
        "function_groups": [_group("healthy_group", ["healthy"], "always")],
        "extension_data": {"retained": [1, {"opaque": "future extension"}]},
    }
    return local_hass, entry, subentry


def request(agent, action, **values):
    hass, entry, subentry = agent
    return management_ui._ManagementRequest(
        hass,
        "admin",
        True,
        {"section": "tools", "action": action, **values},
        entry.entry_id,
        subentry.subentry_id,
        entry,
        subentry,
    )


def assert_unowned_settings(original, persisted):
    assert {
        key: value
        for key, value in persisted.items()
        if key not in {"functions", "function_groups"}
    } == {
        key: value
        for key, value in original.items()
        if key not in {"functions", "function_groups"}
    }, "unowned settings and omitted defaults must survive Function mutations"


async def tool_settings_witness(mutation_agent, settings, representation):
    hass, entry, subentry = mutation_agent
    if representation == "list":
        subentry.data["functions"] = yaml.safe_load(subentry.data["functions"])
    subentry.data.update(settings)
    original = deepcopy(subentry.data)
    before = build_provider_request_snapshot(subentry.data, entry.data)
    old_revision = repair_revision(subentry)
    with pytest.raises(HomeAssistantError, match="not found"):
        await services.async_set_function_tools_enabled(
            hass, entry.entry_id, subentry.subentry_id, ["healthy", "missing"], False
        )
    assert subentry.data == original and hass.config_entries.updates == 0
    await services.async_set_function_tools_enabled(
        hass, entry.entry_id, subentry.subentry_id, ["healthy", "healthy"], False
    )
    assert isinstance(subentry.data["functions"], str)
    configured = yaml.safe_load(subentry.data["functions"])
    assert configured[0]["enabled"] is False
    assert subentry.data["function_groups"] == original["function_groups"]
    assert_unowned_settings(original, subentry.data)
    edited = deepcopy(configured[0])
    edited["spec"]["description"] = "Edited through Management after service toggle"
    result = await management_ui.async_tools_command(
        request(
            mutation_agent,
            "save",
            tool=edited,
            original_name="healthy",
            revision=repair_revision(subentry),
        )
    )
    assert isinstance(subentry.data["functions"], str)
    assert yaml.safe_load(subentry.data["functions"])[0] == edited
    assert result["functions"][0]["enabled"] is False
    assert_unowned_settings(original, subentry.data)
    assert (
        build_provider_request_snapshot(subentry.data, entry.data).api_kwargs
        == before.api_kwargs
    )
    assert hass.config_entries.updates == 2
    saved = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="changed in another tab"):
        await management_ui.async_tools_command(
            request(
                mutation_agent,
                "save",
                tool=edited,
                original_name="healthy",
                revision=old_revision,
            )
        )
    assert subentry.data == saved and hass.config_entries.updates == 2


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {
            "chat_model": "gpt-5.6",
            "api_mode": "responses",
            "reasoning_effort": "low",
            "max_tokens": 750,
        },
    ],
)
@pytest.mark.parametrize("representation", ["yaml", "list"])
async def test_tool_service_then_management_preserves_settings_and_representations(
    mutation_agent, settings, representation
):
    await tool_settings_witness(mutation_agent, settings, representation)


async def test_group_service_then_management_retains_exact_tool_yaml(mutation_agent):
    hass, entry, subentry = mutation_agent
    original = deepcopy(subentry.data)
    before = build_provider_request_snapshot(subentry.data, entry.data)
    await services.async_set_function_groups_enabled(
        hass, entry.entry_id, subentry.subentry_id, ["healthy_group"], False
    )
    assert subentry.data["functions"] == original["functions"]
    # Use the ordinary public read to populate the editor's validated snapshot.
    loaded = await management_ui.async_configuration_command(
        request(mutation_agent, "get")
    )
    group = deepcopy(loaded["config"]["function_groups"][0])
    group["description"] = "Group metadata changed after service toggle"
    await management_ui.async_tools_command(
        request(
            mutation_agent,
            "save_group",
            group=group,
            original_id="healthy_group",
            revision=loaded["revision"],
        )
    )
    assert subentry.data["functions"] == original["functions"]
    assert subentry.data["function_groups"] == [group]
    assert_unowned_settings(original, subentry.data)
    assert (
        build_provider_request_snapshot(subentry.data, entry.data).api_kwargs
        == before.api_kwargs
    )
    assert hass.config_entries.updates == 2


async def quarantine_witness(agent, *, kind):
    hass, entry, subentry = agent
    broken = tool("legacy")
    broken["function"]["name"] = "obsolete_native_implementation"
    damaged_group = {
        "id": "damaged",
        "name": 7,
        "functions": ["legacy", 17],
        "extension": {"keep": True},
    }
    subentry.data["functions"] = yaml.safe_dump([tool(), broken], sort_keys=False)
    subentry.data["function_groups"] = [
        _group("healthy_group", ["healthy", "legacy"], "always"),
        damaged_group,
    ]
    original = deepcopy(subentry.data)
    before = build_provider_request_snapshot(subentry.data, entry.data)
    action = (
        services.async_set_function_tools_enabled
        if kind == "tools"
        else services.async_set_function_groups_enabled
    )
    await action(
        hass,
        entry.entry_id,
        subentry.subentry_id,
        ["healthy" if kind == "tools" else "healthy_group"],
        False,
    )
    saved_tools = yaml.safe_load(subentry.data["functions"])
    assert (
        next((item for item in saved_tools if item["spec"]["name"] == "legacy"), None)
        == broken
    ), "repair-owned Tool siblings must survive public toggles"
    assert (
        next(
            item for item in subentry.data["function_groups"] if item["id"] == "damaged"
        )
        == damaged_group
    )
    if kind == "groups":
        assert subentry.data["functions"] == original["functions"]
    edited = deepcopy(
        next(item for item in saved_tools if item["spec"]["name"] == "healthy")
    )
    edited["spec"]["description"] = "Edited valid sibling"
    result = await management_ui.async_tools_command(
        request(
            agent,
            "save",
            tool=edited,
            original_name="healthy",
            revision=repair_revision(subentry),
        )
    )
    persisted = yaml.safe_load(subentry.data["functions"])
    assert (
        next(item for item in persisted if item["spec"]["name"] == "legacy") == broken
    )
    assert (
        next(item for item in persisted if item["spec"]["name"] == "healthy") == edited
    )
    assert (
        next(
            item for item in subentry.data["function_groups"] if item["id"] == "damaged"
        )
        == damaged_group
    )
    assert next(
        item
        for item in subentry.data["function_groups"]
        if item["id"] == "healthy_group"
    )["functions"] == ["healthy", "legacy"]
    assert result["function_repair"]["invalid_count"] == 1
    assert_unowned_settings(original, subentry.data)
    assert (
        build_provider_request_snapshot(subentry.data, entry.data).api_kwargs
        == before.api_kwargs
    )
    with pytest.raises(HomeAssistantError):
        configured_function_tools_from_data(subentry.data)
    assert hass.config_entries.updates == 2


@pytest.mark.parametrize("kind", ["tools", "groups"])
async def test_quarantined_raw_siblings_survive_service_and_management(
    mutation_agent, kind
):
    await quarantine_witness(mutation_agent, kind=kind)


async def test_raw_sibling_sensitivity_detects_loss_at_persistence(
    mutation_agent, monkeypatch
):
    hass, _entry, _subentry = mutation_agent
    update = hass.config_entries.async_update_subentry

    def discard_sibling(entry, subentry, *, data, **changes):
        mutated = deepcopy(data)
        raw = mutated["functions"]
        configured = yaml.safe_load(raw) if isinstance(raw, str) else raw
        mutated["functions"] = yaml.safe_dump(
            [item for item in configured if item["spec"]["name"] != "legacy"]
        )
        update(entry, subentry, data=mutated, **changes)

    monkeypatch.setattr(hass.config_entries, "async_update_subentry", discard_sibling)
    with pytest.raises(AssertionError, match="repair-owned Tool siblings must survive"):
        await quarantine_witness(mutation_agent, kind="tools")
    assert hass.config_entries.updates == 1


async def test_owned_fields_sensitivity_detects_materialized_omitted_default(
    mutation_agent, monkeypatch
):
    hass, _entry, _subentry = mutation_agent
    update = hass.config_entries.async_update_subentry

    def adopt_unowned_default(entry, subentry, *, data, **changes):
        update(entry, subentry, data={**data, "reasoning_effort": "low"}, **changes)

    monkeypatch.setattr(
        hass.config_entries, "async_update_subentry", adopt_unowned_default
    )
    with pytest.raises(AssertionError, match="unowned settings and omitted defaults"):
        await tool_settings_witness(mutation_agent, {}, "yaml")
    assert hass.config_entries.updates == 1


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("management", [False, True])
async def test_quarantine_cannot_break_working_selected_skill(
    mutation_agent, group, management
):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        agent_config_defaults,
    )
    from custom_components.extended_openai_conversation_responses.skill_availability import (
        skill_loader_status,
    )
    from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
        function_configuration_repair_state,
    )

    hass, entry, subentry = mutation_agent
    loader = next(
        t
        for t in yaml.safe_load(agent_config_defaults()["functions"])
        if t["spec"]["name"] == "load_skill"
    )
    damaged = tool("damaged")
    damaged["function"]["name"] = "nonexistent_legacy_function"
    subentry.data.update(
        {
            "functions": yaml.safe_dump([loader, damaged]),
            "skills": ["selected"],
            "function_groups": [_group("loader", ["load_skill"], "always")],
        }
    )
    original = deepcopy(subentry.data)
    state = function_configuration_repair_state(subentry.data)
    assert skill_loader_status(["selected"], state.valid, state.groups).available
    with pytest.raises(HomeAssistantError, match="disabled"):
        if management:
            if group:
                edited = {**state.groups[0], "enabled": False}
                await management_ui.async_tools_command(
                    request(
                        mutation_agent,
                        "save_group",
                        group=edited,
                        original_id="loader",
                        revision=repair_revision(subentry),
                    )
                )
            else:
                edited = {**state.valid[0], "enabled": False}
                await management_ui.async_tools_command(
                    request(
                        mutation_agent,
                        "save",
                        tool=edited,
                        original_name="load_skill",
                        revision=repair_revision(subentry),
                    )
                )
        elif group:
            await services.async_set_function_groups_enabled(
                hass, entry.entry_id, subentry.subentry_id, ["loader"], False
            )
        else:
            await services.async_set_function_tools_enabled(
                hass, entry.entry_id, subentry.subentry_id, ["load_skill"], False
            )
    assert hass.config_entries.updates == 0 and subentry.data == original
    state = function_configuration_repair_state(subentry.data)
    assert skill_loader_status(["selected"], state.valid, state.groups).available


async def test_quarantine_preserves_existing_skill_problem_and_allows_repair(
    mutation_agent,
):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        agent_config_defaults,
    )

    hass, entry, subentry = mutation_agent
    loader = next(
        t
        for t in yaml.safe_load(agent_config_defaults()["functions"])
        if t["spec"]["name"] == "load_skill"
    )
    loader["enabled"] = False
    damaged = tool("damaged")
    damaged["function"]["name"] = "nonexistent_legacy_function"
    subentry.data.update(
        {
            "functions": yaml.safe_dump([loader, tool(), damaged]),
            "skills": ["selected"],
            "function_groups": [],
        }
    )
    await services.async_set_function_tools_enabled(
        hass, entry.entry_id, subentry.subentry_id, ["healthy"], False
    )
    await services.async_set_function_tools_enabled(
        hass, entry.entry_id, subentry.subentry_id, ["load_skill"], True
    )
    saved = yaml.safe_load(subentry.data["functions"])
    assert next(t for t in saved if t["spec"]["name"] == "healthy")["enabled"] is False
    assert (
        next(t for t in saved if t["spec"]["name"] == "load_skill")["enabled"] is True
    )
    assert saved[-1] == damaged and subentry.data["skills"] == ["selected"]
    assert hass.config_entries.updates == 2
