"""Stable coverage for Management Rule Pack review orchestration."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_rule,
)
from tests.test_request_rules import local_rule


def _canonical_rule(name: str, actions: list[dict], *, group_id=None) -> dict:
    rule = local_rule(name, phrases=[name.casefold()])
    rule["action"]["actions"] = actions
    rule["group_id"] = group_id
    return validate_rule(rule)


class _Rules:
    def __init__(self, *, rules=None, groups=None, revision="revision-1"):
        self._snapshot = {
            "rules": list(rules or []),
            "groups": list(groups or []),
            "revision": revision,
        }

    def snapshot(self):
        return deepcopy(self._snapshot)


@pytest.mark.asyncio
async def test_rule_pack_review_reports_missing_dependencies_without_execution(
    hass, monkeypatch
) -> None:
    ready = _canonical_rule(
        "Ready",
        [
            {
                "action": "light.turn_on",
                "target": {"entity_id": ["light.present"]},
            }
        ],
        group_id="lighting",
    )
    attention = _canonical_rule(
        "Needs attention",
        [
            {
                "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                "data": {"function": "missing_tool", "arguments": {}},
            },
            {
                "action": "switch.turn_on",
                "target": {
                    "entity_id": [
                        "switch.present",
                        "switch.missing",
                        "{{ dynamic_entity }}",
                    ]
                },
            },
        ],
    )
    prepared = {
        "groups": [{"id": "lighting", "name": "Lighting"}],
        "rules": [ready, attention],
    }
    rules = _Rules()
    monkeypatch.setattr(
        management_ui,
        "_validate_request_rule_conditions",
        AsyncMock(),
    )
    function_validation = AsyncMock()
    monkeypatch.setattr(
        management_ui,
        "async_validate_request_rule_functions",
        function_validation,
    )
    hass.states.get.side_effect = lambda entity_id: (
        SimpleNamespace(entity_id=entity_id)
        if entity_id in {"light.present", "switch.present"}
        else None
    )
    hass.services.has_service.side_effect = lambda domain, service: (
        domain,
        service,
    ) in {("light", "turn_on"), ("switch", "turn_on")}

    review = await management_ui._review_rule_pack(
        hass, prepared, rules, configured_tools=[]
    )

    assert review["count"] == 2
    assert review["ready"] == 1
    assert review["needs_attention"] == 1
    assert review["new_groups"] == 1
    assert review["revision"] == "revision-1"
    assert review["will_append"] is True
    assert review["will_disable"] is True
    assert review["rules"][0]["status"] == "ready"
    assert review["rules"][0]["group"] == "Lighting"
    assert review["rules"][0]["entities"] == ["light.present"]
    assert review["rules"][0]["services"] == ["light.turn_on"]
    missing = review["rules"][1]
    assert missing["status"] == "needs_attention"
    assert missing["group"] == "Ungrouped"
    assert missing["function_tools"] == ["missing_tool"]
    assert missing["entities"] == ["switch.missing", "switch.present"]
    assert missing["services"] == ["switch.turn_on"]
    assert missing["missing_dependencies"] == [
        "missing_tool",
        "switch.missing",
    ]
    assert function_validation.await_count == 2


@pytest.mark.asyncio
async def test_rule_pack_review_does_not_count_existing_group_name_as_new(
    hass, monkeypatch
) -> None:
    rule = _canonical_rule("Ready", [])
    prepared = {
        "groups": [{"id": "source-group", "name": "LIGHTING"}],
        "rules": [rule],
    }
    rules = _Rules(groups=[{"id": "target-group", "name": "Lighting"}])
    monkeypatch.setattr(
        management_ui, "_validate_request_rule_conditions", AsyncMock()
    )
    monkeypatch.setattr(
        management_ui, "async_validate_request_rule_functions", AsyncMock()
    )

    review = await management_ui._review_rule_pack(
        hass, prepared, rules, configured_tools=[]
    )

    assert review["new_groups"] == 0


@pytest.mark.asyncio
async def test_rule_pack_review_enforces_total_rule_limit(hass) -> None:
    prepared = {"groups": [], "rules": [_canonical_rule("Extra", [])]}
    rules = _Rules(rules=[{} for _ in range(500)])

    with pytest.raises(HomeAssistantError, match="Request Rule limit reached"):
        await management_ui._review_rule_pack(
            hass, prepared, rules, configured_tools=[]
        )


@pytest.mark.asyncio
async def test_rule_pack_review_enforces_total_group_limit(hass) -> None:
    prepared = {
        "groups": [{"id": "new", "name": "New group"}],
        "rules": [],
    }
    rules = _Rules(
        groups=[
            {"id": f"group-{index}", "name": f"Group {index}"}
            for index in range(100)
        ]
    )

    with pytest.raises(HomeAssistantError, match="Group limit reached"):
        await management_ui._review_rule_pack(
            hass, prepared, rules, configured_tools=[]
        )


@pytest.mark.asyncio
async def test_rule_condition_validation_wraps_home_assistant_error(
    hass, monkeypatch
) -> None:
    from homeassistant.helpers import condition as ha_condition

    validator = AsyncMock(side_effect=ValueError("bad condition"))
    monkeypatch.setattr(ha_condition, "async_validate_condition_config", validator)

    with pytest.raises(HomeAssistantError, match="Invalid Only when condition"):
        await management_ui._validate_request_rule_conditions(
            hass,
            {
                "conditions": [
                    {
                        "condition": "state",
                        "entity_id": "input_boolean.test",
                        "state": "on",
                    }
                ]
            },
        )


@pytest.mark.asyncio
async def test_rule_condition_validation_checks_each_condition(
    hass, monkeypatch
) -> None:
    from homeassistant.helpers import condition as ha_condition

    validator = AsyncMock()
    monkeypatch.setattr(ha_condition, "async_validate_condition_config", validator)
    rule = {
        "conditions": [
            {"condition": "state", "entity_id": "input_boolean.one", "state": "on"},
            {"condition": "state", "entity_id": "input_boolean.two", "state": "off"},
        ]
    }

    await management_ui._validate_request_rule_conditions(hass, rule)

    assert validator.await_count == 2
