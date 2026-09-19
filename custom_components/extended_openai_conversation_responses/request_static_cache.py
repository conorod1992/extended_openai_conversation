"""Request-local schema reuse and safe static context projections."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from typing import Any

from .entity_context_cache import get_entity_prompt_metadata
from .skill_availability import is_canonical_skill_loader

_FORMATTED_TOOLS: ContextVar[
    dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] | None
] = ContextVar("extended_openai_formatted_tool_cache", default=None)


def tools_for_available_skills(
    configured_tools: list[dict[str, Any]], skills_available: bool | None
) -> list[dict[str, Any]]:
    """Hide the canonical no-op skill loader only when zero skills are usable."""
    if skills_available is not False:
        return configured_tools
    return [tool for tool in configured_tools if not is_canonical_skill_loader(tool)]


def _tool_format_signature(tool: dict[str, Any]) -> str | None:
    """Return the model-facing fields that can affect provider schema formatting."""
    function = tool.get("function")
    relevant_function = (
        {
            key: function.get(key)
            for key in ("type", "operation", "name")
            if key in function
        }
        if isinstance(function, dict)
        else {}
    )
    try:
        return json.dumps(
            {"spec": tool.get("spec"), "function": relevant_function},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except TypeError, ValueError:
        # A malformed/non-JSON schema is outside the normal model payload contract.
        # Do not cache it by identity; let the formatter surface its normal error.
        return None


def cached_format_tools(
    function_tools: list[dict[str, Any]],
    api_mode: str,
    formatter: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Reuse equal model schemas within a request, with isolated returned values."""
    cache = _FORMATTED_TOOLS.get()
    if cache is None:
        return formatter(function_tools, api_mode)
    signatures = tuple(_tool_format_signature(tool) for tool in function_tools)
    if any(signature is None for signature in signatures):
        return formatter(function_tools, api_mode)
    key = (
        api_mode,
        tuple(signature for signature in signatures if signature is not None),
    )
    if key not in cache:
        cache[key] = deepcopy(formatter(function_tools, api_mode))
    return deepcopy(cache[key])


def render_maintained_entity_context(
    hass: Any, exposed_entities: list[dict[str, Any]]
) -> str:
    """Render the maintained compact entity table from live rows plus cached metadata."""
    lines = ["## Available Devices", "entity_id,name,state,area_id,aliases"]
    for entity in exposed_entities:
        entity_id = str(entity.get("entity_id", ""))
        metadata = get_entity_prompt_metadata(hass, entity_id)
        aliases = entity.get("aliases") or []
        lines.append(
            f"{entity_id},{entity.get('prompt_name', entity.get('name', ''))},"
            f"{entity.get('state', '')},{metadata.area_id or ''},"
            f"{'/'.join(str(item) for item in aliases)}"
        )
    return "\n".join(lines) + "\n"


@contextmanager
def formatted_tool_cache() -> Iterator[None]:
    """Give one conversation request a fresh cache without disturbing its caller."""
    token = _FORMATTED_TOOLS.set({})
    try:
        yield
    finally:
        _FORMATTED_TOOLS.reset(token)
