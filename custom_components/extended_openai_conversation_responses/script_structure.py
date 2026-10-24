"""Native HA script containers and inspection, independent of execution policy.

Canonical inspection retains the historical Request Rule ordering and budgets.
Unvalidated inspection retains Script Function device preflight's mapping shorthand
and branch-wrapper behavior. Neither walker descends into payloads or conditions.
Home Assistant remains responsible for schema validation and script execution.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, cast

MAX_SCRIPT_NODES = 500
MAX_SCRIPT_DEPTH = 12
_MISSING = object()

type ScriptContainerKind = Literal["sequence", "parallel", "choose", "repeat"]
type ScriptRewriteKind = ScriptContainerKind | Literal["other"]
type ScriptActionRewrite = Callable[
    [Mapping[str, Any], ScriptRewriteKind, Callable[[], dict[str, Any]]], Sequence[Any]
]


@dataclass(frozen=True, slots=True)
class ScriptContainer:
    """A structural field of an action, never a similarly named payload field."""

    key: str
    kind: ScriptContainerKind
    value: Any


def script_containers(action: Mapping[str, Any]) -> Iterator[ScriptContainer]:
    """Classify only native action containers, in established inspection order.

    Choose/repeat mappings contain metadata and conditions, not executable steps.
    Canonical parallel branch wrappers remain action mappings for counting purposes.
    """
    for key in ("sequence", "then", "else", "default", "parallel", "choose", "repeat"):
        value = action.get(key, _MISSING)
        if value is not _MISSING:
            kind = cast(
                ScriptContainerKind,
                key if key in {"parallel", "choose", "repeat"} else "sequence",
            )
            yield ScriptContainer(key, kind, value)


def _child_sequences(action: Mapping[str, Any], *, unvalidated: bool) -> Iterator[Any]:
    """Resolve sequence locations without inspecting branch conditions or payloads."""
    deferred_choose = _MISSING
    for container in script_containers(action):
        value = container.value
        if container.kind in {"sequence", "parallel"}:
            yield value
        elif container.kind == "choose":
            if unvalidated:
                # Legacy device preflight visits repeat before choose, and
                # inspects choose branch wrappers themselves.
                deferred_choose = value
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                for branch in value:
                    if isinstance(branch, Mapping):
                        yield branch.get("sequence")
        elif unvalidated:
            yield value.get("sequence", [])
        elif isinstance(value, Mapping):
            yield value.get("sequence")
    if deferred_choose is not _MISSING:
        yield deferred_choose


def _canonical_sequence(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(child, Mapping) for child in value)
    )


def _walk_script_actions(
    sequence: Any,
    *,
    unvalidated: bool,
    depth: int,
    budget: list[int],
    max_depth: int | None,
    max_nodes: int | None,
) -> Iterator[Mapping[str, Any]]:
    if max_depth is not None and depth > max_depth:
        raise ValueError(f"action sequence exceeds maximum depth {max_depth}")
    actions = [sequence] if unvalidated and isinstance(sequence, Mapping) else sequence
    for item in actions:
        if unvalidated and not isinstance(item, Mapping):
            continue
        budget[0] += 1
        if max_nodes is not None and budget[0] > max_nodes:
            raise ValueError(f"action sequence exceeds {max_nodes} nodes")
        yield item
        for child in _child_sequences(item, unvalidated=unvalidated):
            if unvalidated or _canonical_sequence(child):
                yield from _walk_script_actions(
                    child,
                    unvalidated=unvalidated,
                    depth=depth + 1,
                    budget=budget,
                    max_depth=max_depth,
                    max_nodes=max_nodes,
                )


def iter_script_actions(
    sequence: Sequence[Mapping[str, Any]],
    *,
    depth: int = 0,
    budget: list[int] | None = None,
    max_depth: int = MAX_SCRIPT_DEPTH,
    max_nodes: int = MAX_SCRIPT_NODES,
) -> Iterator[Mapping[str, Any]]:
    """Inspect canonical action mappings with explicit action-count/depth limits.

    Skip malformed nested sequences as before; HA validates executable input.
    Payload complexity is a separate caller-owned validation budget.
    """
    return _walk_script_actions(
        sequence,
        unvalidated=False,
        depth=depth,
        budget=[0] if budget is None else budget,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )


def script_selector_fields(value: Any) -> dict[int, frozenset[str]]:
    """Identify concrete selectors only in actual native script structures.

    Event payloads, repeat items, variables and service data are opaque even when
    their keys resemble actions, conditions or triggers.
    """
    result: dict[int, frozenset[str]] = {}
    selector_keys = frozenset({"entity_id", "zone", "device_id"})

    def conditions(config: Any) -> None:
        if isinstance(config, list):
            for item in config:
                conditions(item)
        elif isinstance(config, Mapping):
            result[id(config)] = selector_keys
            condition = config.get("condition")
            if isinstance(condition, (list, Mapping)):
                conditions(condition)
            elif isinstance(condition, str) and condition in {"and", "or", "not"}:
                conditions(config.get("conditions", []))
            elif condition is None:
                # HA also accepts logical shorthand without a condition key.
                for operator in ("and", "or", "not"):
                    if operator in config:
                        conditions(config[operator])

    actions = [value] if isinstance(value, Mapping) else value
    if not isinstance(actions, list):
        return result
    for action in iter_script_actions(actions):
        if "scene" in action:
            result[id(action)] = frozenset({"scene"})
        if "condition" in action:
            conditions(action)
        conditions(action.get("if", []))
        for branch in action.get("choose", []):
            if isinstance(branch, Mapping):
                conditions(branch.get("conditions", []))
        repeat = action.get("repeat")
        if isinstance(repeat, Mapping):
            for key in ("while", "until"):
                conditions(repeat.get(key, []))
        triggers = action.get("wait_for_trigger", [])
        if isinstance(triggers, Mapping):
            triggers = [triggers]
        if isinstance(triggers, list):
            for trigger in triggers:
                if isinstance(trigger, Mapping):
                    result[id(trigger)] = selector_keys
    return result


def iter_unvalidated_script_actions(sequence: Any) -> Iterator[Mapping[str, Any]]:
    """Inspect Script Function preflight input without adding Request Rule quotas.

    Preserve single-mapping shorthand, mixed-list filtering and choose wrappers.
    This is not shorthand normalization or an alternative to HA validation.
    """
    return _walk_script_actions(
        sequence,
        unvalidated=True,
        depth=0,
        budget=[0],
        max_depth=None,
        max_nodes=None,
    )


def normalize_script_containers(value: Any) -> Any:
    """Normalize HA action shorthand without replacing authored template text."""
    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list):
        return value
    result = []
    for action in value:
        if not isinstance(action, Mapping):
            result.append(action)
            continue
        normalized = deepcopy(dict(action))
        # Store HA's historical service-name alias in the common service form so
        # validation, dependency inspection and Guest preflight agree with HA.
        if "service_template" in normalized and not (
            "service" in normalized or "action" in normalized
        ):
            normalized["service"] = normalized.pop("service_template")
        for container in script_containers(normalized):
            key, kind, child = container.key, container.kind, container.value
            if kind == "sequence":
                normalized[key] = normalize_script_containers(child)
            elif kind == "parallel":
                branches = [child] if isinstance(child, Mapping) else child
                if isinstance(branches, list):
                    parallel = []
                    for branch in branches:
                        if isinstance(branch, Mapping) and "sequence" in branch:
                            parallel.append(
                                {
                                    **branch,
                                    "sequence": normalize_script_containers(
                                        branch["sequence"]
                                    ),
                                }
                            )
                        elif isinstance(branch, (Mapping, list)):
                            parallel.append(
                                {"sequence": normalize_script_containers(branch)}
                            )
                        else:
                            parallel.append(branch)
                    normalized[key] = parallel
            elif kind == "repeat" and isinstance(child, Mapping):
                repeat = dict(child)
                if "sequence" in repeat:
                    repeat["sequence"] = normalize_script_containers(repeat["sequence"])
                normalized[key] = repeat
            elif kind == "choose":
                branches = [child] if isinstance(child, Mapping) else child
                if isinstance(branches, list):
                    normalized[key] = [
                        {
                            **branch,
                            "sequence": normalize_script_containers(
                                branch.get("sequence", [])
                            ),
                        }
                        if isinstance(branch, Mapping)
                        else branch
                        for branch in branches
                    ]
        result.append(normalized)
    return result


def rewrite_script_actions(
    actions: Sequence[Mapping[str, Any]],
    rewrite_action: ScriptActionRewrite,
    *,
    transform_input: Callable[[Any], Any] | None = None,
) -> list[Any]:
    """Rewrite canonical structure with policy-owned zero/one/many action output.

    A callback receives the original mapping, its container role, and a continuation
    that rewrites its children into a fresh mapping. It may omit the continuation
    when replacing an action. Generated output is not revisited. Opaque inputs retain
    their identity unless ``transform_input`` is supplied; that mode maps effect
    fields while retaining choose metadata and repeat/if condition fields.

    No evaluation, authorization or new quota is introduced here. Scalar/non-list
    compatibility for instrumentation remains distinct from validated effect mapping.
    """

    def fields(source: Mapping[str, Any], kind: ScriptRewriteKind) -> dict[str, Any]:
        if transform_input is not None and kind == "choose":
            return {**source, "sequence": walk(source.get("sequence", []), "sequence")}
        containers = {
            container.key: container.kind for container in script_containers(source)
        }
        result = {}
        for name, value in source.items():
            if transform_input is not None and kind == "repeat":
                result[name] = (
                    walk(value, "sequence")
                    if name == "sequence"
                    else value
                    if name in {"while", "until"}
                    else transform_input(value)
                )
            elif name in containers:
                result[name] = walk(value, containers[name])
            elif transform_input is None or name == "if":
                result[name] = value
            else:
                result[name] = transform_input(value)
        return result

    def sequence(value: Any, kind: ScriptRewriteKind) -> list[Any]:
        result: list[Any] = []
        for item in value:
            if transform_input is not None or isinstance(item, Mapping):

                def descend(source: Mapping[str, Any] = item) -> dict[str, Any]:
                    return fields(source, kind)

                result.extend(rewrite_action(item, kind, descend))
            else:
                # Instrumentation historically retains scalar sequence members and
                # recursively visits nested lists without assigning them Stop scope.
                result.append(walk(item, "other"))
        return result

    def walk(value: Any, kind: ScriptRewriteKind) -> Any:
        if transform_input is not None:
            return fields(value, kind) if kind == "repeat" else sequence(value, kind)
        if isinstance(value, Mapping):
            return fields(value, kind)
        if isinstance(value, list):
            return sequence(value, kind)
        return value

    return sequence(list(actions), "sequence")
