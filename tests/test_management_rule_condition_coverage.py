"""Coverage for Management Request Rule condition validation."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from homeassistant.exceptions import HomeAssistantError


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
