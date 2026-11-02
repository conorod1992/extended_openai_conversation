"""Shared availability rules for configured Skills and their loader."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
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
    loadable_skills: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


def preserves_skill_loader_availability(
    before: SkillLoaderStatus,
    after: SkillLoaderStatus,
    selected_before: Iterable[str],
    selected_after: Iterable[str],
) -> bool:
    """Allow repairs/other edits to retain an existing inactive selection.

    Never break a working loader, add newly unusable Skills, or introduce a
    different dependency failure. Only explicit edits may enable capabilities.
    """
    return after.available or (
        not before.available
        and set(after.blockers or (after.reason,)).issubset(
            before.blockers or (before.reason,)
        )
        and set(selected_after).issubset(selected_before)
    )


def is_canonical_skill_loader(tool: dict[str, Any]) -> bool:
    """Identify the maintained loader without trusting its name alone."""
    spec = tool.get("spec", {})
    function = tool.get("function", {})
    return bool(
        spec.get("name") == SKILL_LOADER_TOOL_NAME
        and function.get("type") == "read_file"
        and getattr(function.get("path"), "template", function.get("path"))
        == CANONICAL_SKILL_LOADER_PATH
    )


def function_group_enabled(group: dict[str, Any]) -> bool:
    """Treat legacy groups as enabled without rewriting member tool state."""
    return group.get("enabled", True) is True


def function_group_for_tool(
    tool_name: str, groups: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the configured group containing a tool, if any."""
    return next(
        (group for group in groups if tool_name in group.get("functions", [])),
        None,
    )


def selected_installed_skill_names(
    selected_skills: Iterable[str], installed_skills: Iterable[str]
) -> tuple[str, ...]:
    """Return selected Skills that exist in the published installed catalogue.

    This filesystem/configuration primitive deliberately does not consult either
    loader. Loader exposure and Skill loadability can therefore both be derived from
    it without a circular dependency.
    """
    installed = set(installed_skills)
    return tuple(dict.fromkeys(name for name in selected_skills if name in installed))


def skill_loader_status(
    selected_skills: list[str] | tuple[str, ...],
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    max_function_calls: int | None = None,
) -> SkillLoaderStatus:
    """Return structural availability of the maintained Skill loader.

    Persisted configuration validation intentionally does not inspect the filesystem.
    A selected Skill can remain configured while temporarily absent; live loadability
    is resolved separately by :func:`effective_skill_loader_status`.
    """
    if not selected_skills:
        return SkillLoaderStatus(available=True)
    # Track all structural blockers so an edit cannot hide a new problem behind
    # the first diagnostic, and a partial repair can safely remove one blocker.
    blockers: list[str] = []
    if max_function_calls is not None and max_function_calls <= 0:
        blockers.append(
            "selected Skills require at least one Function Tool call per request"
        )

    loader = next((tool for tool in tools if is_canonical_skill_loader(tool)), None)
    if loader is None:
        blockers.append(
            "selected Skills require the built-in `load_skill` Function Tool"
        )
    elif loader.get("enabled", True) is not True:
        blockers.append("the built-in `load_skill` Function Tool is disabled")

    group = function_group_for_tool(SKILL_LOADER_TOOL_NAME, groups) if loader else None
    group_id = str(group.get("id", "")) if group else None
    if group is not None and not function_group_enabled(group):
        blockers.append(
            "the Function Group containing `load_skill` is disabled"
            + (f" (`{group_id}`)" if group_id else "")
        )
    if blockers:
        return SkillLoaderStatus(
            available=False,
            reason=blockers[0],
            group_id=group_id or None,
            blockers=tuple(blockers),
        )

    return SkillLoaderStatus(
        available=True,
        group_id=group_id or None,
        on_demand=group is not None
        and group.get("loading_mode") == FUNCTION_GROUP_LOADING_ON_DEMAND,
    )


def effective_skill_loader_status(
    selected_skills: Iterable[str],
    installed_skills: Iterable[str],
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    max_function_calls: int | None = None,
    function_tools_supported: bool = True,
    group_loader_supported: bool = True,
) -> SkillLoaderStatus:
    """Resolve whether any selected installed Skill is actually model-loadable."""
    usable = selected_installed_skill_names(selected_skills, installed_skills)
    if not usable:
        return SkillLoaderStatus(
            available=False,
            reason="no selected installed Skills are currently available",
        )
    if not function_tools_supported:
        return SkillLoaderStatus(
            available=False,
            reason="Function Tools are unavailable for this runtime",
            loadable_skills=usable,
        )

    status = skill_loader_status(
        usable,
        tools,
        groups,
        max_function_calls=max_function_calls,
    )
    if not status.available:
        return status
    if status.on_demand and not group_loader_supported:
        return SkillLoaderStatus(
            available=False,
            reason="the Function Group loader is unavailable for this runtime",
            group_id=status.group_id,
            on_demand=True,
            loadable_skills=usable,
        )
    return replace(status, loadable_skills=usable)
