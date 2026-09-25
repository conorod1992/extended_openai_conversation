"""Portable Request Rule packs and captured AI handoff contracts."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    async_request_rule_match_preview,
)
from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    async_append_rule_pack,
    export_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    RequestRules,
    async_evaluate_rule,
    validate_rule,
)
from tests.test_request_rules import FakeServices, MemoryStore, local_rule


def captured_routing_rule():
    rule = local_rule("Deep think", ["deep think {question}"], "sentence_pattern")
    rule["action_type"] = "model_routing"
    rule["action"] = {
        "model": "gpt-5",
        "reasoning_effort": "",
        "scope": "request",
        "reset": False,
        "continue_to_ai": True,
    }
    rule["ai_input_mode"] = "capture"
    rule["ai_input_capture"] = "question"
    return rule


def test_captured_ai_input_requires_every_variant_and_branch() -> None:
    rule = captured_routing_rule()
    assert validate_rule(rule)["ai_input_capture"] == "question"
    rule["phrases"].append("think carefully")
    with pytest.raises(ValueError, match="every trigger|same slots"):
        validate_rule(rule)
    rule["phrases"] = ["deep think [{question}]"]
    with pytest.raises(ValueError, match="every match"):
        validate_rule(rule)
    rule["phrases"] = ["deep think {question}"]
    rule["ai_input_capture"] = "stale"
    with pytest.raises(ValueError, match="every trigger"):
        validate_rule(rule)


async def test_captured_ai_handoff_and_preview_share_resolved_input(hass) -> None:
    stored = RequestRules(MemoryStore({"rules": [captured_routing_rule()]}))
    await stored.async_initialize()
    text = "deep think why is the sky blue"
    evaluated = await async_evaluate_rule(
        hass, stored, RequestRuleRuntime(), text, "session"
    )
    preview = await async_request_rule_match_preview(hass, stored, text)
    assert evaluated is not None and not evaluated.consume
    assert evaluated.provider_input == "why is the sky blue"
    assert preview["matched_rules"][0]["ai_input"] == {
        "mode": "capture",
        "capture": "question",
        "provider_input": evaluated.provider_input,
    }


async def test_captured_ai_input_survives_backup_and_old_rules_default_original() -> None:
    stored = RequestRules(MemoryStore({"rules": [captured_routing_rule()]}))
    await stored.async_initialize()
    backup = await stored.async_backup_data()
    restored = RequestRules.validate_backup_data(backup)
    assert restored["rules"][0]["ai_input_mode"] == "capture"
    assert restored["rules"][0]["ai_input_capture"] == "question"
    old = RequestRules.validate_backup_data({"rules": [local_rule()]})
    assert old["rules"][0]["ai_input_mode"] == "original"
    assert old["rules"][0]["ai_input_capture"] is None


async def test_local_captured_handoff_only_after_success(hass) -> None:
    rule = local_rule("Ask", ["ask {question}"], "sentence_pattern")
    rule["action"]["continue_to_ai"] = True
    rule["ai_input_mode"] = "capture"
    rule["ai_input_capture"] = "question"
    stored = RequestRules(MemoryStore({"rules": [rule]}))
    await stored.async_initialize()
    services = FakeServices()
    hass.services = services
    success = await async_evaluate_rule(
        hass, stored, RequestRuleRuntime(), "ask why", "session"
    )
    assert success is not None and not success.consume
    assert success.provider_input == "why"
    assert len(services.calls) == 1
    hass.services = FakeServices(fail=True)
    failed = await async_evaluate_rule(
        hass, stored, RequestRuleRuntime(), "ask why", "session"
    )
    assert failed is not None and failed.consume and not failed.successful
    assert failed.provider_input is None


async def test_pack_export_import_appends_disabled_in_relative_order() -> None:
    first = local_rule("First", phrases=["first"], order=0)
    second = captured_routing_rule()
    second["order"] = 1
    second["continue_matching"] = True
    source = RequestRules(MemoryStore({"rules": [first, second]}))
    await source.async_initialize()
    pack = export_rule_pack(source, "all")
    assert pack["format"] == "extended_openai_request_rule_pack"
    assert [rule["order"] for rule in pack["rules"]] == [0, 1]
    assert pack["rules"][1]["ai_input_capture"] == "question"
    prepared = validate_rule_pack(pack)
    target = RequestRules(MemoryStore({"rules": [local_rule("Existing")]}))
    await target.async_initialize()
    result = await async_append_rule_pack(
        target, prepared, expected_revision=target.revision()
    )
    assert [rule["name"] for rule in target.snapshot()["rules"]] == [
        "Existing", "First", "Deep think"
    ]
    assert all(not rule["enabled"] for rule in result["rules"])
    assert all(rule["id"] not in {"first", "deep-think"} for rule in result["rules"])
    assert result["rules"][1]["continue_matching"] is True
    assert result["rules"][1]["ai_input_capture"] == "question"


async def test_pack_group_and_selected_exports_keep_subset_order() -> None:
    first = local_rule("First", phrases=["activate lights"], order=0)
    second = local_rule("Second", phrases=["good night"], order=1)
    third = local_rule("Third", phrases=["power up lights"], order=2)
    first["group_id"] = third["group_id"] = "lighting"
    source = RequestRules(MemoryStore({
        "groups": [{"id": "lighting", "name": "Lighting"}],
        "wording_groups": [{"canonical": "activate", "alternatives": ["power up"]}],
        "rules": [first, second, third],
    }))
    await source.async_initialize()
    group = export_rule_pack(source, "group", "lighting")
    assert [rule["name"] for rule in group["rules"]] == ["First", "Third"]
    assert [rule["order"] for rule in group["rules"]] == [0, 1]
    assert [item["name"] for item in group["groups"]] == ["Lighting"]
    assert group["wording_groups"] == [
        {"canonical": "activate", "alternatives": ["power up"]}
    ]
    selected = export_rule_pack(source, "selected", rule_ids=[third["id"], second["id"]])
    assert [rule["name"] for rule in selected["rules"]] == ["Second", "Third"]
    assert selected["groups"][0]["name"] == "Lighting"


def test_pack_rejects_newer_version_unknown_executable_and_duplicate_ids() -> None:
    rule = validate_rule(local_rule())
    pack = {
        "format": "extended_openai_request_rule_pack",
        "version": 2,
        "groups": [],
        "wording_groups": [],
        "rules": [rule],
    }
    with pytest.raises(ValueError, match="version"):
        validate_rule_pack(pack)
    pack["version"] = 1
    pack["rules"][0]["unexpected_action"] = True
    with pytest.raises(ValueError, match="unknown rule fields"):
        validate_rule_pack(pack)
    del pack["rules"][0]["unexpected_action"]
    pack["rules"] = [rule, deepcopy(rule)]
    pack["rules"][1]["order"] = 1
    with pytest.raises(ValueError, match="duplicate rule IDs"):
        validate_rule_pack(pack)
