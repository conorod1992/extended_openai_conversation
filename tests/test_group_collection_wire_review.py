"""Independent Group-repair reads and admission outcomes through HA's wire."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from homeassistant.components.websocket_api.messages import message_to_json_bytes
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_group_collection_repair import _group, _source
from tests.test_management_wire_contracts import exchange
from tests.test_request_rules import MemoryStore


@pytest.fixture
async def review_group_agent(hass, management_agent, monkeypatch):
    entry, subentry = management_agent
    subentry.data = _source(42)
    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    return entry, subentry, rules


def message(entry, subentry, section, action, **fields):
    return {
        "section": section,
        "action": action,
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "revision": repair.repair_revision(subentry),
        **fields,
    }


@pytest.mark.parametrize(
    ("section", "action"),
    [
        ("function_repair", "get"),
        ("configuration", "get"),
        ("function_repair", "configuration_get"),
        ("tools", "validate_current"),
        ("guest_mode", "details"),
    ],
)
async def test_collection_repair_metadata_survives_each_public_read(
    hass, review_group_agent, monkeypatch, section, action
):
    entry, subentry, _rules = review_group_agent
    subentry.data = _source([_group("duplicate"), _group("duplicate", name="Second")])
    subentry.data["guest_allowed_group_ids"] = ["duplicate"]
    before = deepcopy(subentry.data)
    monkeypatch.setattr(
        ui, "async_get_guest_mode", AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(ui, "get_exposed_entities", lambda _: [])
    monkeypatch.setattr(
        ui,
        "resolve_guest_policy",
        lambda *a, **k: SimpleNamespace(as_diagnostics=lambda: {}),
    )
    monkeypatch.setattr(
        ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(async_list=AsyncMock(return_value=[]))),
    )

    frame = await exchange(hass, message(entry, subentry, section, action))
    assert frame["success"] is True
    result = frame["result"]
    metadata = (
        result
        if action == "get" and section == "function_repair"
        else result["function_repair"]
    )
    assert metadata["group_collection_repair"] is True
    assert yaml.safe_load(metadata["groups_yaml"]) == before["function_groups"]
    assert metadata["guest_allowed_group_ids"] == ["duplicate"]
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("outcome", ["rejected", "uncertain"])
async def test_group_repair_wire_distinguishes_no_write_and_committed_revision_failure(
    hass, review_group_agent, monkeypatch, outcome
):
    entry, subentry, _rules = review_group_agent
    before = deepcopy(subentry.data)
    groups = [_group()]
    if outcome == "rejected":
        monkeypatch.setattr(
            ui,
            "_async_validate_configuration_dependencies",
            AsyncMock(
                side_effect=HomeAssistantError(
                    "Dependency validation refused the Group change"
                )
            ),
        )
    else:
        monkeypatch.setattr(
            repair,
            "saved_agent_config_revision",
            lambda *_: (_ for _ in ()).throw(
                RuntimeError("Revision unavailable after write")
            ),
        )

    frame = await exchange(
        hass, message(entry, subentry, "function_repair", "save_groups", groups=groups)
    )
    assert frame["success"] is False
    if outcome == "rejected":
        assert frame["error"]["code"] == "invalid_request"
        assert subentry.data == before
        hass.config_entries.async_update_subentry.assert_not_called()
    else:
        assert frame["error"]["code"] == "eoai_mutation_uncertain"
        assert subentry.data == {**before, "function_groups": groups}
        hass.config_entries.async_update_subentry.assert_called_once()
    assert subentry.data["functions"] == before["functions"]


@pytest.mark.parametrize(
    "outcome", ["committed", "rejected", "lost", "incomplete", "refresh_failure"]
)
async def test_complete_group_repair_producer_reaches_owned_editor(
    hass, review_group_agent, outcome
):
    entry, subentry, _rules = review_group_agent
    node = shutil.which("node")
    if node is None:
        if os.environ.get("RUN_REAL_HA_BROWSER") == "1":
            pytest.fail("Group repair producer-to-consumer coverage requires Node")
        pytest.skip("Node is required for the Group repair wire contract")
    before = deepcopy(subentry.data)
    snapshot = await exchange(hass, message(entry, subentry, "configuration", "get"))
    assert snapshot["success"] is True
    groups = [
        _group(functions=["missing"] if outcome == "rejected" else ["named_tool"])
    ]
    payload = {
        "groups": json.dumps(groups),
        "guest_allowed_group_ids": [],
        "revision": snapshot["result"]["revision"],
    }
    envelope = await exchange(
        hass, message(entry, subentry, "function_repair", "save_groups", **payload)
    )
    assert envelope["success"] is (outcome != "rejected")
    if outcome == "rejected":
        assert subentry.data == before
        hass.config_entries.async_update_subentry.assert_not_called()
    else:
        assert subentry.data == {
            **before,
            "function_groups": groups,
            "guest_allowed_group_ids": [],
        }
        hass.config_entries.async_update_subentry.assert_called_once()
    assert subentry.data["functions"] == before["functions"]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("group-collection-contract-runner.mjs"))],
        input=message_to_json_bytes(
            {
                "before": snapshot["result"],
                "payload": payload,
                "envelope": envelope,
                "outcome": outcome,
            }
        ),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
