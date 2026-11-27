"""Bind Skill and Function Group availability to effective runtime assembly."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .agent_config import normalized_function_call_budget
from .const import CONF_FUNCTION_GROUPS, CONF_SKILLS, DEFAULT_FUNCTION_GROUPS
from .function_groups import function_tool_runtime_scope
from .function_tool_quarantine import (
    _runtime_validate_function_groups as validate_function_groups,
)
from .skill_availability import effective_skill_loader_status, is_canonical_skill_loader
from .skills import Skill, SkillManager

_ACTIVE_SELECTED_SKILLS: ContextVar[frozenset[str] | None] = ContextVar(
    "eoai_selected_skills", default=None
)


def require_selected_skill(name: str) -> None:
    """Authorize helper lookups against the active agent's selected Skills."""
    selected = _ACTIVE_SELECTED_SKILLS.get()
    if selected is not None and name not in selected:
        raise ValueError("Skill is not selected for this agent")


@contextmanager
def selected_skill_scope(options: Mapping[str, Any]) -> Iterator[None]:
    """Bind selection during execution, independently of tool advertisement."""
    token = _ACTIVE_SELECTED_SKILLS.set(frozenset(options.get(CONF_SKILLS, []) or []))
    try:
        yield
    finally:
        _ACTIVE_SELECTED_SKILLS.reset(token)


@contextmanager
def effective_tool_runtime_scope(
    options: Mapping[str, Any],
    configured_tools: list[dict[str, Any]],
    manager: SkillManager | None,
    groups: list[dict[str, Any]] | None = None,
) -> Iterator[list[Skill]]:
    """Resolve one coherent configured-tool availability snapshot."""
    if groups is None:
        groups = validate_function_groups(
            options.get(CONF_FUNCTION_GROUPS, list(DEFAULT_FUNCTION_GROUPS)),
            configured_tools,
        )
    selected = options.get(CONF_SKILLS, []) or []
    selected_names = [name for name in selected if isinstance(name, str)]
    installed = (
        manager.get_all_skills() if manager is not None and selected_names else []
    )
    max_function_calls = normalized_function_call_budget(options, allow_unlimited=True)
    function_tools_supported = max_function_calls != 0
    group_loader_supported = function_tools_supported
    skill_status = effective_skill_loader_status(
        selected_names,
        (skill.name for skill in installed),
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
        token = _ACTIVE_SELECTED_SKILLS.set(frozenset(selected_names))
        try:
            yield (
                [
                    skill
                    for skill in installed
                    if skill.name in skill_status.loadable_skills
                ]
                if skill_status.available
                else []
            )
        finally:
            _ACTIVE_SELECTED_SKILLS.reset(token)
