"""Server export -> actual downloaded text -> raw importer preservation."""

import json
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui as ui,
    request_rule_packs as packs,
)
from tests.test_remaining_management_contracts import _request
from tests.test_request_rules import MemoryStore, RequestRules, local_rule


@pytest.mark.parametrize("value", [2**53 + 1, -(2**63) + 1, 10**80])
@pytest.mark.parametrize("reviewed", [False, True])
async def test_management_text_preserves_artifact_and_review(
    hass, monkeypatch, value, reviewed
):
    rule = local_rule(phrases=["portable payload"])
    payload = {
        "counter": value,
        "literal": 'é😀\n\r\t\\"<tag>\u2028',
        "flags": [True, False, None, "null"],
    }
    rule["action"]["actions"] = [{"action": "rule_probe.record", "data": payload}]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    before = manager.snapshot()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    response = await ui.async_request_rules_command(
        _request(
            hass,
            "request_rules",
            "rule_pack_export",
            selection="all",
            reviewed_custom=reviewed,
            export_text=True,
        )
    )
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("rule-pack-download-wire.mjs"))],
        input=json.dumps(response),
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    wire = json.loads(result.stdout)
    assert wire["downloaded"] == wire["json"] == response["json"]
    document = json.loads(wire["downloaded"])
    assert set(document) == {"format", "version", "groups", "rules", "security"}
    assert wire["security"]["review_required"] is reviewed
    parsed = packs.validate_rule_pack(wire["downloaded"])
    if reviewed:
        assert parsed["rules"][0]["action"]["actions"][0]["data"] == payload
    else:
        assert parsed["rules"] == [] and document["security"]["omitted"]
    assert manager.snapshot() == before
    # Old clients retain the old object response and file schema.
    legacy = await ui.async_request_rules_command(
        _request(
            hass,
            "request_rules",
            "rule_pack_export",
            selection="all",
            reviewed_custom=reviewed,
        )
    )
    assert legacy == document and "json" not in legacy


async def test_exact_final_server_budget_includes_security(monkeypatch):
    manager = RequestRules(MemoryStore({"rules": [local_rule(phrases=["é😀\n"])]}))
    await manager.async_initialize()
    artifact = packs.export_rule_pack(manager, "all", reviewed=True)
    text = packs.serialize_rule_pack(artifact)
    size = len(text.encode("utf-8"))
    assert size > len(text)
    monkeypatch.setattr(packs, "MAX_PACK_BYTES", size)
    assert (
        packs.serialize_rule_pack(packs.export_rule_pack(manager, "all", reviewed=True))
        == text
    )
    monkeypatch.setattr(packs, "MAX_PACK_BYTES", size - 1)
    with pytest.raises(ValueError, match="2 MB"):
        packs.export_rule_pack(manager, "all", reviewed=True)


async def test_text_is_serialized_after_credential_omission(hass, monkeypatch):
    rule = local_rule()
    secret = "fabricated-secret-for-test"
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": {"password": secret}}
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    response = await ui.async_request_rules_command(
        _request(
            hass,
            "request_rules",
            "rule_pack_export",
            selection="all",
            reviewed_custom=True,
            export_text=True,
        )
    )
    assert secret not in response["json"]
    assert json.loads(response["json"])["security"]["omitted"]
    assert packs.validate_rule_pack(response["json"])["rules"] == []
