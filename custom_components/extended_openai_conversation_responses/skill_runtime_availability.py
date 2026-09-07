"""Bind Skill and Function Group availability to effective runtime assembly."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import wraps
from typing import Any

from .agent_config import validate_function_groups
from .const import (
    CONF_FUNCTION_GROUPS,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_SKILLS,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
)
from .function_groups import function_tool_runtime_scope
from .skill_availability import (
    effective_skill_loader_status,
    is_canonical_skill_loader,
)
from .skills import SkillManager

_INSTALLED = False


def _installed_skill_names(manager: SkillManager | None) -> tuple[str, ...]:
    if manager is None:
        return ()
    return tuple(skill.name for skill in manager.get_all_skills())


@contextmanager
def effective_tool_runtime_scope(
    options: Mapping[str, Any],
    configured_tools: list[dict[str, Any]],
    manager: SkillManager | None,
) -> Iterator[None]:
    """Resolve one coherent configured-tool availability snapshot."""
    groups = validate_function_groups(
        options.get(CONF_FUNCTION_GROUPS, list(DEFAULT_FUNCTION_GROUPS)),
        configured_tools,
    )
    selected = options.get(CONF_SKILLS, []) or []
    selected_names = [name for name in selected if isinstance(name, str)]
    max_function_calls = int(
        options.get(
            CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
            DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
        )
    )
    function_tools_supported = max_function_calls > 0
    group_loader_supported = function_tools_supported
    skill_status = effective_skill_loader_status(
        selected_names,
        _installed_skill_names(manager),
        configured_tools,
        groups,
        max_function_calls=max_function_calls,
        function_tools_supported=function_tools_supported,
        group_loader_supported=group_loader_supported,
    )

    def tool_available(tool: dict[str, Any]) -> bool:
        return not is_canonical_skill_loader(tool) or skill_status.available

    with function_tool_runtime_scope(
        function_tools_supported=function_tools_supported,
        group_loader_supported=group_loader_supported,
        tool_available=tool_available,
    ):
        yield


def install_skill_runtime_availability() -> None:
    """Install idempotent scopes around live and Preview tool assembly."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import management_ui
    from .conversation import ExtendedOpenAIAgentEntity

    current_tools = ExtendedOpenAIAgentEntity._get_function_tools
    if not getattr(current_tools, "_extended_openai_skill_availability", False):
        original_tools = current_tools

        @wraps(original_tools)
        def get_function_tools(entity: Any) -> list[dict[str, Any]]:
            configured = entity._get_configured_function_tools()
            manager = getattr(entity, "skill_manager", None)
            if not isinstance(manager, SkillManager):
                manager = SkillManager.get_loaded_instance()
            with effective_tool_runtime_scope(entity.subentry.data, configured, manager):
                return original_tools(entity)

        get_function_tools._extended_openai_skill_availability = True  # type: ignore[attr-defined]
        ExtendedOpenAIAgentEntity._get_function_tools = get_function_tools  # type: ignore[method-assign,assignment]

    current_loader = ExtendedOpenAIAgentEntity._load_function_groups
    if not getattr(current_loader, "_extended_openai_skill_availability", False):
        original_loader = current_loader

        @wraps(original_loader)
        def load_function_groups(entity: Any, requested: Any) -> dict[str, Any]:
            configured = entity._get_configured_function_tools()
            manager = getattr(entity, "skill_manager", None)
            if not isinstance(manager, SkillManager):
                manager = SkillManager.get_loaded_instance()
            with effective_tool_runtime_scope(entity.subentry.data, configured, manager):
                return original_loader(entity, requested)

        load_function_groups._extended_openai_skill_availability = True  # type: ignore[attr-defined]
        ExtendedOpenAIAgentEntity._load_function_groups = load_function_groups  # type: ignore[method-assign,assignment]

    current_preview = management_ui._async_preview_effective_request
    if not getattr(current_preview, "_extended_openai_skill_availability", False):
        original_preview = current_preview

        @wraps(original_preview)
        async def preview_effective_request(
            hass: Any,
            entry: Any,
            subentry: Any,
            options: dict[str, Any],
            user_id: str,
        ) -> dict[str, Any]:
            configured = management_ui.configured_function_tools_from_data(options)
            manager = SkillManager.get_loaded_instance()
            with effective_tool_runtime_scope(options, configured, manager):
                return await original_preview(hass, entry, subentry, options, user_id)

        preview_effective_request._extended_openai_skill_availability = True  # type: ignore[attr-defined]
        management_ui._async_preview_effective_request = preview_effective_request

    _INSTALLED = True
