"""Shareable Setup projects Request Rules through the public-content boundary."""

import json

from tests.test_request_rules import local_rule, manager


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
