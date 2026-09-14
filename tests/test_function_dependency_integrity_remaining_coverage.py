"""Residual coverage for Function Tool dependency integrity defensive branches."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_dependency_integrity as integrity,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_GUEST_ALLOWED_GROUP_IDS,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)


@pytest.mark.parametrize(
    ("value", "schema", "expected"),
    [
        (
            {"known": "{{ dynamic }}", "extra": "static"},
            {
                "type": "object",
                "properties": {"known": {"type": "string"}},
                "additionalProperties": True,
            },
            {
                "type": "object",
                "properties": {"known": {}},
                "additionalProperties": True,
            },
        ),
        (
            {"extra": "{{ dynamic }}"},
            {"type": "object", "additionalProperties": True},
            {"type": "object", "additionalProperties": True},
        ),
    ],
)
def test_mask_dynamic_schema_object_defensive_shapes(value, schema, expected) -> None:
    assert integrity._mask_dynamic_schema(value, schema) == expected


def test_mask_dynamic_schema_leaves_non_array_value_unchanged() -> None:
    schema = {
        "type": "array",
        "items": {"type": "string"},
        "uniqueItems": True,
    }

    result = integrity._mask_dynamic_schema({"x": "{{ dynamic }}"}, schema)

    assert result == schema


async def test_static_descendant_traversal_validates_each_concrete_object_child(
    hass, monkeypatch
) -> None:
    validate = AsyncMock()
    monkeypatch.setattr(integrity, "async_validate_function_arguments", validate)
    schema = {
        "type": "object",
        "properties": {
            "first": {"type": "integer"},
            "dynamic": {"type": "string"},
            "second": {"type": "integer"},
        },
        "additionalProperties": {"type": "integer"},
    }
    value = {
        "first": 1,
        "dynamic": "{{ captured }}",
        "second": 2,
        "extra": 3,
    }

    await integrity._async_validate_static_descendants(hass, value, schema)

    assert validate.await_count == 3
    validated_values = [call.args[2]["value"] for call in validate.await_args_list]
    assert validated_values == [1, 2, 3]


@pytest.mark.parametrize(
    ("value", "schema"),
    [
        ("{{ dynamic }}", {"type": "array", "items": {"type": "string"}}),
        ({"x": "{{ dynamic }}"}, {"type": "array", "items": {"type": "string"}}),
        (["{{ dynamic }}", "static"], {"type": "array", "items": True}),
    ],
)
async def test_static_descendant_traversal_exits_for_non_recursive_shapes(
    hass, monkeypatch, value, schema
) -> None:
    validate = AsyncMock()
    monkeypatch.setattr(integrity, "async_validate_function_arguments", validate)

    await integrity._async_validate_static_descendants(hass, value, schema)

    validate.assert_not_awaited()


class _ReadOnlyAction(Mapping[str, object]):
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


async def test_function_rename_rejects_immutable_matching_action(monkeypatch) -> None:
    action = _ReadOnlyAction(
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": "old_name", "arguments": {}},
        }
    )
    monkeypatch.setattr(
        integrity, "_rule_script_actions", lambda _rule: iter((action,))
    )
    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _rules=[{"id": "rule", "name": "Rule"}],
        _require_revision_locked=Mock(),
        _sort_and_compile=Mock(),
        _async_save_locked=AsyncMock(),
    )

    with pytest.raises(ValueError, match="Request Rule action is not mutable"):
        await integrity.async_rename_function_reference_recursive(
            manager, "old_name", "new_name"
        )

    manager._async_save_locked.assert_not_awaited()
    manager._sort_and_compile.assert_not_called()


@pytest.mark.parametrize(
    "guest_group_ids",
    [pytest.param(("old",), id="tuple"), pytest.param("old", id="string")],
)
def test_guest_group_mutation_ignores_non_list_saved_ids(guest_group_ids) -> None:
    seen: dict[str, object] = {}

    def original(
        _hass,
        _entry,
        _subentry,
        _tools,
        _groups,
        *,
        extra_updates=None,
        expected_revision=None,
    ):
        seen["extra_updates"] = extra_updates
        seen["expected_revision"] = expected_revision
        return {"ok": True}

    subentry = SimpleNamespace(data={CONF_GUEST_ALLOWED_GROUP_IDS: guest_group_ids})
    wrapped = integrity.wrap_persist_function_configuration(original)
    token = integrity._ACTIVE_GROUP_MUTATION.set(("old", "new"))
    try:
        result = wrapped(object(), object(), subentry, [], [])
    finally:
        integrity._ACTIVE_GROUP_MUTATION.reset(token)

    assert result == {"ok": True}
    assert seen["extra_updates"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {
            "section": "request_rules",
            "action": "create",
            "entry_id": 123,
            "subentry_id": "agent",
            "rule": {},
        },
        {
            "section": "request_rules",
            "action": "update",
            "entry_id": "entry",
            "subentry_id": None,
            "rule": {},
        },
        {
            "section": "tools",
            "action": "save",
            "entry_id": None,
            "subentry_id": "agent",
        },
        {
            "section": "tools",
            "action": "delete_group",
            "entry_id": "entry",
            "subentry_id": 456,
        },
    ],
)
async def test_management_mutations_with_invalid_ids_delegate_unchanged(
    hass, monkeypatch, message
) -> None:
    expected = {"delegated": True}
    original = AsyncMock(return_value=expected)
    wrapped = integrity.wrap_management_command(original)
    entry_and_agent = Mock(side_effect=AssertionError("IDs should not be resolved"))
    validate = AsyncMock(
        side_effect=AssertionError("dependency validation should be skipped")
    )
    require_revision = Mock(
        side_effect=AssertionError("revision should not be checked")
    )
    monkeypatch.setattr(integrity.management_ui, "entry_and_agent", entry_and_agent)
    monkeypatch.setattr(integrity, "async_validate_request_rule_functions", validate)
    monkeypatch.setattr(
        integrity.management_ui, "_require_agent_config_revision", require_revision
    )

    result = await wrapped(hass, "admin", True, message)

    assert result is expected
    original.assert_awaited_once_with(hass, "admin", True, message)
    entry_and_agent.assert_not_called()
    validate.assert_not_awaited()
    require_revision.assert_not_called()
