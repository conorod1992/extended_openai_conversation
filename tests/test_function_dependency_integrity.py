"""Regression coverage for Function Tool and Request Rule dependency integrity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_GUEST_ALLOWED_GROUP_IDS,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    async_rename_function_reference_recursive,
    async_validate_request_rule_functions,
    async_validate_static_function_arguments,
    recursive_function_references,
    wrap_management_command,
    wrap_persist_function_configuration,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRules,
)


def _call(function: str = "nested_tool", arguments: dict | None = None) -> dict:
    return {
        "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
        "data": {"function": function, "arguments": arguments or {}},
    }


def _nested_rule() -> dict:
    return {
        "id": "rule-1",
        "name": "Nested functions",
        "enabled": True,
        "phrases": ["do nested work"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [
                {"choose": [{"sequence": [_call()]}]},
                {"repeat": {"sequence": [_call()]}},
                {"parallel": [_call()]},
                {"sequence": [_call()]},
            ],
            "success_response": "Done",
            "failure_response": "Failed",
        },
        "matching_behavior": "defaults",
        "matching": DEFAULT_MATCHING,
        "order": 0,
    }


def _tool(parameters: dict | None = None) -> dict:
    return {
        "spec": {
            "name": "nested_tool",
            "description": "Nested test tool",
            "parameters": parameters
            or {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def test_nested_function_references_cover_all_script_branches() -> None:
    manager = SimpleNamespace(_rules=[_nested_rule()])
    assert recursive_function_references(manager, "nested_tool") == [
        {"id": "rule-1", "name": "Nested functions"}
    ]
    assert recursive_function_references(manager, "other_tool") == []


async def test_nested_function_rename_updates_every_reference_and_rolls_back_on_save_error(
    monkeypatch,
) -> None:
    manager = RequestRules(SimpleNamespace(async_save=AsyncMock()))
    manager._initialized = True
    manager._rules = [_nested_rule()]
    manager._sort_and_compile()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.validate_rule",
        lambda value: value,
    )

    revision = manager.revision()
    changed = await async_rename_function_reference_recursive(
        manager,
        "nested_tool",
        "renamed_tool",
        expected_revision=revision,
    )
    assert changed == 4
    assert recursive_function_references(manager, "nested_tool") == []
    assert recursive_function_references(manager, "renamed_tool") == [
        {"id": "rule-1", "name": "Nested functions"}
    ]

    manager._store.async_save = AsyncMock(side_effect=OSError("disk full"))
    before = manager.revision()
    with pytest.raises(OSError, match="disk full"):
        await async_rename_function_reference_recursive(
            manager,
            "renamed_tool",
            "third_tool",
            expected_revision=before,
        )
    assert recursive_function_references(manager, "renamed_tool") == [
        {"id": "rule-1", "name": "Nested functions"}
    ]
    assert recursive_function_references(manager, "third_tool") == []


async def test_static_request_rule_arguments_use_full_nested_function_schema(hass) -> None:
    parameters = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["safe", "fast"]},
            "payload": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 1, "maximum": 3},
                    "code": {"type": "string", "pattern": "^[A-Z]{2}$"},
                },
                "required": ["count", "code"],
                "additionalProperties": False,
            },
        },
        "required": ["mode", "payload"],
        "additionalProperties": False,
    }
    spec = _tool(parameters)["spec"]

    with pytest.raises(HomeAssistantError, match="at least 1"):
        await async_validate_static_function_arguments(
            hass,
            spec,
            {"mode": "safe", "payload": {"count": 0, "code": "AB"}},
        )
    with pytest.raises(HomeAssistantError, match="Unknown function input"):
        await async_validate_static_function_arguments(
            hass,
            spec,
            {
                "mode": "safe",
                "payload": {"count": 1, "code": "AB", "extra": True},
            },
        )
    with pytest.raises(HomeAssistantError, match="required pattern"):
        await async_validate_static_function_arguments(
            hass,
            spec,
            {"mode": "safe", "payload": {"count": 1, "code": "bad"}},
        )


async def test_dynamic_request_rule_leaf_defers_only_that_leaf(hass) -> None:
    parameters = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 1},
            "mode": {"type": "string", "enum": ["safe", "fast"]},
        },
        "required": ["count", "mode"],
        "additionalProperties": False,
    }
    spec = _tool(parameters)["spec"]

    await async_validate_static_function_arguments(
        hass, spec, {"count": "{{ captured_count }}", "mode": "safe"}
    )
    with pytest.raises(HomeAssistantError, match="one of its choices"):
        await async_validate_static_function_arguments(
            hass, spec, {"count": "{{ captured_count }}", "mode": "invalid"}
        )
    with pytest.raises(HomeAssistantError, match="Missing required function input"):
        await async_validate_static_function_arguments(
            hass, spec, {"count": "{{ captured_count }}"}
        )


async def test_nested_rule_validation_rejects_missing_or_invalid_function_calls(hass) -> None:
    parameters = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 1}},
        "required": ["count"],
        "additionalProperties": False,
    }
    tool = _tool(parameters)
    rule = _nested_rule()
    rule["action"]["actions"][0]["choose"][0]["sequence"][0] = _call(
        arguments={"count": 0}
    )
    with pytest.raises(HomeAssistantError, match="at least 1"):
        await async_validate_request_rule_functions(hass, rule, [tool])

    rule["action"]["actions"][0]["choose"][0]["sequence"][0] = _call(
        "missing_tool", {"count": 1}
    )
    with pytest.raises(HomeAssistantError, match="missing_tool"):
        await async_validate_request_rule_functions(hass, rule, [tool])


async def test_tool_mutations_reject_stale_loaded_revision(hass, monkeypatch) -> None:
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=agent_config_defaults(),
    )
    entry = SimpleNamespace(entry_id="entry-1", domain=DOMAIN, subentries={"agent-1": subentry})
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_args: (entry, subentry))
    original = AsyncMock(return_value={"ok": True})
    command = wrap_management_command(original)

    for action in ("save", "set_enabled", "delete", "save_group", "delete_group"):
        with pytest.raises(HomeAssistantError, match="Configuration changed in another tab"):
            await command(
                hass,
                "admin",
                True,
                {
                    "section": "tools",
                    "action": action,
                    "entry_id": "entry-1",
                    "subentry_id": "agent-1",
                    "revision": "stale",
                },
            )
    original.assert_not_awaited()


@pytest.mark.parametrize(
    ("action", "message", "expected_ids"),
    [
        (
            "save_group",
            {"original_id": "old_group", "group": {"id": "new_group"}},
            ["new_group", "other_group"],
        ),
        (
            "delete_group",
            {"group_id": "old_group"},
            ["other_group"],
        ),
    ],
)
async def test_group_dependency_update_shares_config_revision_and_write(
    hass, monkeypatch, action, message, expected_ids
) -> None:
    data = agent_config_defaults()
    data[CONF_GUEST_ALLOWED_GROUP_IDS] = ["old_group", "other_group"]
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=data,
    )
    entry = SimpleNamespace(entry_id="entry-1", domain=DOMAIN, subentries={"agent-1": subentry})
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_args: (entry, subentry))
    revision = management_ui._agent_config_revision(subentry.data, subentry.title)
    seen: dict = {}

    def persist(
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
        return {"revision": "next-revision"}

    monkeypatch.setattr(
        management_ui,
        "_persist_function_configuration",
        wrap_persist_function_configuration(persist),
    )

    async def original(_hass, _user_id, _is_admin, _message):
        return management_ui._persist_function_configuration(
            hass, entry, subentry, [], []
        )

    command = wrap_management_command(original)
    result = await command(
        hass,
        "admin",
        True,
        {
            "section": "tools",
            "action": action,
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "revision": revision,
            **message,
        },
    )

    assert seen["expected_revision"] == revision
    assert seen["extra_updates"][CONF_GUEST_ALLOWED_GROUP_IDS] == expected_ids
    assert result["revision"] == "next-revision"
