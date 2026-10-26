"""Guest inherited areas and repair-save runtime update contracts."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    guest_mode,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    GUEST_POLICY_VERSION,
)
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    is_live_subentry_update,
)
from tests.test_function_repair_configuration_contract import _repairable_agent


@pytest.mark.parametrize(
    "selector,expected",
    [
        ("guest_excluded_areas", (False, False)),
        ("guest_excluded_labels", (False, False)),
        ("guest_control_excluded_areas", (True, False)),
    ],
)
@pytest.mark.parametrize("override", [None, "device", "entity"])
def test_guest_inherited_area_permissions(
    hass, monkeypatch, selector, expected, override
):
    parent = SimpleNamespace(area_id="bedroom")
    child = SimpleNamespace(
        area_id="hall" if override == "device" else None,
        parent_device_id="parent",
        labels=set(),
    )
    entity = SimpleNamespace(
        area_id="hall" if override == "entity" else None,
        device_id="child",
        labels=set(),
    )
    monkeypatch.setattr(
        guest_mode.er,
        "async_get",
        lambda _: SimpleNamespace(async_get=lambda _: entity),
    )
    monkeypatch.setattr(
        guest_mode.dr, "async_get", lambda _: SimpleNamespace(async_get=lambda _: child)
    )
    monkeypatch.setattr(
        guest_mode.dr,
        "async_get_effective_area_id",
        lambda _, device: device.area_id or parent.area_id,
        raising=False,
    )
    monkeypatch.setattr(
        guest_mode.ar,
        "async_get",
        lambda _: SimpleNamespace(
            async_get_area=lambda area: SimpleNamespace(
                labels={"private"} if area == "bedroom" else set()
            )
        ),
    )
    monkeypatch.setattr(
        guest_mode, "get_exposed_entities", lambda _: [{"entity_id": "light.child"}]
    )
    options = {
        "guest_policy_version": GUEST_POLICY_VERSION,
        "guest_separate_control_restrictions": True,
        selector: ["private" if selector.endswith("labels") else "bedroom"],
    }
    policy = guest_mode.resolve_guest_policy(
        hass, options, SimpleNamespace(is_active=lambda: True)
    )
    assert (
        policy.allows_entity_read("light.child"),
        policy.allows_entity_control("light.child"),
    ) == ((True, True) if override else expected)


@pytest.mark.parametrize("legacy", [False, True])
def test_guest_area_resolution_older_ha_fallback(hass, monkeypatch, legacy):
    entity = SimpleNamespace(area_id=None, device_id="child", labels=set())
    child = SimpleNamespace(area_id="bedroom", labels=set())
    monkeypatch.delattr(guest_mode.dr, "async_get_effective_area_id", raising=False)
    monkeypatch.setattr(
        guest_mode.er,
        "async_get",
        lambda _: SimpleNamespace(async_get=lambda _: entity),
    )
    monkeypatch.setattr(
        guest_mode.dr, "async_get", lambda _: SimpleNamespace(async_get=lambda _: child)
    )
    monkeypatch.setattr(
        guest_mode.ar,
        "async_get",
        lambda _: SimpleNamespace(async_get_area=lambda _: None),
    )
    hass.states.async_all.return_value = [SimpleNamespace(entity_id="light.child")]
    if legacy:
        result = guest_mode._resolve_legacy_entity_ids(
            hass, {"guest_readable_areas": ["bedroom"]}, control=False
        )
    else:
        result = guest_mode.resolve_guest_selector_entity_ids(
            hass, {"light.child"}, areas=["bedroom"]
        )
    assert result == {"light.child"}


@pytest.mark.parametrize(
    "changes,expected",
    [
        (
            {
                "config": {
                    "usage_request_retention_days": 0,
                    "usage_run_retention_days": 0,
                }
            },
            False,
        ),
        ({"title": "Renamed repair agent"}, False),
        ({"config": {"max_tokens": 700}}, True),
    ],
)
async def test_repair_configuration_save_obeys_runtime_eligibility(
    hass, monkeypatch, changes, expected
):
    entry, subentry = _repairable_agent()
    raw_tools = deepcopy(subentry.data[CONF_FUNCTION_TOOLS])
    markers = []

    def persist(_entry, target, **updates):
        markers.append(is_live_subentry_update())
        target.data = updates["data"]
        if "title" in updates:
            target.title = updates["title"]

    hass.config_entries = SimpleNamespace(async_update_subentry=persist)
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *_, **__: (entry, subentry)
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": []})),
    )
    response = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "function_repair",
            "action": "configuration_save",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            **changes,
        },
    )
    assert response["valid"] is True
    assert markers == [expected]
    assert subentry.data[CONF_FUNCTION_TOOLS] == raw_tools
