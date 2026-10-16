"""Tests for administrator Function Tool and Function Group state actions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import guest_mode
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_CALL_FUNCTION,
    SERVICE_DISABLE_FUNCTION_TOOLS,
    SERVICE_ENABLE_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.services import (
    SERVICE_DISABLE_FUNCTION_GROUPS,
    SERVICE_ENABLE_FUNCTION_GROUPS,
    async_setup_services,
    async_skill_source_ref,
)
from homeassistant.exceptions import HomeAssistantError


def _tool(name: str) -> dict:
    return {
        "spec": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_energy"},
    }


def _group(group_id: str, functions: list[str]) -> dict:
    return {
        "id": group_id,
        "name": group_id.title(),
        "description": f"Functions for {group_id}",
        "loading_mode": "on_demand",
        "functions": functions,
    }


@pytest.mark.parametrize("policy_version", [None, 2])
@pytest.mark.parametrize(
    ("service", "field", "target", "enabled"),
    [
        (SERVICE_ENABLE_FUNCTION_TOOLS, "functions", "energy", True),
        (SERVICE_DISABLE_FUNCTION_TOOLS, "functions", "energy", False),
        (SERVICE_ENABLE_FUNCTION_GROUPS, "function_groups", "energy_group", True),
        (SERVICE_DISABLE_FUNCTION_GROUPS, "function_groups", "energy_group", False),
    ],
)
async def test_function_state_services_preserve_unrelated_configuration_and_guest_policy(
    hass, monkeypatch, policy_version, service, field, target, enabled
):
    tool = {**_tool("energy"), "enabled": not enabled, "guest_allowed": True}
    group = {
        **_group("energy_group", ["energy"]),
        "enabled": not enabled,
        "guest_allowed": True,
    }
    original = {
        "functions": yaml.safe_dump([tool]),
        "function_groups": [group],
        "guest_mode_enabled": True,
        "guest_readable_entities": ["light.guest"],
        "guest_controllable_entities": ["light.guest"],
        "guest_shared_memory_read": True,
        "guest_knowledge_enabled": True,
        "future_setting": {"preserve": "exactly"},
    }
    if policy_version is not None:
        original.update(
            guest_policy_version=policy_version,
            guest_excluded_entities=["lock.front_door"],
            guest_shared_memory_policy="read_only",
            guest_knowledge_policy="on",
        )
    subentry = SimpleNamespace(
        subentry_id="agent-1", subentry_type="conversation", data=deepcopy(original)
    )
    entry = SimpleNamespace(
        entry_id="entry-1", domain=DOMAIN, subentries={"agent-1": subentry}
    )
    hass.config_entries.async_get_entry.return_value = entry
    registry = SimpleNamespace(async_get=lambda _: None)
    for module in (guest_mode.er, guest_mode.dr, guest_mode.ar):
        monkeypatch.setattr(module, "async_get", lambda _: registry)
    exposed = [{"entity_id": "light.guest"}, {"entity_id": "lock.front_door"}]
    hass.states.async_all.return_value = [
        SimpleNamespace(entity_id=item["entity_id"]) for item in exposed
    ]
    active = SimpleNamespace(is_active=lambda: True)
    before = guest_mode.resolve_guest_policy(
        hass, subentry.data, active, [tool], exposed_entities=exposed
    )

    def apply_update(_entry, _subentry, *, data):
        subentry.data = data

    hass.config_entries.async_update_subentry.side_effect = apply_update
    await async_setup_services(hass, {})
    handler = next(
        call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[:2] == (DOMAIN, service)
    )
    await handler(
        SimpleNamespace(
            data={"config_entry": "entry-1", "agent_id": "agent-1", field: [target]}
        )
    )
    after = guest_mode.resolve_guest_policy(
        hass, subentry.data, active, [tool], exposed_entities=exposed
    )
    assert before.readable_entity_ids == frozenset({"light.guest"})
    assert after.readable_entity_ids == before.readable_entity_ids
    assert after.controllable_entity_ids == before.controllable_entity_ids
    assert after.shared_memory_read == before.shared_memory_read
    assert after.knowledge_access == before.knowledge_access
    assert {key: value for key, value in subentry.data.items() if key != field} == {
        key: value for key, value in original.items() if key != field
    }
    changed = (
        yaml.safe_load(subentry.data[field])
        if field == "functions"
        else subentry.data[field]
    )
    assert changed[0]["enabled"] is enabled


async def test_enable_disable_actions_update_one_or_multiple_tools(
    hass, monkeypatch
) -> None:
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        data={"functions": yaml.safe_dump([_tool("one"), _tool("two")])},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        subentries={"agent-1": subentry},
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.er.async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _entity_id: None),
    )

    def apply_update(_entry, _subentry, *, data):
        subentry.data = data

    hass.config_entries.async_update_subentry.side_effect = apply_update
    await async_setup_services(hass, {})
    handlers = {
        call.args[1]: call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[0] == DOMAIN
    }

    await handlers[SERVICE_DISABLE_FUNCTION_TOOLS](
        SimpleNamespace(
            data={
                "config_entry": "entry-1",
                "agent_id": "agent-1",
                "functions": ["one", "two"],
            }
        )
    )
    disabled = yaml.safe_load(subentry.data["functions"])
    assert [tool["enabled"] for tool in disabled] == [False, False]

    await handlers[SERVICE_ENABLE_FUNCTION_TOOLS](
        SimpleNamespace(
            data={
                "config_entry": "entry-1",
                "agent_id": "agent-1",
                "functions": ["two"],
            }
        )
    )
    enabled = yaml.safe_load(subentry.data["functions"])
    assert [tool["enabled"] for tool in enabled] == [False, True]

    hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=False)
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await handlers[SERVICE_ENABLE_FUNCTION_TOOLS](
            SimpleNamespace(
                context=SimpleNamespace(user_id="non-admin"),
                data={
                    "config_entry": "entry-1",
                    "agent_id": "agent-1",
                    "functions": ["one"],
                },
            )
        )


async def test_group_actions_do_not_change_member_tool_state(hass, monkeypatch) -> None:
    one = _tool("one")
    two = {**_tool("two"), "enabled": False}
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        data={
            "functions": yaml.safe_dump([one, two]),
            "function_groups": [_group("optional", ["one", "two"])],
        },
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        subentries={"agent-1": subentry},
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.er.async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _entity_id: None),
    )

    def apply_update(_entry, _subentry, *, data):
        subentry.data = data

    hass.config_entries.async_update_subentry.side_effect = apply_update
    await async_setup_services(hass, {})
    handlers = {
        call.args[1]: call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[0] == DOMAIN
    }

    await handlers[SERVICE_DISABLE_FUNCTION_GROUPS](
        SimpleNamespace(
            data={
                "config_entry": "entry-1",
                "agent_id": "agent-1",
                "function_groups": ["optional"],
            }
        )
    )
    assert subentry.data["function_groups"][0]["enabled"] is False
    stored_tools = yaml.safe_load(subentry.data["functions"])
    assert [tool.get("enabled", True) for tool in stored_tools] == [True, False]

    await handlers[SERVICE_ENABLE_FUNCTION_GROUPS](
        SimpleNamespace(
            data={
                "config_entry": "entry-1",
                "agent_id": "agent-1",
                "function_groups": ["optional"],
            }
        )
    )
    assert subentry.data["function_groups"][0]["enabled"] is True
    stored_tools = yaml.safe_load(subentry.data["functions"])
    assert [tool.get("enabled", True) for tool in stored_tools] == [True, False]


async def test_skill_source_ref_defaults_to_installed_release(monkeypatch) -> None:
    async def integration(_hass, _domain):
        return SimpleNamespace(version="6.8.3")

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_get_integration",
        integration,
    )
    assert await async_skill_source_ref(SimpleNamespace()) == "6.8.3"
    assert await async_skill_source_ref(SimpleNamespace(), "develop") == "develop"


async def test_call_function_action_uses_request_scoped_bridge(
    hass, monkeypatch
) -> None:
    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_call_active_function",
        execute,
    )
    await async_setup_services(hass, {})
    handler = next(
        call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[:2] == (DOMAIN, SERVICE_CALL_FUNCTION)
    )
    result = await handler(
        SimpleNamespace(data={"function": "remember", "arguments": {"fact": "Tuesday"}})
    )
    assert result == {"result": {"ok": True}}
    execute.assert_awaited_once_with("remember", {"fact": "Tuesday"})
