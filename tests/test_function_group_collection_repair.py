"""Complete Group repair owns Groups and explicitly reviewed Group references."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui as ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    configured_function_tools_from_data,
    validate_function_groups,
)
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_configuration_repair_state,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    group_collection_repair_metadata,
    persisted_config_projection,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from homeassistant.exceptions import HomeAssistantError
from tests.function_configuration_corpus import FUNCTION_CONFIGURATION_CASES
from tests.test_private_backup_fidelity import private_document
from tests.test_request_rules import MemoryStore
from tests.test_selective_restore_concurrency import RestoreHarness


def _group(identifier="repaired", **changes):
    return {
        "id": identifier,
        "name": f"Group {identifier}",
        "description": "Reviewed collection",
        "functions": ["named_tool"],
        "loading_mode": "always",
        "enabled": False,
        **changes,
    }


MALFORMED_GROUPS = [
    42,
    {"unexpected": "mapping"},
    [42],
    [{"name": "Missing identity"}],
    [_group(42)],
    [_group("")],
    [_group("duplicate"), _group("duplicate", name="Second group")],
]


def _source(groups):
    typed = next(
        case["config"]["functions"]
        for case in FUNCTION_CONFIGURATION_CASES
        if case["effective"] == "disabled"
    )
    return {
        "functions": "# Exact authored Function source must survive Group repair\n"
        + yaml.safe_dump(
            [
                {
                    "spec": {
                        "name": "named_tool",
                        "description": "Usable Function sibling",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {"type": "template", "value_template": "Done"},
                }
            ]
        )
        + typed,
        "function_groups": deepcopy(groups),
        "skills": [],
        "prompt": "Unchanged prompt",
        "future_option": {"keep": True},
    }


@pytest.fixture
async def group_agent(hass, management_agent, monkeypatch):
    entry, subentry = management_agent
    subentry.data = _source(42)
    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    return entry, subentry, manager


async def _save(hass, entry, subentry, groups, **extra):
    return await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "function_repair",
            "action": "save_groups",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "revision": persisted_config_projection(subentry).revision,
            "groups": groups,
            **extra,
        },
    )


@pytest.mark.parametrize("malformed", MALFORMED_GROUPS)
@pytest.mark.parametrize("as_text", [False, True])
async def test_complete_group_repair_restores_usable_runtime_and_preserves_functions(
    hass, group_agent, malformed, as_text
):
    entry, subentry, _rules = group_agent
    subentry.data = _source(malformed)
    before = deepcopy(subentry.data)
    metadata = group_collection_repair_metadata(subentry.data)
    assert metadata["group_collection_repair"] is True
    assert yaml.safe_load(metadata["groups_yaml"]) == malformed
    groups = [_group()]
    result = await _save(
        hass, entry, subentry, yaml.safe_dump(groups) if as_text else groups
    )
    assert result["valid"] is True
    assert result["function_groups"] == groups
    assert result["function_repair"]["group_collection_repair"] is False
    assert result["function_repair"]["group_issues"] == []
    assert result["revision"] == persisted_config_projection(subentry).revision
    assert subentry.data == {**before, "function_groups": groups}
    runtime = configured_function_tools_from_data(json.loads(json.dumps(subentry.data)))
    assert validate_function_groups(subentry.data["function_groups"], runtime) == groups
    assert runtime[1]["enabled"] is False


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        42,
        "bad: yaml",
        [_group(), _group()],
        [_group(functions=["unknown"])],
        [_group(functions=["named_tool", "named_tool"])],
        [_group(), _group("other", name="Other")],
    ],
)
async def test_invalid_complete_group_repair_cannot_write_or_drop_raw_evidence(
    hass, group_agent, candidate
):
    entry, subentry, _rules = group_agent
    before = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError):
        await _save(hass, entry, subentry, candidate)
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("previous", ["duplicate", "removed", "malformed"])
async def test_guest_group_references_require_explicit_resolution(
    hass, group_agent, previous
):
    entry, subentry, _rules = group_agent
    subentry.data = _source(
        [_group("duplicate"), _group("duplicate", name="Second")]
        if previous == "duplicate"
        else 42
    )
    subentry.data["guest_allowed_group_ids"] = (
        42 if previous == "malformed" else [previous]
    )
    before = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="Explicit Guest Group references"):
        await _save(hass, entry, subentry, [_group()])
    assert subentry.data == before
    with pytest.raises(HomeAssistantError, match="unknown Function Group"):
        await _save(
            hass, entry, subentry, [_group()], guest_allowed_group_ids=["not-saved"]
        )
    assert subentry.data == before
    result = await _save(hass, entry, subentry, [_group()], guest_allowed_group_ids=[])
    assert result["valid"] is True
    assert subentry.data == {
        **before,
        "function_groups": [_group()],
        "guest_allowed_group_ids": [],
    }


async def test_revision_change_during_dependency_check_preserves_newer_source(
    hass, group_agent, monkeypatch
):
    entry, subentry, _rules = group_agent
    original = deepcopy(subentry.data)

    async def changed(*_args, **_kwargs):
        subentry.data = {**original, "prompt": "Newer setting"}

    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", changed)
    with pytest.raises(HomeAssistantError, match="changed"):
        await _save(hass, entry, subentry, [_group()])
    assert subentry.data == {**original, "prompt": "Newer setting"}
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_group_repair_keeps_named_quarantined_function_members(hass, group_agent):
    entry, subentry, _rules = group_agent
    quarantined = next(
        case["config"]["functions"]
        for case in FUNCTION_CONFIGURATION_CASES
        if case["id"] == "quarantined-native-list"
    )
    subentry.data["functions"] += quarantined
    before = deepcopy(subentry.data)
    result = await _save(
        hass, entry, subentry, [_group(functions=["named_tool", "unavailable_native"])]
    )
    assert result["valid"] is True
    assert subentry.data["functions"] == before["functions"]
    state = function_configuration_repair_state(subentry.data)
    assert state is not None and len(state.invalid) == 1
    assert state.group_issues[0]["unavailable_functions"] == ["unavailable_native"]


async def test_complete_group_repair_cannot_disable_working_skill_loader(
    hass, group_agent
):
    entry, subentry, _rules = group_agent
    loader = next(
        tool
        for tool in yaml.safe_load(agent_config_defaults()["functions"])
        if tool["spec"]["name"] == "load_skill"
    )
    subentry.data["functions"] += yaml.safe_dump([loader])
    subentry.data["skills"] = ["selected"]
    before = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="containing `load_skill` is disabled"):
        await _save(
            hass,
            entry,
            subentry,
            [_group("loader", functions=["load_skill"], enabled=False)],
        )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()
    await _save(
        hass,
        entry,
        subentry,
        [_group("loader", functions=["load_skill"], enabled=True)],
    )
    assert subentry.data["functions"] == before["functions"]
    assert subentry.data["skills"] == before["skills"]


async def test_complete_group_editor_can_save_later_edits_after_first_repair(
    hass, group_agent
):
    entry, subentry, _rules = group_agent
    source = deepcopy(subentry.data)
    first = await _save(hass, entry, subentry, [_group()])
    assert function_configuration_repair_state(subentry.data) is None
    later = [_group(name="Edited during first save")]
    second = await _save(hass, entry, subentry, later, revision=first["revision"])
    assert second["valid"] is True
    assert subentry.data == {**source, "function_groups": later}
    assert second["revision"] != first["revision"]
    with pytest.raises(HomeAssistantError, match="changed"):
        await _save(hass, entry, subentry, [_group()], revision=first["revision"])
    assert subentry.data == {**source, "function_groups": later}


async def test_serialized_restore_exposes_complete_group_repair_then_runtime_recovers(
    hass, monkeypatch
):
    tx = RestoreHarness(monkeypatch)
    original_update = tx.hass.config_entries.async_update_subentry

    def update(entry, subentry, *, data, title=None):
        original_update(
            entry, subentry, data=data, title=subentry.title if title is None else title
        )

    monkeypatch.setattr(tx.hass.config_entries, "async_update_subentry", update)
    document = private_document()
    document["agent"]["config"] = _source(
        [_group("duplicate"), _group("duplicate", name="Second")]
    )
    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        backup.finalize_backup_snapshot(document)["json"],
        sections=[transfer.SECTION_CONFIGURATION],
    )
    source = deepcopy(tx.subentry.data)
    tx.entry.data = {}
    tx.subentry.subentry_type = "conversation"
    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (tx.entry, tx.subentry))
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    repair = await ui.async_management_command(
        tx.hass,
        "admin",
        True,
        {
            "section": "function_repair",
            "action": "get",
            "entry_id": tx.entry.entry_id,
            "subentry_id": tx.subentry.subentry_id,
        },
    )
    assert repair["group_collection_repair"] is True
    assert yaml.safe_load(repair["groups_yaml"]) == source["function_groups"]
    await _save(tx.hass, tx.entry, tx.subentry, [_group()])
    reloaded = json.loads(json.dumps(tx.subentry.data))
    assert reloaded["functions"] == source["functions"]
    assert function_configuration_repair_state(reloaded) is None
