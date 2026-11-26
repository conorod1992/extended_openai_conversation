"""Shared availability rules for configured Skills and their loader."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from time import monotonic
from typing import Any

import regex

from .const import FUNCTION_GROUP_LOADING_ON_DEMAND, SKILL_FILE_NAME

SKILL_LOADER_TOOL_NAME = "load_skill"
CANONICAL_SKILL_LOADER_PATH = "{{extended_openai.skill_dir(name)}}/{{file}}"
_LITERAL_PROPERTY_KEY = re.compile(r"[a-zA-Z0-9_-]+")
_PROPERTY_END_ASSERTION = r"(?![\s\S])"


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
        and (set(before.loadable_skills) & set(selected_after)).issubset(
            after.loadable_skills
        )
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


def _allowed_string_arguments(
    schema: dict[str, Any], values: tuple[str, ...]
) -> tuple[str, ...]:
    minimum, maximum = schema.get("minLength"), schema.get("maxLength")
    choices = schema.get("enum")
    allowed = tuple(
        value
        for value in values
        if isinstance(value, str)
        and (type(minimum) is not int or len(value) >= minimum)
        and (type(maximum) is not int or len(value) <= maximum)
        and ("const" not in schema or value == schema["const"])
        and (not isinstance(choices, list) or value in choices)
    )
    if (pattern := schema.get("pattern")) is None:
        return allowed
    if not allowed or not isinstance(pattern, str):
        return ()
    try:
        compiled = regex.compile(pattern, regex.VERSION0)
    except regex.error:
        return ()
    # Bound matching in synchronous HA projections. Start the budget after
    # compilation so an empty cache cannot consume time for later names.
    deadline = monotonic() + 0.01
    matches = []
    for value in allowed:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            if compiled.search(value, timeout=remaining) is not None:
                matches.append(value)
        except TimeoutError:
            break
    return tuple(matches)


def _closed_property_count(properties: dict[str, Any], patterns: Any) -> int | None:
    """Count possible keys when all patterns are anchored literal alternatives."""
    if not isinstance(patterns, dict):
        return None
    keys = set(properties)
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.startswith("^"):
            return None
        final_newline = False
        body = pattern[1:]
        if body.endswith(_PROPERTY_END_ASSERTION):
            body = body.removesuffix(_PROPERTY_END_ASSERTION).removesuffix("$")
        elif body.endswith((r"\Z", r"\z")):
            body = body[:-2]
        elif body.endswith("$"):
            body = body[:-1]
            # Python's runtime validator also accepts a terminal newline.
            final_newline = True
        else:
            return None
        alternatives = (
            body[3:-1].split("|")
            if body.startswith("(?:") and body.endswith(")")
            else [body]
        )
        if not all(_LITERAL_PROPERTY_KEY.fullmatch(key) for key in alternatives):
            return None
        keys.update(alternatives)
        if final_newline:
            keys.update(f"{key}\n" for key in alternatives)
    return len(keys)


def _loader_allowed_names(
    tool: dict[str, Any], selected: tuple[str, ...]
) -> tuple[str, ...] | None:
    """Return names the schema allows, or None for an unusable loader shape."""
    parameters = tool.get("spec", {}).get("parameters", {})
    if not isinstance(parameters, dict):
        return None
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict):
        return None
    required = parameters.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(key, str) for key in required
    ):
        return None
    maximum = parameters.get("maxProperties")
    if type(maximum) is int and maximum < len({"name", "file", *required}):
        return None
    minimum = parameters.get("minProperties")
    if (
        type(minimum) is int
        and minimum > len(properties)
        and parameters.get("additionalProperties") is False
    ):
        possible = _closed_property_count(
            properties, parameters.get("patternProperties", {})
        )
        if possible is not None and minimum > possible:
            return None
    for key in ("file", "name"):
        schema = properties.get(key)
        if key not in properties:
            additional = parameters.get("additionalProperties")
            if additional is False:
                return None
            if not parameters.get("patternProperties"):
                schema = additional
        if schema is False:
            return None
        if not isinstance(schema, dict):
            continue
        kind = schema.get("type")
        if (
            kind is not None
            and kind != "string"
            and not (isinstance(kind, list) and "string" in kind)
        ):
            return None
        if key == "name":
            maximum = schema.get("maxLength")
            if type(maximum) is int and maximum == 0:
                return None
            selected = _allowed_string_arguments(schema, selected)
        elif not _allowed_string_arguments(schema, (SKILL_FILE_NAME,)):
            return None
    return selected


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
    return skill_loader_configuration_status(
        tools,
        groups,
        max_function_calls=max_function_calls,
        selected_skills=tuple(selected_skills),
    )


def skill_loader_configuration_status(
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    max_function_calls: int | None = None,
    selected_skills: tuple[str, ...] = (),
    require_all_selected: bool = True,
) -> SkillLoaderStatus:
    """Check whether the configured loader can serve a Skill selection."""
    # Track all structural blockers so an edit cannot hide a new problem behind
    # the first diagnostic, and a partial repair can safely remove one blocker.
    blockers: list[str] = []
    if max_function_calls is not None and max_function_calls <= 0:
        blockers.append(
            "selected Skills require at least one Function Tool call per request"
        )

    loader = next((tool for tool in tools if is_canonical_skill_loader(tool)), None)
    allowed: tuple[str, ...] | None = None
    if loader is None:
        blockers.append(
            "selected Skills require the built-in `load_skill` Function Tool"
        )
    else:
        if loader.get("enabled", True) is not True:
            blockers.append("the built-in `load_skill` Function Tool is disabled")
        allowed = _loader_allowed_names(loader, selected_skills)
        if allowed is None:
            blockers.append(
                "the `load_skill` schema must allow string name and file arguments"
            )
        elif selected_skills and (
            not allowed or (require_all_selected and allowed != selected_skills)
        ):
            blockers.append(
                "the `load_skill` schema excludes one or more selected Skill names"
            )

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
            loadable_skills=allowed or (),
            blockers=tuple(blockers),
        )

    return SkillLoaderStatus(
        available=True,
        group_id=group_id or None,
        loadable_skills=allowed or (),
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

    status = skill_loader_configuration_status(
        tools,
        groups,
        max_function_calls=max_function_calls,
        selected_skills=usable,
        require_all_selected=False,
    )
    if not status.available:
        return status
    if status.on_demand and not group_loader_supported:
        return SkillLoaderStatus(
            available=False,
            reason="the Function Group loader is unavailable for this runtime",
            group_id=status.group_id,
            on_demand=True,
            loadable_skills=status.loadable_skills,
        )
    return status
