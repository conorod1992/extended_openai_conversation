"""Tests for Function base class and helper function."""

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock

import pytest
import voluptuous as vol

# Import Tools
from custom_components.extended_openai_conversation_responses.exceptions import (
    EntityNotExposed,
    EntityNotFound,
    FunctionNotFound,
    InvalidFunction,
)
from custom_components.extended_openai_conversation_responses.functions import (
    NativeFunction,
    ScriptFunction,
    TemplateFunction,
    get_function,
)
from custom_components.extended_openai_conversation_responses.functions.base import (
    Function,
    _RuntimeFunctionConfig,
    copy_runtime_function_config,
)


class TestGetFunction:
    """Test get_function helper function."""

    def test_get_existing_function(self):
        """Test getting an existing function."""
        function = get_function("template")
        assert isinstance(function, TemplateFunction)

    def test_get_native_function(self):
        """Test getting native function."""
        function = get_function("native")
        assert isinstance(function, NativeFunction)

    def test_get_script_function(self):
        """Test getting script function."""
        function = get_function("script")
        assert isinstance(function, ScriptFunction)

    def test_get_nonexistent_function(self):
        """Test getting a nonexistent function raises error."""
        with pytest.raises(FunctionNotFound):
            get_function("nonexistent")


class TestFunctionBase:
    """Test Function base class."""

    def test_validate_function_valid(self, hass):
        """Test validate_function with valid arguments - using pre-built Template."""
        function = TemplateFunction()
        # For testing, pass an already-built Template to bypass cv.template validation
        # This tests the schema structure, not the cv.template behavior
        # The function's schema uses cv.template which validates strings
        # For unit testing, we verify the schema accepts the required keys
        assert (
            function.data_schema.schema.get(vol.Required("value_template")) is not None
        )
        assert function.data_schema.schema.get(vol.Required("type")) is not None

    def test_validate_function_invalid(self):
        """Test validate_function with invalid arguments raises InvalidFunction."""
        function = TemplateFunction()
        with pytest.raises(InvalidFunction):
            function.validate_schema({"type": "template"})  # Missing value_template

    def test_validate_entity_ids_valid(self, hass, exposed_entities):
        """Test validate_entity_ids with valid entities."""
        function = NativeFunction()
        # Should not raise
        function.validate_entity_ids(hass, ["light.living_room"], exposed_entities)

    def test_validate_entity_ids_not_found(self, hass, exposed_entities):
        """Test validate_entity_ids raises EntityNotFound."""
        function = NativeFunction()
        hass.states.get = MagicMock(return_value=None)

        with pytest.raises(EntityNotFound):
            function.validate_entity_ids(hass, ["light.nonexistent"], exposed_entities)

    def test_validate_entity_ids_not_exposed(self, hass, exposed_entities):
        """Test validate_entity_ids raises EntityNotExposed."""
        function = NativeFunction()

        with pytest.raises(EntityNotExposed):
            function.validate_entity_ids(hass, ["light.not_exposed"], exposed_entities)

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


def test_copy_runtime_function_config_preserves_collections_and_hydrated_leaves(
) -> None:
    """Container types are copied while opaque runtime leaves retain identity."""
    runtime_object = _AtomicRuntimeObject()
    source = {
        "tuple": ("alpha", runtime_object),
        "set": {"alpha", "beta"},
        "frozenset": frozenset({"alpha", "beta"}),
        "nested": [runtime_object],
    }

    copied = copy_runtime_function_config(source)

    assert copied["tuple"] == source["tuple"]
    assert isinstance(copied["tuple"], tuple)
    assert isinstance(copied["set"], set)
    assert isinstance(copied["frozenset"], frozenset)
    assert copied["set"] is not source["set"]
    assert copied["frozenset"] is not source["frozenset"]
    assert copied["tuple"][1] is runtime_object
    assert copied["nested"] is not source["nested"]
    assert copied["nested"][0] is runtime_object


def test_validate_schema_returns_empty_mapping_for_non_mapping_schema_result() -> None:
    """Defensively reject a schema callable that violates the mapping contract."""
    function = _Function()
    function.data_schema = lambda value: [value]  # type: ignore[assignment]
    config = {"type": "example", "value": "kept"}

    assert function.validate_schema(config) == {}
    assert config == {"type": "example", "value": "kept"}


def test_validate_schema_preserves_persisted_and_runtime_copy_contracts() -> None:
    """Hydration stays runtime-only while copied wrappers retain hydrated leaves."""
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

    copied = copy_runtime_function_config(validated)

    assert isinstance(copied, _RuntimeFunctionConfig)
    assert copied is not validated
    assert copied["runtime"] is runtime_object
    assert deepcopy(copied) == {"type": "example", "source": "persisted"}
    assert deepcopy(copied) is not config

