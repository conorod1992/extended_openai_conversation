"""Restored Rule values remain exact through unrelated browser mutations."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui as ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from homeassistant.exceptions import HomeAssistantError
from tests import test_selective_restore_concurrency as concurrency
from tests.test_management_typed_function_roundtrips import _wire
from tests.test_private_backup_fidelity import private_document
from tests.test_request_rules import (
    MemoryStore,
    RequestRules,
    local_rule,
    validate_rule,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests" / "fixtures" / "frontend" / "rule-enabled-wire.mjs"


@pytest.mark.parametrize("frontend", ["request-rules-ui-core.js", "shipped"])
@pytest.mark.parametrize(
    "credential",
    [
        2**53 + 1,
        2**63 - 1,
        -(2**53 + 1),
        2**53 - 1,
        str(2**63 - 1),
    ],
)
async def test_restored_rule_numeric_credential_toggle(
    hass,
    monkeypatch,
    management_agent,
    management_message,
    credential,
    frontend,
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to exercise the production frontend module")
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": "rest_command.report",
            "data": {"password": credential, "units": "c"},
        }
    ]
    assert validate_rule(rule)["action"]["actions"][0]["data"]["password"] == credential
    source = RequestRules(MemoryStore({"rules": [rule]}))
    await source.async_initialize()
    original_rules = await source.async_backup_data()
    document = private_document()
    document["request_rules"] = original_rules
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact)["request_rules"] == original_rules
    monkeypatch.setattr(concurrency, "_identity", lambda: management_agent)
    tx = concurrency.RestoreHarness(monkeypatch)
    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        artifact,
        sections=[transfer.SECTION_REQUEST_RULES],
    )
    restored = RequestRules(MemoryStore(tx.state.request_rules))
    await restored.async_initialize()
    assert (
        restored.snapshot()["rules"][0]["action"]["actions"][0]["data"]["password"]
        == credential
    )
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=restored))
    listed = await ui.async_management_command(
        hass, "admin", True, management_message("request_rules", "list")
    )
    wire = _wire(listed)
    assert wire["rules"][0]["action"]["actions"][0]["data"]["password"] == credential
    if frontend == "shipped":
        manifest = json.loads(
            (
                REPO
                / "custom_components/extended_openai_conversation_responses/frontend/dist/manifest.json"
            ).read_text()
        )
        frontend = "dist/" + next(
            item["file"]
            for item in manifest.values()
            if item["file"].startswith("assets/request-rules-ui-core-")
        )
    result = subprocess.run(
        [
            node,
            str(SCRIPT),
            str(
                REPO
                / "custom_components/extended_openai_conversation_responses/frontend"
                / frontend
            ),
        ],
        input=json.dumps(wire),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["rule"]["enabled"] is False
    assert payload["rule"] == {"enabled": False}
    assert "password" not in result.stdout
    await ui.async_management_command(
        hass, "admin", True, management_message("request_rules", "update", **payload)
    )
    saved = restored.snapshot()["rules"][0]
    assert saved["enabled"] is False
    assert saved["action"]["actions"][0]["data"]["password"] == credential
    assert saved["action"]["actions"] == original_rules["rules"][0]["action"]["actions"]


@pytest.fixture
async def numeric_rules(hass, monkeypatch, management_agent):
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": {"password": 2**53 + 1}}
    ]
    store = MemoryStore({"rules": [rule]})
    manager = RequestRules(store)
    await manager.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    return manager, store


@pytest.mark.parametrize(
    "change", ["rounded", "removed", "string", "float", "container"]
)
async def test_full_management_updates_reject_loss_of_existing_exact_integers(
    hass, numeric_rules, management_message, change
):
    manager, store = numeric_rules
    before = manager.snapshot()
    stored = deepcopy(store.data)
    rule = deepcopy(before["rules"][0])
    data = rule["action"]["actions"][0]["data"]
    if change == "removed":
        del data["password"]
    else:
        data["password"] = {
            "rounded": 2**53,
            "string": str(2**53 + 1),
            "float": float(2**53 + 1),
            "container": {"value": 2**53 + 1},
        }[change]
    with pytest.raises(ValueError, match="outside the browser's exact range") as caught:
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "request_rules", "update", rule_id=rule["id"], rule=rule
            ),
        )
    assert str(2**53 + 1) not in str(caught.value)
    assert manager.snapshot() == before
    assert store.data == stored


async def test_native_exact_full_update_remains_supported(
    hass, numeric_rules, management_message
):
    manager, _store = numeric_rules
    rule = manager.snapshot()["rules"][0]
    rule["name"] = "Renamed exactly"
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("request_rules", "update", rule_id=rule["id"], rule=rule),
    )
    assert result["rule"]["name"] == "Renamed exactly"
    assert result["rule"]["action"]["actions"][0]["data"]["password"] == 2**53 + 1


@pytest.mark.parametrize("enabled", ["false", 0, None])
async def test_field_only_toggle_requires_a_boolean(
    hass, numeric_rules, management_message, enabled
):
    manager, _store = numeric_rules
    before = manager.snapshot()
    with pytest.raises((ValueError, HomeAssistantError), match="enabled"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "request_rules",
                "update",
                rule_id=before["rules"][0]["id"],
                rule={"enabled": enabled},
            ),
        )
    assert manager.snapshot() == before


async def test_field_only_toggle_retains_administrator_authorization(
    hass, numeric_rules, management_message
):
    manager, _store = numeric_rules
    before = manager.snapshot()
    with pytest.raises(HomeAssistantError):
        await ui.async_management_command(
            hass,
            "other-user",
            False,
            management_message(
                "request_rules",
                "update",
                rule_id=before["rules"][0]["id"],
                rule={"enabled": False},
            ),
        )
    assert manager.snapshot() == before


async def test_field_only_toggle_rejects_concurrent_change_during_validation(
    hass, monkeypatch, numeric_rules, management_message
):
    manager, _store = numeric_rules
    rule_id = manager.snapshot()["rules"][0]["id"]

    async def concurrent_edit(*_args, **_kwargs):
        current = manager.snapshot()["rules"][0]
        current["name"] = "Concurrent change"
        await manager.async_update(rule_id, current)
        return []

    monkeypatch.setattr(ui, "_validate_request_rule_conditions", concurrent_edit)
    with pytest.raises(ValueError, match="changed in another tab"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "request_rules", "update", rule_id=rule_id, rule={"enabled": False}
            ),
        )
    saved = manager.snapshot()["rules"][0]
    assert saved["name"] == "Concurrent change"
    assert saved["enabled"] is True
    assert saved["action"]["actions"][0]["data"]["password"] == 2**53 + 1


async def test_single_rule_hydration_is_detached_and_does_not_read_the_collection(
    monkeypatch, numeric_rules
):
    manager, _store = numeric_rules
    rule_id = manager.snapshot()["rules"][0]["id"]

    def no_collection_snapshot():
        raise AssertionError("A field patch must not snapshot every Rule")

    monkeypatch.setattr(manager, "snapshot", no_collection_snapshot)
    candidate = ui._prepare_request_rule({"enabled": False}, rule_id, manager)
    candidate["action"]["actions"][0]["data"]["password"] = "changed local copy"
    assert manager.rule_snapshot(rule_id)["enabled"] is True
    assert (
        manager.rule_snapshot(rule_id)["action"]["actions"][0]["data"]["password"]
        == 2**53 + 1
    )


async def test_field_only_toggle_rejects_unknown_rule(numeric_rules):
    manager, _store = numeric_rules
    with pytest.raises(ValueError, match="Request Rule not found"):
        ui._prepare_request_rule({"enabled": False}, "missing-rule", manager)
