"""Side-effect-free management preview for Request Rule matching."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import is_template_string

from . import request_rules
from .request_rule_patterns import SentenceMatchLimitError
from .request_rules import RuleMatch, rule_provider_input, rule_stops_matching
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
    if rule.get(request_rules.RESULT_ALIAS_REPAIR):
        continue_to_ai = False
        would_do = {
            "type": "local_action",
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
        conditional = continuation == "conditional" and (
            continue_to_ai or rule.get("continue_matching", False)
        )
        functions = [
            {
                "name": data["function"],
                "result_alias": data.get("result_alias"),
            }
            for step in iter_script_actions(
                action.get("actions", []),
                max_depth=request_rules.MAX_SCRIPT_DEPTH,
                max_nodes=request_rules.MAX_SCRIPT_NODES,
            )
            if isinstance(data := effective_service_payload(step), dict)
            and is_function_service_action(step)
            and isinstance(data.get("function"), str)
        ]
        would_do: dict[str, Any] = {
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

    if (
        rule.get("continue_matching")
        and not continue_to_ai
        and not would_do.get("terminal")
    ):
        would_do["continue_matching"] = True

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


def _preview_chain_step(
    match: RuleMatch, text: str, conditional: bool = False
) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    """Keep the selected summary, chain and potential AI input in agreement."""
    summary = request_rule_match_preview(match)
    action = summary["would_do"]
    conditional = conditional or bool(action.get("conditional"))
    ai_input = (
        _preview_ai_input(match, text) if action["provider_input"] != "none" else None
    )
    if conditional:
        summary["conditional"] = action["conditional"] = True
        if ai_input is not None:
            ai_input["conditional"] = True
            action.update(consumed=None, provider_input="conditional")
    stopped = (
        bool(action.get("terminal"))
        or ai_input is not None
        or rule_stops_matching(match.rule)
    )
    return (
        summary,
        {
            "rule": {"id": match.rule["id"], "name": match.rule["name"]},
            "status": "conditional"
            if conditional
            else (
                "would_send_to_ai"
                if ai_input is not None
                else "stopped"
                if stopped
                else "continued"
            ),
            "would_do": action,
            "ai_input": ai_input,
        },
        stopped,
        conditional,
    )


async def async_request_rule_match_preview(
    hass: HomeAssistant,
    rules: Any,
    text: Any,
) -> dict[str, Any]:
    """Test a match without executing actions or making a provider request."""
    try:
        if hasattr(rules, "async_eligible_matches") and rules._has_continuation:
            skipped: list[dict[str, str]] = []
            chain: list[dict[str, Any]] = []
            summary = request_rule_match_preview(None)
            conditional = False
            async for match in rules.async_eligible_matches(hass, text, skipped):
                summary, item, stopped, conditional = _preview_chain_step(
                    match, text, conditional
                )
                chain.append(item)
                if stopped:
                    break
            return {
                **summary,
                "matched_rules": chain,
                "skipped_conditions": skipped,
            }
        if hasattr(rules, "async_match_with_skipped"):
            match, skipped = await rules.async_match_with_skipped(hass, text)
        else:
            match, skipped = await rules.async_match(hass, text), []
    except SentenceMatchLimitError as err:
        raise HomeAssistantError(str(err)) from err
    if match is None:
        summary, chain = request_rule_match_preview(None), []
    else:
        summary, item, _, _ = _preview_chain_step(match, text)
        chain = [item]
    return {
        **summary,
        "skipped_conditions": skipped,
        "matched_rules": chain,
    }
