"""Shared availability rules for configured Skills and their loader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import FUNCTION_GROUP_LOADING_ON_DEMAND

SKILL_LOADER_TOOL_NAME = "load_skill"
CANONICAL_SKILL_LOADER_PATH = "{{extended_openai.skill_dir(name)}}/{{file}}"


@dataclass(frozen=True, slots=True)
class SkillLoaderStatus:
    """Describe whether selected Skills can be loaded by the model."""

    available: bool
    reason: str | None = None
    group_id: str | None = None
    on_demand: bool = False


def is_canonical_skill_loader(tool: dict[str, Any]) -> bool:
    """Identify the maintained loader without trusting its name alone."""
    spec = tool.get("spec", {})
    function = tool.get("function", {})
    return bool(
        spec.get("name") == SKILL_LOADER_TOOL_NAME
        and function.get("type") == "read_file"
        and function.get("path") == CANONICAL_SKILL_LOADER_PATH
    )


def function_group_enabled(group: dict[str, Any]) -> bool:
    """Treat legacy groups as enabled without rewriting member tool state."""
    return group.get("enabled", True) is True


def function_group_for_tool(
    tool_name: str, groups: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the configured group containing a tool, if any."""
    return next(
        (
            group
            for group in groups
            if tool_name in group.get("functions", [])
        ),
        None,
    )


def skill_loader_status(
    selected_skills: list[str],
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    max_function_calls: int | None = None,
) -> SkillLoaderStatus:
    """Return the structural/model availability of the maintained Skill loader."""
    if not selected_skills:
        return SkillLoaderStatus(available=True)
    if max_function_calls is not None and max_function_calls <= 0:
        return SkillLoaderStatus(
            available=False,
            reason="selected Skills require at least one Function Tool call per request",
        )

    loader = next((tool for tool in tools if is_canonical_skill_loader(tool)), None)
    if loader is None:
        return SkillLoaderStatus(
            available=False,
            reason="selected Skills require the built-in `load_skill` Function Tool",
        )
    if loader.get("enabled", True) is not True:
        return SkillLoaderStatus(
            available=False,
            reason="the built-in `load_skill` Function Tool is disabled",
        )

    group = function_group_for_tool(SKILL_LOADER_TOOL_NAME, groups)
    if group is None:
        return SkillLoaderStatus(available=True)
    group_id = str(group.get("id", ""))
    if not function_group_enabled(group):
        return SkillLoaderStatus(
            available=False,
            reason=(
                "the Function Group containing `load_skill` is disabled"
                + (f" (`{group_id}`)" if group_id else "")
            ),
            group_id=group_id or None,
        )

    # Function Groups are flat: the integration-owned `load_function_groups` tool is
    # generated independently of configured groups, so an enabled on-demand group
    # containing `load_skill` remains reachable without a circular dependency.
    return SkillLoaderStatus(
        available=True,
        group_id=group_id or None,
        on_demand=group.get("loading_mode") == FUNCTION_GROUP_LOADING_ON_DEMAND,
    )
