"""Canonical coverage for historical native Function Tool schema migration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    native_function_schema_migration as migration,
)


def _tool(name: str, parameters: object, **spec_overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "name": f"custom_{name}",
        "description": "User-customized presentation metadata",
        "parameters": deepcopy(parameters),
    }
    spec.update(spec_overrides)
    return {
        "function": {"type": "native", "name": name},
        "spec": spec,
        "enabled": False,
        "guest_allowed": True,
    }


def test_service_migration_covers_all_recognized_historical_shapes() -> None:
    """Both batch schemas and the single-service schema receive current constraints."""
    cases = (
        (
            "execute_service",
            migration._LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS,
            True,
        ),
        (
            "execute_service",
            migration._LEGACY_PRESET_EXECUTE_SERVICE_PARAMETERS,
            False,
        ),
        (
            "execute_service_single",
            migration._LEGACY_PRESET_EXECUTE_SERVICE_SINGLE_PARAMETERS,
            False,
        ),
    )

    for name, parameters, is_legacy_default in cases:
        original = _tool(name, parameters)
        migrated, changed = migration.migrate_legacy_stock_native_function_tools(
            [original]
        )

        assert changed is True
        assert original["spec"]["parameters"] == parameters  # type: ignore[index]
        migrated_tool = migrated[0]
        assert migrated_tool["enabled"] is False
        assert migrated_tool["guest_allowed"] is True
        assert migrated_tool["spec"]["name"] == f"custom_{name}"
        assert migrated_tool["spec"]["description"] == (
            "User-customized presentation metadata"
        )
        assert migrated_tool["spec"]["strict"] is False

        migrated_parameters = migrated_tool["spec"]["parameters"]
        if name == "execute_service_single":
            service_data = migrated_parameters["properties"]["service_data"]
        else:
            list_schema = migrated_parameters["properties"]["list"]
            assert list_schema["maxItems"] == migration.MAX_NATIVE_SERVICE_ACTIONS
            service_data = list_schema["items"]["properties"]["service_data"]

        assert service_data["description"] == migration.SERVICE_DATA_DESCRIPTION
        assert service_data["additionalProperties"] is True
        if is_legacy_default:
            assert migrated_parameters["required"] == ["list"]


def test_statistics_migration_updates_current_constraints() -> None:
    original = _tool(
        "get_statistics", migration._LEGACY_PRESET_GET_STATISTICS_PARAMETERS
    )

    migrated, changed = migration.migrate_legacy_stock_native_function_tools([original])

    assert changed is True
    parameters = migrated[0]["spec"]["parameters"]
    properties = parameters["properties"]
    assert migrated[0]["spec"]["strict"] is False
    assert properties["period"]["enum"] == migration.STATISTICS_PERIODS
    assert properties["units"]["additionalProperties"] == {"type": "string"}
    assert properties["types"]["items"]["enum"] == migration.STATISTICS_TYPES


def test_near_matches_and_malformed_tool_shapes_are_not_migrated() -> None:
    legacy = migration._LEGACY_PRESET_EXECUTE_SERVICE_PARAMETERS
    changed_schema = deepcopy(legacy)
    changed_schema["properties"]["list"]["minItems"] = 1

    candidates = [
        {"function": "execute_service", "spec": {"parameters": deepcopy(legacy)}},
        {
            "function": {
                "type": "native",
                "name": "execute_service",
                "unexpected": True,
            },
            "spec": {"parameters": deepcopy(legacy)},
        },
        _tool("execute_service", legacy, strict=False),
        _tool("execute_service", "not-a-schema"),
        _tool("execute_service", changed_schema),
        _tool("get_statistics", {"type": "object", "properties": {}}),
        _tool("unrecognized_native_tool", {}),
    ]

    migrated, changed = migration.migrate_legacy_stock_native_function_tools(candidates)

    assert changed is False
    assert migrated == candidates
    assert migrated is not candidates


def test_yaml_migration_rejects_invalid_and_non_tool_inputs_without_rewriting() -> None:
    values = (
        None,
        "",
        "   ",
        "[unterminated",
        "key: value\n",
        "- execute_service\n",
    )

    for value in values:
        migrated, changed = migration.migrate_legacy_stock_native_function_tools_yaml(
            value
        )
        assert changed is False
        assert migrated == value


def test_yaml_noop_preserves_original_formatting_but_real_migration_serializes() -> (
    None
):
    custom_yaml = "- function: {type: native, name: custom}\n  spec: {parameters: {}}\n"
    unchanged, changed = migration.migrate_legacy_stock_native_function_tools_yaml(
        custom_yaml
    )
    assert changed is False
    assert unchanged == custom_yaml

    legacy = _tool(
        "execute_service_single",
        migration._LEGACY_PRESET_EXECUTE_SERVICE_SINGLE_PARAMETERS,
    )
    source = yaml.safe_dump([legacy], sort_keys=False)
    migrated_yaml, changed = migration.migrate_legacy_stock_native_function_tools_yaml(
        source
    )

    assert changed is True
    parsed = yaml.safe_load(migrated_yaml)
    assert parsed[0]["spec"]["strict"] is False
    assert (
        parsed[0]["spec"]["parameters"]["properties"]["service_data"][
            "additionalProperties"
        ]
        is True
    )


def _legacy_service_preset(implementation: str) -> dict[str, Any]:
    service_data = {
        "type": "object",
        "description": (
            "Service data, including an entity_id, device_id, or area_id target."
        ),
    }
    if implementation == "execute_service_single":
        parameters = {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "The Home Assistant service domain.",
                },
                "service": {
                    "type": "string",
                    "description": "The service name to call.",
                },
                "service_data": service_data,
            },
            "required": ["domain", "service", "service_data"],
        }
    else:
        parameters = {
            "type": "object",
            "properties": {
                "list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "description": "The Home Assistant service domain.",
                            },
                            "service": {
                                "type": "string",
                                "description": "The service name to call.",
                            },
                            "service_data": service_data,
                        },
                        "required": ["domain", "service", "service_data"],
                    },
                }
            },
            "required": ["list"],
        }
    return {
        "spec": {
            "name": f"custom_{implementation}",
            "description": "Keep this customized description.",
            "parameters": parameters,
        },
        "function": {"type": "native", "name": implementation},
        "enabled": False,
        "guest_allowed": True,
    }


def _service_data(tool: dict[str, Any]) -> dict[str, Any]:
    properties = tool["spec"]["parameters"]["properties"]
    if tool["function"]["name"] == "execute_service_single":
        return properties["service_data"]
    return properties["list"]["items"]["properties"]["service_data"]


@pytest.mark.parametrize(
    "implementation", ["execute_service", "execute_service_single"]
)
def test_historical_inserted_service_preset_is_migrated(implementation: str) -> None:
    """Both historical built-in service presets receive the persistent open schema."""
    tool = _legacy_service_preset(implementation)

    migrated, changed = migration.migrate_legacy_stock_native_function_tools([tool])

    assert changed is True
    current = migrated[0]
    assert current["spec"]["name"] == f"custom_{implementation}"
    assert current["spec"]["description"] == "Keep this customized description."
    assert current["enabled"] is False
    assert current["guest_allowed"] is True
    assert current["spec"]["strict"] is False
    assert _service_data(current)["additionalProperties"] is True


@pytest.mark.parametrize("implementation", ["execute_service", "get_statistics"])
def test_yaml_aliases_do_not_migrate_custom_schemas_or_backend_payloads(implementation):
    fingerprint = (
        migration._LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS
        if implementation == "execute_service"
        else migration._LEGACY_PRESET_GET_STATISTICS_PARAMETERS
    )
    shared = deepcopy(fingerprint)
    native = _tool(implementation, {})
    native["spec"]["parameters"] = shared
    custom = {
        "spec": {"name": "custom_template", "parameters": shared},
        "function": {"type": "template", "value_template": "Done"},
        "enabled": False,
    }
    backend = {
        "spec": {
            "name": "custom_script",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "variables": {"schema": shared},
            "sequence": [{"stop": "Done"}],
        },
        "enabled": False,
    }
    source = yaml.safe_dump([native, custom, backend], sort_keys=False)
    assert "&id" in source and "*id" in source
    before = yaml.safe_load(source)
    result, changed = migration.migrate_legacy_stock_native_function_tools_yaml(source)
    assert changed is True
    restored = yaml.safe_load(result)
    assert restored[1:] == before[1:]
    assert restored[0]["spec"]["strict"] is False
    assert all(tool["enabled"] is False for tool in restored)
    assert migration.migrate_legacy_stock_native_function_tools_yaml(result) == (
        result,
        False,
    )


@pytest.mark.parametrize("implementation", ["execute_service", "get_statistics"])
def test_every_eligible_alias_uses_its_original_schema_fingerprint(implementation):
    shared = deepcopy(
        migration._LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS
        if implementation == "execute_service"
        else migration._LEGACY_PRESET_GET_STATISTICS_PARAMETERS
    )
    first = _tool(implementation, {})
    first["spec"]["parameters"] = shared
    second = {**first, "spec": {**first["spec"], "name": "second_native"}}
    source = yaml.safe_dump([first, second], sort_keys=False)
    result, changed = migration.migrate_legacy_stock_native_function_tools_yaml(source)
    assert changed is True
    tools = yaml.safe_load(result)
    assert all(tool["spec"]["strict"] is False for tool in tools)
    assert tools[0]["spec"]["parameters"] == tools[1]["spec"]["parameters"]
    assert yaml.safe_load(source)[0]["spec"]["parameters"] == shared


def test_whole_tool_aliases_are_assessed_independently():
    tool = _tool(
        "execute_service", migration._LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS
    )
    migrated, changed = migration.migrate_legacy_stock_native_function_tools(
        [tool, tool]
    )
    assert changed is True
    assert migrated[0] == migrated[1] and migrated[0] is not migrated[1]
    assert "strict" not in tool["spec"]


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("damaged", [False, True])
def test_representation_and_quarantined_sibling_migrate_independently(native, damaged):
    tool = _tool("get_statistics", migration._LEGACY_PRESET_GET_STATISTICS_PARAMETERS)
    values = [tool, 42] if damaged else [tool]
    before = deepcopy(values)
    source = values if native else yaml.safe_dump(values)
    result, changed = migration.migrate_legacy_stock_native_function_tools_yaml(source)
    assert changed
    decoded = result if native else yaml.safe_load(result)
    assert "week" in decoded[0]["spec"]["parameters"]["properties"]["period"]["enum"]
    assert values == before
    if damaged:
        assert decoded[1] == 42
    assert migration.migrate_legacy_stock_native_function_tools_yaml(result) == (result, False)
    assert isinstance(result, list) == native
