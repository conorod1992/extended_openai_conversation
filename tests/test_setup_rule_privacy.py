"""Shareable Setup projects Request Rules through the public-content boundary."""

import json

from tests.test_request_rules import MemoryStore, RequestRules, local_rule, manager
from tests.test_sharing_schema_context import rule_setup_artifact


async def test_rule_credentials_and_derived_signatures_do_not_leave(hass):
    secret = "fabricated-unknown-credential-42"
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": {"password": secret}}
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    assert secret in manager.snapshot()["rules"][0]["action"]["canonical_signature"]
    setup = rule_setup_artifact(manager.snapshot())
    rules = setup["document"]["sections"]["request_rules"]["rules"]
    assert secret not in setup["json"]
    assert rules == []
    assert setup["security"]["omitted"]
    assert secret in manager.snapshot()["rules"][0]["action"]["canonical_signature"]


async def test_historical_reviewed_setup_preserves_custom_configuration_and_disabled_rules(hass):
    from custom_components.extended_openai_conversation_responses import transfer
    from tests.test_transfer import _portable_document

    rule = local_rule("Historical example", enabled=False)
    stored = await manager(rule)
    document = _portable_document(mode="setup")
    document["version"] = 2
    document["security"] = {
        "policy_version": 1,
        "mode": "reviewed_custom",
        "review_required": True,
        "omitted": [],
    }
    document["sections"]["configuration"]["prompt"] = "A previously shared prompt."
    document["sections"]["request_rules"] = await stored.async_backup_data()

    prepared = transfer.inspect_transfer(json.dumps(document), "destination")
    assert prepared.security["mode"] == "reviewed_custom"
    assert prepared.config["prompt"] == "A previously shared prompt."
    assert prepared.request_rules["rules"] == (await stored.async_backup_data())["rules"]
    assert prepared.request_rules["rules"][0]["enabled"] is False
