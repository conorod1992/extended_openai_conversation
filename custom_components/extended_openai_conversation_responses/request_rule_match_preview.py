"""Side-effect-free management preview for Request Rule matching."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .request_rule_patterns import SentenceMatchLimitError
from .request_rules import RuleMatch, _iter_script_actions


def request_rule_match_preview(match: RuleMatch | None) -> dict[str, Any]:
    """Return a frontend-safe summary of the rule that would win."""
    if match is None:
        return {"matched": False}

    rule = match.rule
    action = rule["action"]
    if rule["action_type"] == "local_action":
        continue_to_ai = bool(action.get("continue_to_ai", False))
        functions = [
            {
                "name": step["data"]["function"],
                "result_alias": step["data"].get("result_alias"),
            }
            for step in _iter_script_actions(action.get("actions", []))
            if isinstance(step.get("data"), dict)
            and step.get("action", step.get("service"))
            == "extended_openai_conversation_responses.call_function"
            and isinstance(step["data"].get("function"), str)
        ]
        would_do: dict[str, Any] = {
            "type": "local_action",
            "action_count": len(action.get("actions", [])),
            "consumed": not continue_to_ai,
            "provider_input": "original" if continue_to_ai else "none",
            **({"functions": functions} if functions else {}),
        }
    else:
        continue_to_ai = action.get("continue_to_ai")
        if not isinstance(continue_to_ai, bool):
            # Direct preview callers may provide an unnormalized legacy rule.
            continue_to_ai = rule["match_type"] not in {"equals", "sentence_pattern"}
        consumed = not continue_to_ai
        would_do = {
            "type": "model_routing",
            "reset": bool(action.get("reset")),
            "model": action.get("model"),
            "reasoning_effort": action.get("reasoning_effort"),
            "scope": action.get("scope"),
            "consumed": consumed,
            "provider_input": "none" if consumed else "original",
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


async def async_request_rule_match_preview(
    hass: HomeAssistant,
    rules: Any,
    text: Any,
) -> dict[str, Any]:
    """Test a match without executing actions or making a provider request."""
    try:
        if hasattr(rules, "async_match_with_skipped"):
            match, skipped = await rules.async_match_with_skipped(hass, text)
        else:
            match, skipped = await rules.async_match(hass, text), []
    except SentenceMatchLimitError as err:
        raise HomeAssistantError(str(err)) from err
    return {**request_rule_match_preview(match), "skipped_conditions": skipped}
