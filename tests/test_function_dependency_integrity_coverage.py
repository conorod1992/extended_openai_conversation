"""Residual branch coverage for Function Tool dependency integrity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    function_dependency_integrity as integrity,
)


def test_template_detection_and_schema_type_inference() -> None:
    assert integrity._is_template_string("{{ value }}") is True
    assert integrity._is_template_string("plain") is False
    assert integrity._contains_template({"a": [1, {"b": "{% if x %}"}]}) is True
    assert integrity._contains_template(("plain", 2)) is False

    assert integrity._schema_types({"type": "string"}) == {"string"}
    assert integrity._schema_types({"type": ["string", 3, "null"]}) == {
        "string",
        "null",
    }
    assert integrity._schema_types({"properties": {}}) == {"object"}
    assert integrity._schema_types({"required": []}) == {"object"}
    assert integrity._schema_types({"items": {}}) == {"array"}
    assert integrity._schema_types({}) == set()


def test_mask_dynamic_schema_preserves_static_structure() -> None:
    schema = {
        "type": "object",
        "enum": [{"mode": "fixed"}],
        "const": {"mode": "fixed"},
        "properties": {
            "name": {"type": "string", "minLength": 2},
            "count": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": {"type": "string", "minLength": 1},
    }
    masked = integrity._mask_dynamic_schema(
        {"name": "{{ dynamic }}", "count": 2, "extra": "{{ value }}"}, schema
    )

    assert "enum" not in masked
    assert "const" not in masked
    assert masked["properties"]["name"] == {}
    assert masked["properties"]["count"] == {
        "type": "integer",
        "minimum": 1,
    }
    assert masked["properties"]["extra"] == {}
    assert masked["additionalProperties"] is False

    array_masked = integrity._mask_dynamic_schema(
        ["fixed", "{{ dynamic }}"],
        {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
    )
    assert array_masked["items"] == {}
    assert "uniqueItems" not in array_masked

    unchanged = {"type": "string", "minLength": 2}
    assert integrity._mask_dynamic_schema("fixed", unchanged) == unchanged


@pytest.mark.asyncio
async def test_static_descendant_validation_skips_templates_and_checks_static_values(
    monkeypatch,
) -> None:
    validate = AsyncMock()
    monkeypatch.setattr(integrity, "async_validate_function_arguments", validate)

    await integrity._async_validate_static_descendants(
        SimpleNamespace(), "{{ dynamic }}", {"type": "string"}
    )
    validate.assert_not_awaited()

    await integrity._async_validate_static_descendants(
        SimpleNamespace(), "fixed", {"type": "string", "minLength": 2}
    )
    validate.assert_awaited_once()
    spec, arguments = validate.await_args.args[1:]
    assert spec["parameters"]["properties"]["value"]["minLength"] == 2
    assert arguments == {"value": "fixed"}


@pytest.mark.asyncio
async def test_static_descendants_recurse_object_and_array(monkeypatch) -> None:
    validate = AsyncMock()
    monkeypatch.setattr(integrity, "async_validate_function_arguments", validate)
    schema = {
        "type": "object",
        "properties": {
            "known": {"type": "string"},
            "items": {"type": "array", "items": {"type": "integer"}},
        },
        "additionalProperties": {"type": "boolean"},
    }
    value = {
        "known": "fixed",
        "items": [1, "{{ later }}", 3],
        "extra": True,
        "dynamic": "{{ later }}",
    }

    await integrity._async_validate_static_descendants(
        SimpleNamespace(), value, schema
    )

    checked_values = [call.args[2]["value"] for call in validate.await_args_list]
    assert checked_values == ["fixed", 1, 3, True]


@pytest.mark.asyncio
async def test_validate_static_function_arguments_rejects_bad_schema_and_masks_dynamic(
    monkeypatch,
) -> None:
    with pytest.raises(HomeAssistantError, match="input schema is invalid"):
        await integrity.async_validate_static_function_arguments(
            SimpleNamespace(), {"parameters": []}, {}
        )

    validate = AsyncMock()
    descendants = AsyncMock()
    monkeypatch.setattr(integrity, "async_validate_function_arguments", validate)
    monkeypatch.setattr(integrity, "_async_validate_static_descendants", descendants)

    spec = {
        "name": "demo",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    }
    arguments = {"value": "{{ dynamic }}"}
    await integrity.async_validate_static_function_arguments(
        SimpleNamespace(), spec, arguments
    )

    masked_spec = validate.await_args.args[1]
    assert masked_spec["parameters"]["properties"]["value"] == {}
    descendants.assert_awaited_once()


@pytest.mark.asyncio
async def test_rule_action_iteration_and_recursive_references(monkeypatch) -> None:
    assert list(integrity._rule_script_actions({"action": {"actions": "bad"}})) == []
    assert list(integrity._rule_script_actions({})) == []

    monkeypatch.setattr(
        integrity.request_rules,
        "_iter_script_actions",
        lambda actions: iter(actions),
    )
    service = (
        f"{integrity.DOMAIN}.{integrity.SERVICE_CALL_FUNCTION}"
    )
    manager = SimpleNamespace(
        _rules=[
            {
                "id": "one",
                "name": "Rule one",
                "action": {
                    "actions": [
                        {"action": service, "data": {"function": "alpha"}},
                        {"action": "light.turn_on"},
                    ]
                },
            },
            {
                "id": "two",
                "name": "Rule two",
                "action": {"actions": [{"service": service, "data": {"function": "beta"}}]},
            },
        ]
    )

    assert integrity.recursive_function_references(manager, "alpha") == [
        {"id": "one", "name": "Rule one"}
    ]


@pytest.mark.asyncio
async def test_recursive_rename_noop_success_and_save_rollback(monkeypatch) -> None:
    monkeypatch.setattr(
        integrity.request_rules,
        "_iter_script_actions",
        lambda actions: iter(actions),
    )
    monkeypatch.setattr(
        integrity.request_rules, "validate_rule", lambda rule: rule
    )
    service = f"{integrity.DOMAIN}.{integrity.SERVICE_CALL_FUNCTION}"
    original = {
        "id": "one",
        "name": "Rule",
        "action": {
            "actions": [
                {"action": service, "data": {"function": "old", "arguments": {}}}
            ]
        },
    }
    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _rules=[original],
        _require_revision_locked=lambda revision: None,
        _sort_and_compile=lambda: None,
        _async_save_locked=AsyncMock(),
    )

    assert await integrity.async_rename_function_reference_recursive(
        manager, "old", "old"
    ) == 0
    assert await integrity.async_rename_function_reference_recursive(
        manager, "missing", "new"
    ) == 0
    assert await integrity.async_rename_function_reference_recursive(
        manager, "old", "new", expected_revision="rev"
    ) == 1
    assert manager._rules[0]["action"]["actions"][0]["data"]["function"] == "new"

    manager._rules = [original]
    manager._async_save_locked = AsyncMock(side_effect=RuntimeError("save failed"))
    with pytest.raises(RuntimeError, match="save failed"):
        await integrity.async_rename_function_reference_recursive(
            manager, "old", "new"
        )
    assert manager._rules == [original]


@pytest.mark.asyncio
async def test_request_rule_function_validation_rejects_invalid_dependencies(monkeypatch) -> None:
    service = f"{integrity.DOMAIN}.{integrity.SERVICE_CALL_FUNCTION}"
    monkeypatch.setattr(
        integrity.request_rules,
        "_iter_script_actions",
        lambda actions: iter(actions),
    )
    monkeypatch.setattr(integrity, "function_tool_enabled", lambda tool: tool["enabled"])

    def rule(data):
        return {"action": {"actions": [{"action": service, "data": data}]}}

    tools = [
        {
            "enabled": True,
            "spec": {"name": "available", "parameters": {"type": "object"}},
        },
        {
            "enabled": False,
            "spec": {"name": "disabled", "parameters": {"type": "object"}},
        },
    ]

    with pytest.raises(HomeAssistantError, match="action is invalid"):
        await integrity.async_validate_request_rule_functions(
            SimpleNamespace(), rule({}), tools
        )
    with pytest.raises(HomeAssistantError, match="arguments must be an object"):
        await integrity.async_validate_request_rule_functions(
            SimpleNamespace(), rule({"function": "available", "arguments": []}), tools
        )
    with pytest.raises(HomeAssistantError, match="unavailable or disabled"):
        await integrity.async_validate_request_rule_functions(
            SimpleNamespace(), rule({"function": "disabled", "arguments": {}}), tools
        )

    validate = AsyncMock()
    monkeypatch.setattr(integrity, "async_validate_static_function_arguments", validate)
    await integrity.async_validate_request_rule_functions(
        SimpleNamespace(),
        rule({"function": "available", "arguments": {"x": 1}}),
        tools,
    )
    validate.assert_awaited_once()


def test_persist_wrapper_applies_group_mutation_and_revision_context() -> None:
    calls = []

    def original(hass, entry, subentry, tools, groups, **kwargs):
        calls.append(kwargs)
        return kwargs

    wrapped = integrity.wrap_persist_function_configuration(original)
    subentry = SimpleNamespace(data={integrity.CONF_GUEST_ALLOWED_GROUP_IDS: ["old", "keep", "old"]})

    revision_token = integrity._ACTIVE_CONFIG_REVISION.set("active-rev")
    group_token = integrity._ACTIVE_GROUP_MUTATION.set(("old", "new"))
    try:
        result = wrapped(None, None, subentry, [], [])
    finally:
        integrity._ACTIVE_GROUP_MUTATION.reset(group_token)
        integrity._ACTIVE_CONFIG_REVISION.reset(revision_token)

    assert result["expected_revision"] == "active-rev"
    assert result["extra_updates"][integrity.CONF_GUEST_ALLOWED_GROUP_IDS] == [
        "new",
        "keep",
    ]

    group_token = integrity._ACTIVE_GROUP_MUTATION.set(("old", None))
    try:
        result = wrapped(
            None,
            None,
            subentry,
            [],
            [],
            extra_updates={"other": True},
            expected_revision="explicit",
        )
    finally:
        integrity._ACTIVE_GROUP_MUTATION.reset(group_token)
    assert result["expected_revision"] == "explicit"
    assert result["extra_updates"] == {
        "other": True,
        integrity.CONF_GUEST_ALLOWED_GROUP_IDS: ["keep"],
    }


@pytest.mark.asyncio
async def test_management_wrapper_preserves_auth_boundary_and_context(monkeypatch) -> None:
    observations = []

    async def original(hass, user_id, is_admin, message):
        observations.append(
            (
                integrity._ACTIVE_CONFIG_REVISION.get(),
                integrity._ACTIVE_GROUP_MUTATION.get(),
            )
        )
        return {"ok": True}

    wrapped = integrity.wrap_management_command(original)

    assert await wrapped(
        SimpleNamespace(),
        "user",
        False,
        {"section": "tools", "action": "delete"},
    ) == {"ok": True}
    assert observations[-1] == (None, None)

    subentry = SimpleNamespace(data={})
    monkeypatch.setattr(
        integrity.management_ui,
        "entry_and_agent",
        lambda hass, entry_id, subentry_id: (SimpleNamespace(), subentry),
    )
    require_revision = lambda subentry, revision: None
    monkeypatch.setattr(
        integrity.management_ui, "_require_agent_config_revision", require_revision
    )

    result = await wrapped(
        SimpleNamespace(),
        "admin",
        True,
        {
            "section": "tools",
            "action": "save_group",
            "entry_id": "entry",
            "subentry_id": "agent",
            "revision": "rev-1",
            "original_id": "old",
            "group": {"id": "new"},
        },
    )
    assert result == {"ok": True}
    assert observations[-1] == ("rev-1", ("old", "new"))
    assert integrity._ACTIVE_CONFIG_REVISION.get() is None
    assert integrity._ACTIVE_GROUP_MUTATION.get() is None

    await wrapped(
        SimpleNamespace(),
        "admin",
        True,
        {
            "section": "tools",
            "action": "delete_group",
            "entry_id": "entry",
            "subentry_id": "agent",
            "group_id": "gone",
        },
    )
    assert observations[-1] == (None, ("gone", None))


@pytest.mark.asyncio
async def test_management_wrapper_validates_request_rules_before_persist(monkeypatch) -> None:
    original = AsyncMock(return_value={"ok": True})
    wrapped = integrity.wrap_management_command(original)
    subentry = SimpleNamespace(data={"tools": []})
    candidate = {"id": "rule", "action": {"actions": []}}
    monkeypatch.setattr(
        integrity.management_ui,
        "entry_and_agent",
        lambda hass, entry_id, subentry_id: (SimpleNamespace(), subentry),
    )
    monkeypatch.setattr(
        integrity.management_ui,
        "_prepare_request_rule",
        lambda rule, rule_id: candidate,
    )
    monkeypatch.setattr(
        integrity, "configured_function_tools_from_data", lambda data: []
    )
    validate = AsyncMock()
    monkeypatch.setattr(integrity, "async_validate_request_rule_functions", validate)

    result = await wrapped(
        SimpleNamespace(),
        "admin",
        True,
        {
            "section": "request_rules",
            "action": "update",
            "entry_id": "entry",
            "subentry_id": "agent",
            "rule_id": "rule",
            "rule": {"name": "Rule"},
        },
    )

    assert result == {"ok": True}
    validate.assert_awaited_once_with(SimpleNamespace(), candidate, [])
    original.assert_awaited_once()
