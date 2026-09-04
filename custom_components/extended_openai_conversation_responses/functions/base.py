"""Base classes for functions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ..exceptions import EntityNotExposed, EntityNotFound, InvalidFunction


class _RuntimeFunctionConfig(dict[str, Any]):
    """Runtime-validated config with a persistence-safe deepcopy boundary."""

    def __init__(
        self, runtime_config: dict[str, Any], persisted_config: dict[str, Any]
    ) -> None:
        super().__init__(runtime_config)
        self._persisted_config = persisted_config

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        """Return the plain source config instead of copying runtime HA objects."""
        return deepcopy(self._persisted_config, memo)


def _copy_runtime_value(value: Any, memo: dict[int, Any]) -> Any:
    """Copy runtime config containers while retaining hydrated leaf objects."""
    value_id = id(value)
    if value_id in memo:
        return memo[value_id]

    if isinstance(value, _RuntimeFunctionConfig):
        copied = _RuntimeFunctionConfig({}, deepcopy(value._persisted_config))
        memo[value_id] = copied
        copied.update(
            {key: _copy_runtime_value(item, memo) for key, item in value.items()}
        )
        return copied

    if isinstance(value, dict):
        copied_dict: dict[Any, Any] = {}
        memo[value_id] = copied_dict
        copied_dict.update(
            {key: _copy_runtime_value(item, memo) for key, item in value.items()}
        )
        return copied_dict

    if isinstance(value, list):
        copied_list: list[Any] = []
        memo[value_id] = copied_list
        copied_list.extend(_copy_runtime_value(item, memo) for item in value)
        return copied_list

    if isinstance(value, tuple):
        copied_tuple = tuple(_copy_runtime_value(item, memo) for item in value)
        memo[value_id] = copied_tuple
        return copied_tuple

    if isinstance(value, set):
        copied_set = {_copy_runtime_value(item, memo) for item in value}
        memo[value_id] = copied_set
        return copied_set

    if isinstance(value, frozenset):
        copied_frozenset = frozenset(
            _copy_runtime_value(item, memo) for item in value
        )
        memo[value_id] = copied_frozenset
        return copied_frozenset

    # Schema-created runtime objects such as Home Assistant Template instances are
    # deliberately treated as atomic. They are reusable, but recursively copying
    # them can traverse into the live HomeAssistant object and is not supported.
    return value


def copy_runtime_function_config(value: Any) -> Any:
    """Return an isolated runtime-safe copy without de-hydrating Function Tools."""
    return _copy_runtime_value(value, {})


class Function(ABC):
    def __init__(self, data_schema: vol.Schema = vol.Schema({})) -> None:
        """Initialize tool."""
        self.data_schema = data_schema.extend({vol.Required("type"): str})

    def validate_schema(self, function_config: dict[str, Any]) -> dict[str, Any]:
        """Validate and convert function configuration using the schema."""
        try:
            persisted_config = deepcopy(function_config)
            result = self.data_schema(function_config)
            if not isinstance(result, dict):
                return {}
            return _RuntimeFunctionConfig(dict(result), persisted_config)
        except vol.error.Error as e:
            from . import FUNCTIONS

            function_type = next(
                (key for key, value in FUNCTIONS.items() if value == self),
                "",
            )
            raise InvalidFunction(function_type) from e

    def validate_entity_ids(
        self,
        hass: HomeAssistant,
        entity_ids: list[str],
        exposed_entities: list[dict[str, Any]],
    ) -> None:
        not_found = [
            entity_id for entity_id in entity_ids if hass.states.get(entity_id) is None
        ]
        if not_found:
            raise EntityNotFound(", ".join(not_found))
        exposed_entity_ids = {e["entity_id"] for e in exposed_entities}
        not_exposed = [
            entity_id for entity_id in entity_ids if entity_id not in exposed_entity_ids
        ]
        if not_exposed:
            raise EntityNotExposed(", ".join(not_exposed))

    @abstractmethod
    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        """Execute function."""
