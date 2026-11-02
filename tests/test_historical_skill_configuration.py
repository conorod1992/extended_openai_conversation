"""Released configurations stay recoverable without re-enabling capabilities."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    async_migrate_integration,
    backup,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_snapshot,
    configured_function_tools_from_data,
    merge_agent_config,
    normalize_agent_config,
    project_agent_config_updates,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONFIG_ENTRY_VERSION,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from tests.restore_participants import participants
from tests.test_backup import _document
from tests.test_request_rules import MemoryStore

HISTORICAL = json.loads(
    (Path(__file__).parent / "fixtures/persistence/agent_config_6_8_3.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture(params=HISTORICAL["cases"], ids=lambda case: case["id"])
def historical_config(request):
    return deepcopy({**HISTORICAL["base"], **request.param["overrides"]})


@pytest.fixture
async def empty_rules(monkeypatch):
    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    return rules


@pytest.mark.parametrize("budget", [0, 1, "0", "1", "1.0"])
async def test_released_config_migrates_reads_saves_and_can_be_explicitly_repaired(
    hass, management_agent, management_message, historical_config, empty_rules, budget
):
    historical_config["max_function_calls_per_conversation"] = budget
    entry, subentry = management_agent
    subentry.data = historical_config
    entry.version = CONFIG_ENTRY_VERSION
    entry.disabled_by = None
    hass.config_entries.async_entries.return_value = [entry]
    await async_migrate_integration(hass)
    baseline = deepcopy(subentry.data)
    read = await management_ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    # Positive-budget controls make the former zero-budget case usable; the
    # missing/disabled-loader cases must remain inactive in every representation.
    usable = (
        any(
            t["spec"]["name"] == "load_skill" and t.get("enabled", True)
            for t in yaml.safe_load(historical_config["functions"])
        )
        and float(budget) > 0
    )
    assert read["configuration_guidance"]["skills"]["available"] is usable
    assert "load_skill" in read["configuration_guidance"]["skills"]["repair"]
    saved = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            config={"max_tokens": 612},
            revision=read["revision"],
        ),
    )
    assert saved["valid"], saved
    assert subentry.data["max_tokens"] == 612
    assert subentry.data == {**baseline, "max_tokens": 612}
    # Simulate a cold projection after reload, reading the actual persisted data.
    from custom_components.extended_openai_conversation_responses.management_function_repair import (
        discard_persisted_config_projection,
    )

    discard_persisted_config_projection(subentry)
    again = await management_ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    assert again["config"]["skills"] == historical_config["skills"]
    # Repair is an explicit administrative edit, never a migration side effect.
    loader = deepcopy(
        next(
            tool
            for tool in DEFAULT_CONF_FUNCTION_TOOLS
            if tool["spec"]["name"] == "load_skill"
        )
    )
    loader["enabled"] = True
    repaired = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            config={
                "functions": [loader],
                "max_function_calls_per_conversation": 1,
            },
            revision=again["revision"],
        ),
    )
    assert repaired["valid"]
    assert repaired["configuration_guidance"]["skills"]["available"] is True
    assert configured_function_tools_from_data(subentry.data)[0]["enabled"] is True


async def test_released_config_can_inspect_its_own_private_backup(
    hass, management_agent, historical_config, monkeypatch
):
    entry, subentry = management_agent
    subentry.data = historical_config
    document = _document()
    sources = participants(
        **{
            name: SimpleNamespace(
                async_backup_data=AsyncMock(
                    return_value=document.get(section, {"schedule": None})
                )
            )
            for name, section in (
                ("memory", "memories"),
                ("temporary", "temporary_memories"),
                ("knowledge", "knowledge"),
                ("archive", "archive"),
                ("usage", "usage"),
                ("guest_mode", "guest_mode"),
                ("request_rules", "request_rules"),
            )
        }
    )
    monkeypatch.setattr(backup, "_managers", AsyncMock(return_value=sources))
    exported = await backup.async_create_backup(hass, entry, subentry)
    prepared = backup.inspect_backup(exported["json"], subentry.subentry_id)
    assert prepared.config["skills"] == historical_config["skills"]
    assert prepared.config["functions"] == historical_config["functions"]
    assert (
        prepared.config["max_function_calls_per_conversation"]
        == historical_config["max_function_calls_per_conversation"]
    )


def test_recovery_does_not_admit_new_or_worsened_skill_incompatibilities(
    historical_config,
):
    with pytest.raises(AgentConfigError, match="skills"):
        normalize_agent_config(historical_config)
    with pytest.raises(AgentConfigError, match="skills"):
        merge_agent_config(historical_config, {"skills": ["heating", "new_skill"]})
    # Clearing selected Skills is a supported repair, preserving disabled tools.
    normalized = merge_agent_config(historical_config, {"skills": []})
    persisted = project_agent_config_updates(
        historical_config, {"skills": []}, normalized
    )
    assert persisted["functions"] == historical_config["functions"]
    assert persisted["skills"] == []


def test_unavailable_skill_recovery_does_not_hide_other_invalid_fields(
    historical_config,
):
    with pytest.raises(AgentConfigError, match="max_tokens"):
        merge_agent_config(historical_config, {"max_tokens": -1})


@pytest.mark.parametrize("budget", [0, "0", "0.0"])
def test_recovery_rejects_hidden_new_blockers_and_allows_partial_repairs(budget):
    case = next(case for case in HISTORICAL["cases"] if case["id"] == "zero_budget")
    current = deepcopy({**HISTORICAL["base"], **case["overrides"]})
    current["max_function_calls_per_conversation"] = budget
    # The budget diagnostic must not hide a newly removed/disabled loader.
    with pytest.raises(AgentConfigError, match="skills"):
        merge_agent_config(current, {"functions": "[]"})
    tool = agent_config_snapshot(current)["functions"][0]
    tool["enabled"] = False
    with pytest.raises(AgentConfigError, match="skills"):
        merge_agent_config(current, {"functions": [tool]})
    # A historical configuration can have two blockers; fixing one is progress.
    current["functions"] = "[]"
    partial = merge_agent_config(current, {"max_function_calls_per_conversation": 1})
    assert partial["skills"] == ["heating"]
    assert yaml.safe_load(partial["functions"]) == []


@pytest.mark.parametrize("budget", [0, 1, "0", "1", "1.0"])
async def test_skill_recovery_coexists_with_function_quarantine(
    hass, management_agent, management_message, historical_config, empty_rules, budget
):
    historical_config["max_function_calls_per_conversation"] = budget
    _entry, subentry = management_agent
    tools = yaml.safe_load(historical_config["functions"])
    tools.append(
        {
            "spec": {
                "name": "stable",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "template", "value_template": "ok"},
        }
    )
    tools.append(
        {
            "spec": {
                "name": "unavailable",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "removed_implementation"},
        }
    )
    subentry.data = {**historical_config, "functions": yaml.safe_dump(tools)}
    before = deepcopy(subentry.data)
    read = await management_ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    assert read["function_repair"]["invalid_count"] == 1
    saved = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            config={"max_tokens": 613},
            revision=read["revision"],
        ),
    )
    assert saved["valid"], saved
    assert subentry.data == {**before, "max_tokens": 613}
    await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("tools", "set_enabled", name="stable", enabled=False),
    )
    assert subentry.data["max_function_calls_per_conversation"] == budget
    assert yaml.safe_load(subentry.data["functions"])[-1] == tools[-1]
