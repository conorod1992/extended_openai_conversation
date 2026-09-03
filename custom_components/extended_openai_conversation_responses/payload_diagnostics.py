"""Metadata-only payload sizing helpers for opt-in request debugging."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from math import ceil
import json
from typing import Any

from .prompt import EffectivePrompt, PromptSection

APPROX_TOKEN_METHOD = "characters_div_4"


def _json_characters(value: Any) -> int:
    """Return deterministic compact JSON characters for already JSON-safe data."""
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def approximate_tokens(characters: int) -> int:
    """Return a deliberately labelled rough character-based token estimate."""
    return ceil(max(0, characters) / 4)


def _sized(characters: int) -> dict[str, Any]:
    return {
        "characters": characters,
        "approx_tokens": approximate_tokens(characters),
    }


def prompt_metrics(prompt: EffectivePrompt) -> dict[str, Any]:
    """Describe exact prompt sections without retaining any additional prompt text."""
    sections = [
        {
            "key": section.key,
            "label": section.label,
            "volatility": section.volatility,
            **_sized(len(section.text)),
        }
        for section in prompt.sections
    ]
    by_volatility: dict[str, int] = defaultdict(int)
    for section in prompt.sections:
        by_volatility[section.volatility] += len(section.text)

    leading_stable: list[PromptSection] = []
    for section in prompt.sections:
        if section.volatility != "stable":
            break
        leading_stable.append(section)
    stable_prefix = ""
    if leading_stable:
        stable_prefix = leading_stable[0].text
        for section in leading_stable[1:]:
            stable_prefix = f"{stable_prefix.rstrip()}\n{section.text}"

    largest = sorted(sections, key=lambda item: item["characters"], reverse=True)
    return {
        **_sized(len(prompt.text)),
        "approximation_method": APPROX_TOKEN_METHOD,
        "sections": sections,
        "largest_sections": largest,
        "section_characters_by_volatility": dict(by_volatility),
        "integration_stable_prefix": {
            "section_count": len(leading_stable),
            **_sized(len(stable_prefix)),
            "first_non_stable_section": (
                prompt.sections[len(leading_stable)].key
                if len(leading_stable) < len(prompt.sections)
                else None
            ),
        },
    }


def _input_kind(item: Any) -> str:
    if not isinstance(item, dict):
        return "other"
    role = item.get("role")
    if role in {"system", "developer", "user", "assistant", "tool"}:
        return "tool_result" if role == "tool" else str(role)
    item_type = str(item.get("type", ""))
    if item_type in {"function_call_output", "tool_result"}:
        return "tool_result"
    if item_type == "function_call":
        return "function_call"
    if item_type == "reasoning":
        return "reasoning"
    if "web_search" in item_type:
        return "web_search"
    return item_type or "other"


def input_breakdown(input_value: Any) -> dict[str, Any]:
    """Aggregate provider input by message/item type without copying its content."""
    items = input_value if isinstance(input_value, list) else [input_value]
    totals: dict[str, dict[str, int]] = {}
    tool_result_characters = 0
    for item in items:
        kind = _input_kind(item)
        characters = _json_characters(item)
        bucket = totals.setdefault(kind, {"count": 0, "characters": 0})
        bucket["count"] += 1
        bucket["characters"] += characters
        if kind == "tool_result":
            tool_result_characters += characters
    for bucket in totals.values():
        bucket["approx_tokens"] = approximate_tokens(bucket["characters"])
    return {
        "items": len(items),
        "by_kind": totals,
        "tool_result_characters": tool_result_characters,
        "tool_result_approx_tokens": approximate_tokens(tool_result_characters),
    }


def explicit_cache_breakdown(input_value: Any) -> dict[str, Any]:
    """Measure explicit provider cache blocks that are actually present in input."""
    items = input_value if isinstance(input_value, list) else []
    breakpoint_count = 0
    prefix_characters = 0
    for item in items:
        if not isinstance(item, dict) or item.get("role") not in {
            "system",
            "developer",
        }:
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or "prompt_cache_breakpoint" not in block:
                continue
            breakpoint_count += 1
            text = block.get("text")
            if isinstance(text, str):
                prefix_characters += len(text)
    return {
        "breakpoint_count": breakpoint_count,
        "cacheable_prefix_characters": prefix_characters,
        "cacheable_prefix_approx_tokens": approximate_tokens(prefix_characters),
    }


def _tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return "<unknown>"
    name = tool.get("name")
    if isinstance(name, str) and name:
        return name
    function = tool.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            return name
    tool_type = tool.get("type")
    return str(tool_type or "<unknown>")


def tool_breakdown(tools: Any) -> list[dict[str, Any]]:
    """Return per-tool schema sizes, sorted largest first, without schema contents."""
    if not isinstance(tools, list):
        return []
    result = [
        {
            "name": _tool_name(tool),
            "type": str(tool.get("type", "unknown")) if isinstance(tool, dict) else "unknown",
            **_sized(_json_characters(tool)),
        }
        for tool in tools
    ]
    return sorted(result, key=lambda item: item["characters"], reverse=True)


def provider_payload_metrics(input_value: Any, tools: Any) -> dict[str, Any]:
    """Build metadata-only provider input/tool sizing diagnostics."""
    input_characters = _json_characters(input_value)
    tool_characters = _json_characters(tools)
    return {
        "approximation_method": APPROX_TOKEN_METHOD,
        "approx_input_tokens": approximate_tokens(input_characters),
        "approx_tool_tokens": approximate_tokens(tool_characters),
        "input_breakdown": input_breakdown(input_value),
        "explicit_prompt_cache": explicit_cache_breakdown(input_value),
        "tool_breakdown": tool_breakdown(tools),
    }


def cache_usage_metrics(usage: dict[str, int]) -> dict[str, Any]:
    """Describe provider-reported cache usage; never estimate cache hits locally."""
    input_tokens = max(0, int(usage.get("input_tokens", 0)))
    cached = max(0, int(usage.get("cached_input_tokens", 0)))
    return {
        "provider_reported_cached_input_tokens": cached,
        "provider_reported_cache_ratio": (
            round(cached / input_tokens, 4) if input_tokens else None
        ),
    }


def largest_contributors(
    *,
    prompt_sections: Iterable[dict[str, Any]] = (),
    tools: Iterable[dict[str, Any]] = (),
    input_kinds: dict[str, dict[str, int]] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Combine content-free size contributors for quick debugging triage."""
    contributors: list[dict[str, Any]] = []
    for item in prompt_sections:
        contributors.append(
            {
                "category": "system_prompt_section",
                "name": str(item.get("label") or item.get("key") or "unknown"),
                "characters": int(item.get("characters", 0)),
            }
        )
    for item in tools:
        contributors.append(
            {
                "category": "tool_schema",
                "name": str(item.get("name") or "unknown"),
                "characters": int(item.get("characters", 0)),
            }
        )
    for name, item in (input_kinds or {}).items():
        contributors.append(
            {
                "category": "provider_input",
                "name": name,
                "characters": int(item.get("characters", 0)),
            }
        )
    contributors.sort(key=lambda item: item["characters"], reverse=True)
    return [
        {**item, "approx_tokens": approximate_tokens(item["characters"])}
        for item in contributors[: max(1, limit)]
    ]
