"""Focused coverage for Function base helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from custom_components.extended_openai_conversation_responses.functions.base import (
    Function,
    _RuntimeFunctionConfig,
    copy_runtime_function_config,
)


class _Function(Function):
    """Minimal concrete Function for base-class validation tests."""

    async def execute(
        self,
        hass: Any,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        return None


class _AtomicRuntimeObject:
    """Stand-in for a hydrated Home Assistant runtime object."""


def test_copy_runtime_function_config_preserves_aliases_and_cycles() -> None:
    """Mutable containers are isolated while aliases/cycles remain coherent."""
    shared: dict[str, Any] = {"value": [1, 2]}
    source: dict[str, Any] = {"first": shared, "second": shared}
    source["self"] = source

    copied = copy_runtime_function_config(source)

    assert copied is not source
    assert copied["first"] is copied["second"]
    assert copied["first"] is not shared
    assert copied["first"]["value"] is not shared["value"]
    assert copied["self"] is copied


def test_copy_runtime_function_config_handles_tuple_set_and_frozenset() -> None:
    """Immutable/set-like container branches retain type and values."""
    source = {
        "tuple": ("alpha", "beta"),
        "set": {"alpha", "beta"},
        "frozenset": frozenset({"alpha", "beta"}),
    }

    copied = copy_runtime_function_config(source)

    assert copied == source
    assert isinstance(copied["tuple"], tuple)
    assert isinstance(copied["set"], set)
    assert isinstance(copied["frozenset"], frozenset)
    assert copied["set"] is not source["set"]
    assert copied["frozenset"] is not source["frozenset"]


def test_copy_runtime_function_config_keeps_hydrated_leaf_by_identity() -> None:
    """Opaque runtime objects must not be recursively copied."""
    runtime_object = _AtomicRuntimeObject()
    source = {"runtime": runtime_object, "nested": [runtime_object]}

    copied = copy_runtime_function_config(source)

    assert copied is not source
    assert copied["nested"] is not source["nested"]
    assert copied["runtime"] is runtime_object
    assert copied["nested"][0] is runtime_object


def test_copy_runtime_function_config_preserves_runtime_wrapper_contract() -> None:
    """Runtime wrapper copies keep hydrated values but deepcopy as persisted data."""
    runtime_object = _AtomicRuntimeObject()
    persisted = {"type": "example", "template": "{{ value }}"}
    runtime = _RuntimeFunctionConfig(
        {"type": "example", "template": runtime_object}, persisted
    )

    copied = copy_runtime_function_config(runtime)

    assert isinstance(copied, _RuntimeFunctionConfig)
    assert copied is not runtime
    assert copied["template"] is runtime_object
    assert deepcopy(copied) == persisted
    assert deepcopy(copied) is not persisted


def test_validate_schema_returns_empty_mapping_for_non_mapping_schema_result() -> None:
    """Defensively reject a schema callable that violates the mapping contract."""
    function = _Function()
    function.data_schema = lambda value: [value]  # type: ignore[assignment]

    config = {"type": "example", "value": "kept"}

    assert function.validate_schema(config) == {}
    assert config == {"type": "example", "value": "kept"}


def test_validate_schema_keeps_persisted_source_separate_from_runtime_result() -> None:
    """Runtime schema hydration must not leak into the persisted deepcopy boundary."""
    runtime_object = _AtomicRuntimeObject()

    def _hydrate(value: dict[str, Any]) -> dict[str, Any]:
        value["runtime"] = runtime_object
        return value

    function = _Function()
    function.data_schema = _hydrate  # type: ignore[assignment]
    config = {"type": "example", "source": "persisted"}

    validated = function.validate_schema(config)

    assert isinstance(validated, _RuntimeFunctionConfig)
    assert validated["runtime"] is runtime_object
    assert deepcopy(validated) == {"type": "example", "source": "persisted"}
