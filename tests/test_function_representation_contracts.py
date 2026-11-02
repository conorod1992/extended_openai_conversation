"""Dependency edits use the same tool collection that requests will execute."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_ui as ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.const import (
    AGENT_CONFIG_EXPORT_VERSION,
)
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_configuration_repair_state,
)
from homeassistant.exceptions import HomeAssistantError
from tests.function_configuration_corpus import (
    FUNCTION_CONFIGURATION_CASES,
    corpus_config,
)
from tests.test_function_dependency_integrity import _call, _nested_rule
from tests.test_function_repair_configuration_contract import _repairable_agent
from tests.test_selective_restore_concurrency import RestoreHarness


def attribute_rule():
    rule = _nested_rule()
    rule["action"]["actions"] = [
        _call("get_attributes", {"entity_id": ["light.kitchen"]})
    ]
    return rule


def retained_rules():
    document = {"rules": [attribute_rule()]}
    return SimpleNamespace(
        revision=lambda: "unchanged-rules",
        snapshot=lambda: deepcopy(document),
        async_backup_data=AsyncMock(return_value=document),
    )


@pytest.mark.parametrize(
    "case", FUNCTION_CONFIGURATION_CASES, ids=lambda case: case["id"]
)
async def test_management_dependency_validation_matches_persisted_collection(
    hass, case
):
    config = corpus_config(case)
    before = deepcopy(config)
    state = function_configuration_repair_state(config)
    tools = state.valid if state else configured_function_tools_from_data(config)
    available = any(tool["spec"]["name"] == "get_attributes" for tool in tools)
    assert available == (case["effective"] == "defaults")
    operation = ui._async_validate_configuration_dependencies(
        hass,
        SimpleNamespace(data={}),
        SimpleNamespace(),
        config,
        rules_manager=retained_rules(),
    )
    if available:
        await operation
    else:
        with pytest.raises(HomeAssistantError, match="get_attributes"):
            await operation
    assert config == before  # Validation cannot change source or enable a definition.


@pytest.mark.parametrize(
    "action", ["validate", "save", "update", "import_preview", "import"]
)
@pytest.mark.parametrize("empty", [[], "[]", "# Authored empty collection\n[]\n"])
async def test_configuration_edits_cannot_remove_active_rule_dependencies(
    hass,
    management_agent,
    management_message,
    monkeypatch,
    action,
    empty,
):
    _entry, subentry = management_agent
    before = deepcopy(subentry.data)
    rules = retained_rules()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    updates = {"functions": empty, "function_groups": [], "skills": []}
    fields = {
        "config": updates,
        "revision": ui._agent_config_revision(subentry.data, subentry.title),
    }
    if action.startswith("import"):
        fields = {
            "document": json.dumps(
                {
                    "schema": transfer.LEGACY_AGENT_SCHEMA,
                    "version": AGENT_CONFIG_EXPORT_VERSION,
                    "title": "Replacement",
                    "config": updates,
                }
            ),
            "mode": "current",
            "confirm": True,
            "revision": fields["revision"],
        }
    if action in {"validate", "save"}:
        if action == "save":
            with pytest.raises(HomeAssistantError, match="get_attributes"):
                await ui.async_management_command(
                    hass,
                    "admin",
                    True,
                    management_message("configuration", action, **fields),
                )
        else:
            result = await ui.async_management_command(
                hass,
                "admin",
                True,
                management_message("configuration", action, **fields),
            )
            assert result["valid"] is False
            assert set(result["errors"]) == {"functions"}
            assert "get_attributes" in str(result["errors"])
    else:
        with pytest.raises(HomeAssistantError, match="get_attributes"):
            await ui.async_management_command(
                hass,
                "admin",
                True,
                management_message("configuration", action, **fields),
            )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_bulk_repair_cannot_substitute_default_tools_for_empty_edit(
    hass, monkeypatch
):
    entry, subentry = _repairable_agent()
    before = deepcopy(subentry.data)
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(
        ui, "async_get_request_rules", AsyncMock(return_value=retained_rules())
    )
    with pytest.raises(HomeAssistantError, match="get_attributes"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            {
                "section": "function_repair",
                "action": "save",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "revision": ui._agent_config_revision(subentry.data, subentry.title),
                "tools": [],
            },
        )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_decoded_tool_edit_cannot_substitute_default_dependencies(
    hass, management_agent, monkeypatch
):
    entry, subentry = management_agent
    before = deepcopy(subentry.data)
    monkeypatch.setattr(
        ui, "async_get_request_rules", AsyncMock(return_value=retained_rules())
    )
    with pytest.raises(HomeAssistantError, match="get_attributes"):
        await ui._persist_function_configuration(
            hass,
            entry,
            subentry,
            [],
            [],
            extra_updates={"skills": []},
            expected_revision=ui._agent_config_revision(subentry.data, subentry.title),
        )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_field_only_preview_keeps_explicit_empty_tool_projection(
    hass,
    management_agent,
    management_message,
    monkeypatch,
):
    _entry, subentry = management_agent
    subentry.data = {
        **subentry.data,
        "functions": "[]",
        "function_groups": [],
        "skills": [],
    }
    rules = retained_rules()
    rules.snapshot = lambda: {"rules": []}
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("configuration", "validate", config={"max_tokens": 700}),
    )
    assert result["valid"] is True
    assert result["config"]["functions"] == []
    assert subentry.data["functions"] == "[]"
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_retained_disabled_tool_is_checked_without_being_enabled(hass):
    config = {
        "functions": yaml.safe_dump(
            [
                {
                    "spec": {
                        "name": "get_attributes",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "entity_id": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                }
                            },
                        },
                    },
                    "function": {
                        "type": "template",
                        "value_template": "{{ entity_id }}",
                    },
                    "enabled": False,
                }
            ]
        ),
        "function_groups": [],
        "skills": [],
    }
    before = deepcopy(config)
    await ui._async_validate_configuration_dependencies(
        hass,
        SimpleNamespace(data={}),
        SimpleNamespace(),
        config,
        rules_manager=retained_rules(),
    )
    assert config == before
    assert configured_function_tools_from_data(config)[0]["enabled"] is False


@pytest.mark.parametrize("empty", ["[]", "# Explicitly empty\n[]\n", "null\n"])
async def test_selective_restore_rejects_empty_configuration_before_any_write(
    hass,
    monkeypatch,
    empty,
):
    from custom_components.extended_openai_conversation_responses import backup
    from custom_components.extended_openai_conversation_responses.request_rules import (
        RequestRules,
    )
    from tests.test_private_backup_fidelity import private_document

    tx = RestoreHarness(monkeypatch)
    tx.state.request_rules = RequestRules.validate_backup_data(
        {
            "defaults": {},
            "rules": [attribute_rule()],
        }
    )
    before = deepcopy(tx.subentry.data)
    document = private_document()
    document["agent"]["config"].update(functions=empty, function_groups=[], skills=[])
    with pytest.raises(HomeAssistantError, match="get_attributes"):
        await transfer.async_restore_transfer(
            tx.hass,
            tx.entry,
            tx.subentry,
            backup.finalize_backup_snapshot(document)["json"],
            sections=[transfer.SECTION_CONFIGURATION],
        )
    assert tx.subentry.data == before
    assert tx.journal.saved == tx.writes == []
