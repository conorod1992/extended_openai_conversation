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
