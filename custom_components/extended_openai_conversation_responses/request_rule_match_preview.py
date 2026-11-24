"""Side-effect-free management preview for Request Rule matching."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import is_template_string

from . import request_rules
from .request_rule_patterns import SentenceMatchLimitError
from .request_rules import RuleMatch, rule_provider_input
from .script_payload import effective_service_payload, is_function_service_action
from .script_structure import iter_script_actions


def _local_continuation(actions: list[dict[str, Any]]) -> str:
    """Prove only flat Script termination; never render or walk branch outcomes.

    An earlier failure also prevents continuation, so an unconditional stop or
    final literal response proves termination even after opaque actions. Other
    actions may fail, stop within a nested scope, or change/clear the response.
    """
    response: bool | None = False  # False: cleared, True: non-null, None: unknown.
    uncertain = False
    for step in actions:
        enabled = step.get("enabled", True)
        if enabled is False:
            continue
        if enabled is not True:
            uncertain, response = True, None
        elif "stop" in step:
            # HA treats the stop message as literal text, even with Jinja syntax.
            return "terminal"
        elif "set_conversation_response" in step:
            value = step["set_conversation_response"]
            if value is None:
                response = False
            elif isinstance(value, str) and not is_template_string(value):
                response = True  # Empty text is non-null too.
            else:
                uncertain, response = True, None
        else:
            uncertain, response = True, None
    if response is True:
        return "terminal"
    return "conditional" if uncertain or response is None else "continue"


def _preview_ai_input(match: RuleMatch, original: str) -> dict[str, Any]:
    """Describe a handoff without invoking the provider."""
    capture = match.rule.get("ai_input_capture")
    return {
        "mode": match.rule.get("ai_input_mode", "original"),
        "capture": capture,
        "provider_input": rule_provider_input(match, original) or original,
    }


def request_rule_match_preview(match: RuleMatch | None) -> dict[str, Any]:
    """Describe a match and conservative continuation without executing actions."""
    if match is None:
        return {"matched": False}

    rule = match.rule
    action = rule["action"]
    would_do: dict[str, Any]
    if rule.get(request_rules.MATCH_REPAIR):
        continue_to_ai = False
        would_do = {
            "type": rule["action_type"],
            "action_count": 0,
            "consumed": True,
            "provider_input": "none",
            "terminal": True,
            "needs_repair": True,
        }
    elif rule["action_type"] == "local_action":
        continue_to_ai = bool(action.get("continue_to_ai", False))
        continuation = _local_continuation(action.get("actions", []))
        terminal = continuation == "terminal"
        conditional = continuation == "conditional" and continue_to_ai
        functions = [
            {"name": data["function"]}
            for step in iter_script_actions(
                action.get("actions", []),
                max_depth=request_rules.MAX_SCRIPT_DEPTH,
                max_nodes=request_rules.MAX_SCRIPT_NODES,
            )
            if isinstance(data := effective_service_payload(step), dict)
            and is_function_service_action(step)
            and isinstance(data.get("function"), str)
        ]
        would_do = {
            "type": "local_action",
            "action_count": len(action.get("actions", [])),
            "consumed": None
            if conditional and continue_to_ai
            else not continue_to_ai or terminal,
            "provider_input": ("conditional" if conditional else "original")
            if continue_to_ai and not terminal
            else "none",
            **({"terminal": True} if terminal else {}),
            **({"conditional": True} if conditional else {}),
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
            **(
                {"azure_model": action["azure_model"]}
                if action.get("azure_model")
                else {}
            ),
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
        **({"conditional": True} if would_do.get("conditional") else {}),
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
    summary = request_rule_match_preview(match)
    items = []
    if match is not None:
        action = summary["would_do"]
        ai_input = (
            _preview_ai_input(match, text)
            if action["provider_input"] != "none"
            else None
        )
        if ai_input is not None and action.get("conditional"):
            ai_input["conditional"] = True
        items.append(
            {
                "rule": {"id": match.rule["id"], "name": match.rule["name"]},
                "status": (
                    "conditional"
                    if action.get("conditional")
                    else "would_send_to_ai"
                    if ai_input is not None
                    else "stopped"
                ),
                "would_do": action,
                "ai_input": ai_input,
            }
        )
    return {**summary, "skipped_conditions": skipped, "matched_rules": items}
