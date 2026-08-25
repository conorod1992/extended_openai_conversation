"""Conservative security classification for configured functions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import IntEnum
from typing import Any


class FunctionSecurity(IntEnum):
    """Increasingly difficult capability classes for Guest enforcement."""

    SAFE = 0
    CONTROL = 1
    INDIRECT = 2
    UNSCOPABLE = 3


_DIRECT_NATIVE = {"execute_service", "execute_service_single"}
_SCOPED_READ_NATIVE = {"get_history", "get_statistics"}
_INDIRECT_DOMAINS = {"script", "automation", "scene"}
_MAX_CLASSIFICATION_DEPTH = 12


def classify_tool(tool: Mapping[str, Any]) -> FunctionSecurity:
    """Classify one configured tool definition."""
    function = tool.get("function")
    if not isinstance(function, Mapping):
        return FunctionSecurity.UNSCOPABLE
    return classify_function(function)


def classify_function(
    function: Mapping[str, Any],
    *,
    _depth: int = 0,
    _seen: frozenset[int] = frozenset(),
) -> FunctionSecurity:
    """Recursively classify a function without interpreting HA scripts."""
    if _depth >= _MAX_CLASSIFICATION_DEPTH or id(function) in _seen:
        return FunctionSecurity.UNSCOPABLE
    seen = _seen | {id(function)}
    function_type = function.get("type")
    if function_type == "native":
        name = function.get("name")
        if name in _DIRECT_NATIVE:
            return FunctionSecurity.CONTROL
        if name in _SCOPED_READ_NATIVE:
            return FunctionSecurity.SAFE
        return FunctionSecurity.UNSCOPABLE
    if function_type == "script":
        return _classify_script(function.get("sequence"))
    if function_type == "composite":
        sequence = function.get("sequence")
        if not isinstance(sequence, Sequence) or isinstance(sequence, (str, bytes)):
            return FunctionSecurity.UNSCOPABLE
        level = FunctionSecurity.SAFE
        for nested in sequence:
            if not isinstance(nested, Mapping):
                return FunctionSecurity.UNSCOPABLE
            level = max(
                level,
                classify_function(nested, _depth=_depth + 1, _seen=seen),
            )
        return level
    # Templates, network, database, shell, and file functions can access data or
    # cause effects outside entity-scoped HA action validation.
    return FunctionSecurity.UNSCOPABLE


def _classify_script(sequence: Any) -> FunctionSecurity:
    if not isinstance(sequence, Sequence) or isinstance(sequence, (str, bytes)):
        return FunctionSecurity.UNSCOPABLE
    level = FunctionSecurity.SAFE
    for step in sequence:
        if not isinstance(step, Mapping):
            return FunctionSecurity.UNSCOPABLE
        service = step.get("service", step.get("action"))
        if not isinstance(service, str) or "{{" in service or "{%" in service:
            return FunctionSecurity.UNSCOPABLE
        domain, separator, service_name = service.partition(".")
        if not separator or not domain or not service_name:
            return FunctionSecurity.UNSCOPABLE
        target = step.get("target")
        if not isinstance(target, Mapping) or not target:
            return FunctionSecurity.UNSCOPABLE
        if not _static_target(target):
            return FunctionSecurity.UNSCOPABLE
        level = max(
            level,
            FunctionSecurity.INDIRECT
            if domain in _INDIRECT_DOMAINS
            else FunctionSecurity.CONTROL,
        )
    return level


def _static_target(target: Mapping[str, Any]) -> bool:
    supported = {"entity_id", "device_id", "area_id", "floor_id", "label_id"}
    if not set(target).issubset(supported):
        return False
    for value in target.values():
        values = value if isinstance(value, list) else [value]
        if not values or any(
            not isinstance(item, str) or not item or "{{" in item or "{%" in item
            for item in values
        ):
            return False
    return True


def contains_indirect_service_call(value: Any) -> bool:
    """Detect explicit generic HA wrapper calls in model-supplied arguments."""
    if isinstance(value, Mapping):
        domain = value.get("domain")
        service = value.get("service", value.get("action"))
        if isinstance(domain, str) and domain in _INDIRECT_DOMAINS:
            return True
        if isinstance(service, str):
            service_domain = service.partition(".")[0]
            if service_domain in _INDIRECT_DOMAINS:
                return True
        return any(contains_indirect_service_call(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_indirect_service_call(child) for child in value)
    return False
