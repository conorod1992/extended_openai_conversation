"""Missing condition resources cannot prevent disabling or disabled pack import."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    export_rule_pack,
)
from homeassistant.components.device_automation import (
    condition as device_condition,
    helpers as device_helpers,
)
from homeassistant.components.switch import device_condition as switch_condition
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    condition,
    device_registry as dr,
    entity_registry as er,
)
from tests.test_management_ui import _entry_pair
from tests.test_request_rules import local_rule, manager

DEVICE = "0123456789abcdef0123456789abcdef"


@pytest.fixture
async def condition_management(hass, monkeypatch):
    devices = {DEVICE: SimpleNamespace(id=DEVICE)}
    registry = SimpleNamespace(
        devices=devices,
        async_get=lambda key: devices.get(key),
        async_get_devices_for_composite_device_id=lambda _: [],
    )
    monkeypatch.setattr(dr, "async_get", lambda _: registry)
    monkeypatch.setattr(er, "async_get", lambda _: object())
    monkeypatch.setattr(er, "async_validate_entity_id", lambda _, key: key)
    original = condition._async_get_condition_platform

    async def platform(hass, name):
        if name == "device":
            return "device_automation", device_condition
        return await original(hass, name)

    monkeypatch.setattr(condition, "_async_get_condition_platform", platform)
    monkeypatch.setattr(
        device_helpers,
        "async_get_device_automation_platform",
        AsyncMock(return_value=switch_condition),
    )
    # The recovery validator also loads the platform directly to distinguish
    # invalid authored fields from a missing device before HA's live check.
    import homeassistant.components.device_automation as automation

    monkeypatch.setattr(
        automation,
        "async_get_device_automation_platform",
        AsyncMock(return_value=switch_condition),
    )
    rules = await manager()
    entry, subentry = _entry_pair()
    subentry.data["functions"] = "[]"
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=rules)
    )

    async def command(action, **fields):
        return await management_ui.async_management_command(
            hass,
            "admin",
            True,
            {
                "section": "request_rules",
                "action": action,
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                **fields,
            },
        )

    return devices, rules, command


def device_rule(nested=False):
    rule = local_rule("Device condition", ["run"])
    leaf = {
        "condition": "device",
        "domain": "switch",
        "device_id": DEVICE,
        "entity_id": "switch.heater",
        "type": "is_on",
    }
    rule["conditions"] = (
        [{"condition": "and", "conditions": [leaf]}] if nested else [leaf]
    )
    return rule


@pytest.mark.parametrize("nested", [False, True])
async def test_disable_broken_condition_allows_later_rule_but_enable_stays_strict(
    hass, condition_management, nested
):
    devices, rules, command = condition_management
    created = (
        await command("create", rule=device_rule(nested), revision=rules.revision())
    )["rule"]
    later = await rules.async_create(local_rule("Later", ["run"]))
    devices.clear()
    with pytest.raises(HomeAssistantError, match="condition could not be evaluated"):
        await rules.async_match(hass, "run")
    disabled = deepcopy(created)
    disabled["enabled"] = False
    saved = await command(
        "update", rule_id=created["id"], rule=disabled, revision=rules.revision()
    )
    assert saved["rule"]["enabled"] is False
    assert (await rules.async_match(hass, "run")).rule["id"] == later["id"]
    disabled["enabled"] = True
    revision = rules.revision()
    with pytest.raises(HomeAssistantError, match="Unknown device"):
        await command("update", rule_id=created["id"], rule=disabled, revision=revision)
    assert rules.revision() == revision
    assert (
        next(rule for rule in rules.snapshot()["rules"] if rule["id"] == created["id"])[
            "enabled"
        ]
        is False
    )
    devices[DEVICE] = SimpleNamespace(id=DEVICE)
    assert (
        await command("update", rule_id=created["id"], rule=disabled, revision=revision)
    )["rule"]["enabled"] is True


async def test_missing_condition_is_reported_and_pack_imported_disabled(
    condition_management,
):
    devices, rules, command = condition_management
    await command("create", rule=device_rule(True), revision=rules.revision())
    pack = export_rule_pack(rules, "all")
    devices.clear()
    review = await command("rule_pack_review", pack=pack)
    assert review["needs_attention"] == 1 and review["ready"] == 0
    assert any(
        "Unknown device" in item for item in review["rules"][0]["missing_dependencies"]
    )
    result = await command(
        "rule_pack_import",
        pack=pack,
        confirm=True,
        revision=review["revision"],
        review_token=review["review_token"],
    )
    assert result["rules"][-1]["enabled"] is False
    assert result["rules"][-1]["conditions"] == device_rule(True)["conditions"]


@pytest.mark.parametrize("bad", ["missing_device_field", "invalid_type"])
async def test_disabled_conditions_still_reject_malformed_structure(
    condition_management, bad
):
    devices, rules, command = condition_management
    raw = device_rule()
    raw["enabled"] = False
    devices.clear()
    if bad == "missing_device_field":
        del raw["conditions"][0]["device_id"]
    else:
        raw["conditions"][0]["type"] = "not_a_switch_condition"
    with pytest.raises((HomeAssistantError, ValueError)):
        await command("create", rule=raw, revision=rules.revision())
    assert not rules.snapshot()["rules"]


async def test_unexpected_validation_failure_is_not_a_dependency_warning(
    hass, monkeypatch
):
    monkeypatch.setattr(
        condition,
        "async_validate_condition_config",
        AsyncMock(side_effect=RuntimeError("unexpected failure")),
    )
    raw = local_rule()
    raw["enabled"] = False
    raw["conditions"] = [{"condition": "template", "value_template": "{{ true }}"}]
    with pytest.raises(HomeAssistantError, match="unexpected failure"):
        await management_ui._validate_request_rule_conditions(hass, raw)


async def test_missing_earlier_condition_does_not_hide_malformed_later_branch(
    condition_management,
):
    devices, rules, command = condition_management
    raw = device_rule(True)
    raw["enabled"] = False
    malformed = deepcopy(raw["conditions"][0]["conditions"][0])
    malformed["type"] = "invalid_condition"
    raw["conditions"][0]["conditions"].append(malformed)
    devices.clear()
    with pytest.raises(HomeAssistantError, match="Invalid Only when condition"):
        await command("create", rule=raw, revision=rules.revision())
    assert not rules.snapshot()["rules"]


async def test_pack_review_rejects_malformed_platform_fields(condition_management):
    devices, rules, command = condition_management
    await command("create", rule=device_rule(), revision=rules.revision())
    pack = export_rule_pack(rules, "all")
    pack["rules"][0]["conditions"][0]["type"] = "invalid_condition"
    devices.clear()
    revision = rules.revision()
    with pytest.raises(HomeAssistantError, match="Invalid Only when condition"):
        await command("rule_pack_review", pack=pack)
    assert rules.revision() == revision
