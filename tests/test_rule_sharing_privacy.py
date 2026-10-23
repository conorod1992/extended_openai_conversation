"""Rule Sharing uses the same conservative public-content boundary."""

import json

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    export_rule_pack,
    validate_rule_pack,
)
from tests.test_request_rules import MemoryStore, RequestRules, local_rule


@pytest.mark.parametrize("selection", ["all", "selected", "group"])
@pytest.mark.parametrize("reviewed", [False, True])
async def test_rule_credentials_and_derived_signatures_do_not_leave(
    selection, reviewed, hass
):
    secret = "fabricated-unknown-credential-42"
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": {"password": secret}}
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    assert secret in manager.snapshot()["rules"][0]["action"]["canonical_signature"]
    pack = export_rule_pack(
        manager, selection, rule_ids=[rule["id"]], reviewed=reviewed
    )
    assert secret not in json.dumps(pack)
    assert pack["rules"] == []
    assert pack["security"]["omitted"]
    assert validate_rule_pack(pack)["rules"] == []
    assert secret in manager.snapshot()["rules"][0]["action"]["canonical_signature"]


async def test_unclassified_rule_content_requires_review(hass):
    secret = "ordinary opaque words"
    rule = local_rule(phrases=[secret])
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    assert secret not in json.dumps(export_rule_pack(manager, "all"))
    reviewed = export_rule_pack(manager, "all", reviewed=True)
    assert reviewed["rules"][0]["phrases"] == [secret]
    assert "canonical_signature" not in reviewed["rules"][0]["action"]
    assert reviewed["rules"][0]["enabled"] is False
    assert validate_rule_pack(reviewed)["rules"]
