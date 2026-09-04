"""Base classes for functions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ..exceptions import EntityNotExposed, EntityNotFound, InvalidFunction


class _RuntimeFunctionConfig(dict[str, Any]):
    """Runtime-validated config with an explicit persistence representation."""

    def __init__(
        self, runtime_config: dict[str, Any], persisted_config: dict[str, Any]
    ) -> None:
        super().__init__(runtime_config)
        self._persisted_config = persisted_config

    def persisted_copy(self) -> dict[str, Any]:
        """Return an isolated JSON/YAML-safe source configuration."""
        return deepcopy(self._persisted_config)

    def __deepcopy__(self, memo: dict[int, Any]) -> _RuntimeFunctionConfig:
        """Copy runtime containers without de-hydrating schema-created objects."""
        return _copy_runtime_value(self, memo)


def _copy_runtime_value(value: Any, memo: dict[int, Any]) -> Any:
    """Copy mutable config containers while treating runtime objects as atomic.

    Home Assistant schema validation can hydrate persisted strings into objects such
    as Template instances that are deliberately bound to the live HomeAssistant
    object and are not deepcopy-safe. Runtime Function Tool callers still require
    isolated mutable dictionaries/lists, so copy those containers recursively while
    retaining schema-created leaf objects by reference.
    """
    value_id = id(value)
    if value_id in memo:
        return memo[value_id]

    if isinstance(value, _RuntimeFunctionConfig):
        copied = _RuntimeFunctionConfig({}, value.persisted_copy())
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

    return value


def _represent_runtime_function_config(
    dumper: yaml.SafeDumper, data: _RuntimeFunctionConfig
) -> Any:
    """Serialize runtime configs using only their original persisted representation."""
    return dumper.represent_dict(data.persisted_copy())


# Runtime Function Tool objects can legitimately reach the management normalization
# path before they are written back as YAML. Keep runtime deepcopy semantics correct,
# and make the persistence conversion explicit at the serializer boundary instead.
yaml.SafeDumper.add_representer(
    _RuntimeFunctionConfig, _represent_runtime_function_config
)


class Function(ABC):
    def __init__(self, data_schema: vol.Schema = vol.Schema({})) -> None:
        """Initialize tool."""
        self.data_schema = data_schema.extend({vol.Required("type"): str})

    def validate_schema(self, function_config: dict[str, Any]) -> dict[str, Any]:
        """Validate and convert function configuration using the schema."""
        try:
            persisted_config = (
                function_config.persisted_copy()
                if isinstance(function_config, _RuntimeFunctionConfig)
                else deepcopy(function_config)
            )
            result = self.data_schema(deepcopy(persisted_config))
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
