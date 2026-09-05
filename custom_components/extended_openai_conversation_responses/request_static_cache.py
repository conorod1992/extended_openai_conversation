"""Request-local schema reuse and safe static context projections."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
import json
from typing import Any

from .entity_context_cache import get_entity_prompt_metadata

_INSTALLED = False
_SKILLS_AVAILABLE: ContextVar[bool | None] = ContextVar(
    "extended_openai_skills_available", default=None
)
type _FormattedToolCacheEntry = tuple[
    tuple[dict[str, Any], ...],
    tuple[str, ...],
    tuple[dict[str, Any], ...],
]
_FORMATTED_TOOLS: ContextVar[
    dict[tuple[str, tuple[int, ...]], _FormattedToolCacheEntry] | None
] = ContextVar("extended_openai_formatted_tool_cache", default=None)
_CANONICAL_SKILL_LOADER_PATH = "{{extended_openai.skill_dir(name)}}/{{file}}"


def _is_canonical_skill_loader(tool: dict[str, Any]) -> bool:
    """Identify only the integration's built-in skill loader, never by name alone."""
    spec = tool.get("spec", {})
    function = tool.get("function", {})
    return bool(
        spec.get("name") == "load_skill"
        and function.get("type") == "read_file"
        and function.get("path") == _CANONICAL_SKILL_LOADER_PATH
    )


def tools_for_available_skills(
    configured_tools: list[dict[str, Any]], skills_available: bool | None
) -> list[dict[str, Any]]:
    """Hide the canonical no-op skill loader only when zero skills are usable."""
    if skills_available is not False:
        return configured_tools
    return [tool for tool in configured_tools if not _is_canonical_skill_loader(tool)]


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
    except (TypeError, ValueError):
        # A malformed/non-JSON schema is outside the normal model payload contract.
        # Do not cache it by identity; let the formatter surface its normal error.
        return None


def cached_format_tools(
    function_tools: list[dict[str, Any]],
    api_mode: str,
    formatter: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Reuse provider-formatted schemas only for the exact unchanged tool objects."""
    cache = _FORMATTED_TOOLS.get()
    if cache is None:
        return formatter(function_tools, api_mode)

    signatures = tuple(_tool_format_signature(tool) for tool in function_tools)
    if any(signature is None for signature in signatures):
        return formatter(function_tools, api_mode)
    stable_signatures = tuple(signature for signature in signatures if signature is not None)
    key = (api_mode, tuple(id(tool) for tool in function_tools))
    cached = cache.get(key)
    if cached is not None:
        original_tools, original_signatures, formatted = cached
        if (
            len(original_tools) == len(function_tools)
            and all(
                original is current
                for original, current in zip(original_tools, function_tools, strict=True)
            )
            and original_signatures == stable_signatures
        ):
            return list(formatted)

    formatted = tuple(formatter(function_tools, api_mode))
    # Keep strong references to the original containers. Their ids therefore cannot
    # be recycled into a false cache hit while this request-local entry exists.
    cache[key] = (tuple(function_tools), stable_signatures, formatted)
    return list(formatted)


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


def install_request_static_caching() -> None:
    """Install request-scoped caching after existing performance/security wrappers."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import conversation, entity, prompt

    original_process = conversation.ExtendedOpenAIAgentEntity._async_process
    original_format_tools = entity._format_tools
    original_render_template = prompt._render_template
    original_get_function_tools = (
        conversation.ExtendedOpenAIAgentEntity._get_function_tools
    )
    original_load_function_groups_method = (
        conversation.ExtendedOpenAIAgentEntity._load_function_groups
    )
    original_assemble_function_tools = conversation.assemble_function_tools
    original_load_function_groups = conversation.load_function_groups

    @wraps(original_process)
    async def process_with_fresh_formatted_tool_cache(
        self: Any, user_input: Any
    ) -> Any:
        token = _FORMATTED_TOOLS.set({})
        try:
            return await original_process(self, user_input)
        finally:
            _FORMATTED_TOOLS.reset(token)

    def format_tools_cached(
        function_tools: list[dict[str, Any]], api_mode: str
    ) -> list[dict[str, Any]]:
        return cached_format_tools(function_tools, api_mode, original_format_tools)

    def render_template_fast(
        hass: Any,
        raw: str,
        *,
        exposed_entities: list[dict[str, Any]],
        current_device_id: str | None,
        user_input: Any,
        skills: list[Any],
    ) -> str:
        maintained_default = getattr(prompt, "_DEFAULT_EXPOSED_ENTITIES_CONTEXT", None)
        if maintained_default is not None and raw == maintained_default:
            return render_maintained_entity_context(hass, exposed_entities)
        return original_render_template(
            hass,
            raw,
            exposed_entities=exposed_entities,
            current_device_id=current_device_id,
            user_input=user_input,
            skills=skills,
        )

    @wraps(original_get_function_tools)
    def get_function_tools_for_skills(self: Any) -> list[dict[str, Any]]:
        token = _SKILLS_AVAILABLE.set(bool(self._get_enabled_skills()))
        try:
            return original_get_function_tools(self)
        finally:
            _SKILLS_AVAILABLE.reset(token)

    @wraps(original_load_function_groups_method)
    def load_function_groups_for_skills(self: Any, requested: Any) -> dict[str, Any]:
        token = _SKILLS_AVAILABLE.set(bool(self._get_enabled_skills()))
        try:
            return original_load_function_groups_method(self, requested)
        finally:
            _SKILLS_AVAILABLE.reset(token)

    def assemble_function_tools_for_skills(
        configured_tools: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        loaded_group_ids: set[str],
    ) -> Any:
        return original_assemble_function_tools(
            tools_for_available_skills(configured_tools, _SKILLS_AVAILABLE.get()),
            groups,
            loaded_group_ids,
        )

    def load_function_groups_projected(
        session: Any,
        requested: Any,
        groups: list[dict[str, Any]],
        configured_tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        projected = (
            None
            if configured_tools is None
            else tools_for_available_skills(configured_tools, _SKILLS_AVAILABLE.get())
        )
        return original_load_function_groups(session, requested, groups, projected)

    conversation.ExtendedOpenAIAgentEntity._async_process = (  # type: ignore[method-assign]
        process_with_fresh_formatted_tool_cache
    )
    entity._format_tools = format_tools_cached
    prompt._render_template = render_template_fast  # type: ignore[attr-defined]
    conversation.ExtendedOpenAIAgentEntity._get_function_tools = (  # type: ignore[method-assign]
        get_function_tools_for_skills
    )
    conversation.ExtendedOpenAIAgentEntity._load_function_groups = (  # type: ignore[method-assign]
        load_function_groups_for_skills
    )
    conversation.assemble_function_tools = assemble_function_tools_for_skills
    conversation.load_function_groups = load_function_groups_projected
