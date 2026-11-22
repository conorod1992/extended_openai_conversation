"""Shareable Setup projects Request Rules through the public-content boundary."""

import pytest

from tests.test_request_rules import MemoryStore, RequestRules, local_rule
from tests.test_sharing_schema_context import rule_setup_artifact


@pytest.mark.parametrize("reviewed", [False, True])
async def test_rule_credentials_and_derived_signatures_do_not_leave(reviewed, hass):
    secret = "fabricated-unknown-credential-42"
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": {"password": secret}}
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    assert secret in manager.snapshot()["rules"][0]["action"]["canonical_signature"]
    setup = rule_setup_artifact(manager.snapshot(), reviewed=reviewed)
    rules = setup["document"]["sections"]["request_rules"]["rules"]
    assert secret not in setup["json"]
    assert rules == []
    assert setup["security"]["omitted"]
    assert secret in manager.snapshot()["rules"][0]["action"]["canonical_signature"]


async def test_unclassified_rule_content_requires_review(hass):
    secret = "ordinary opaque words"
    rule = local_rule(phrases=[secret])
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    assert secret not in rule_setup_artifact(manager.snapshot())["json"]
    reviewed = rule_setup_artifact(manager.snapshot(), reviewed=True)
    rules = reviewed["document"]["sections"]["request_rules"]["rules"]
    assert rules[0]["phrases"] == [secret]
    assert "canonical_signature" not in rules[0]["action"]
    assert rules[0]["enabled"] is False


async def test_reviewed_setup_omits_rules_when_group_metadata_contains_a_secret(hass):
    secret = "fabricated-group-credential"
    group = {"id": "private", "name": f"https://example.invalid/?api_key={secret}"}
    rule = {**local_rule(), "group_id": group["id"]}
    manager = RequestRules(MemoryStore({"rules": [rule], "groups": [group]}))
    await manager.async_initialize()
    before = await manager.async_backup_data()

    setup = rule_setup_artifact(before, reviewed=True)
    public_rules = setup["document"]["sections"]["request_rules"]

    assert secret not in setup["json"]
    assert public_rules["groups"] == []
    assert public_rules["rules"] == []
    assert any(
        item["field"] == "rule_metadata" for item in setup["security"]["omitted"]
    )
    assert await manager.async_backup_data() == before
