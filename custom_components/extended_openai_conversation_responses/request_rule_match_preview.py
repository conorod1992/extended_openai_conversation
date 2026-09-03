"""Side-effect-free management preview for Request Rule matching."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui
from .management_permissions import install_management_permissions
from .request_rules import RuleMatch, async_get_request_rules

_PATCHED = "extended_openai_request_rule_match_preview"
ManagementCommand = Callable[
    [HomeAssistant, str, bool, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]
]


def request_rule_match_preview(match: RuleMatch | None) -> dict[str, Any]:
    """Return a frontend-safe summary of the rule that would win."""
    if match is None:
        return {"matched": False}

    rule = match.rule
    action = rule["action"]
    if rule["action_type"] == "local_action":
        would_do: dict[str, Any] = {
            "type": "local_action",
            "action_count": len(action.get("actions", [])),
        }
    else:
        would_do = {
            "type": "model_routing",
            "reset": bool(action.get("reset")),
            "model": action.get("model"),
            "reasoning_effort": action.get("reasoning_effort"),
            "scope": action.get("scope"),
        }

    return {
        "matched": True,
        "rule": {
            "id": rule["id"],
            "name": rule["name"],
            "match_type": rule["match_type"],
            "action_type": rule["action_type"],
        },
        "matched_phrase": match.phrase,
        "fuzzy": match.fuzzy,
        "score": round(match.score, 1),
        "captured_values": dict(match.slots),
        "would_do": would_do,
    }


def wrap_management_command(original: ManagementCommand) -> ManagementCommand:
    """Intercept Request Rule tests before the legacy real-processing path."""

    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        if message.get("section") != "request_rules" or message.get("action") not in {
            "test",
            "test_match",
        }:
            return await original(hass, user_id, is_admin, message)

        if not is_admin:
            raise HomeAssistantError("Administrator permission is required")
        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            raise HomeAssistantError("entry_id and subentry_id are required")
        management_ui.entry_and_agent(hass, entry_id, subentry_id)

        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HomeAssistantError("Test request text is required")
        rules = await async_get_request_rules(hass, entry_id, subentry_id)
        return request_rule_match_preview(rules.match(text.strip()))

    return wrapped


def install_request_rule_match_preview() -> bool:
    """Make both legacy and new management test actions side-effect free."""
    if getattr(management_ui, _PATCHED, False):
        install_management_permissions()
        return False
    original = management_ui.async_management_command
    setattr(  # noqa: B010
        management_ui,
        "async_management_command",
        wrap_management_command(original),
    )
    setattr(management_ui, _PATCHED, True)
    install_management_permissions()
    return True
