"""Due-time Function authorization against live config and HA state mutations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.backup import (
    async_collect_backup_snapshot,
    async_restore_backup,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_MODE_ENABLED,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DATA_DELAYED_TOOL_MANAGER,
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    async_get_guest_mode,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_delayed_tool_due_reauthorization import (
    _ENTITY_ID,
    _execute_now,
    _restricted_user,
    _schedule_delayed_call,
    _set_control_permission,
)
from tests_stress.conftest import record

MUTATIONS = (
    "tool_disabled",
    "tool_deleted",
    "group_disabled",
    "group_detached",
    "guest_enabled",
    "guest_policy_edited",
    "backup_restore_removed_tool",
    "entity_unexposed",
    "entity_removed",
    "entity_unavailable",
    "service_removed",
    "permission_revoked",
    "user_deactivated",
    "entry_reloaded",
    "entry_unloaded_and_setup",
)
SURVIVES = {"group_detached", "entry_reloaded", "entry_unloaded_and_setup"}


@pytest.mark.parametrize("mutation", MUTATIONS)
async def test_delayed_tool_uses_live_state_at_due_time(
    hass: HomeAssistant,
    mutation: str,
    stress_trace: list[dict],
) -> None:
    user = _restricted_user()
    user.add_to_hass(hass)
    calls: list[Any] = []

    async def turn_off(call: Any) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set(_ENTITY_ID, "on")
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    tool = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    group = {
        "id": "delayed-mutation-group",
        "name": "Delayed mutation group",
        "description": "One native service tool",
        "loading_mode": "always",
        "functions": ["execute_services"],
        "enabled": True,
    }
    grouped = mutation in {"group_disabled", "group_detached"}
    entry = _make_entry(
        f"Delayed mutation {mutation}",
        include_ai_task=False,
        conversation_options={
            CONF_FUNCTION_TOOLS: [tool],
            CONF_FUNCTION_GROUPS: [group] if grouped else [],
            CONF_GUEST_MODE_ENABLED: True,
            CONF_GUEST_FUNCTION_POLICY: "on"
            if mutation == "guest_policy_edited"
            else "off",
        },
    )
    await _setup_entry(hass, entry)
    subentry = next(iter(entry.subentries.values()))
    options = normalize_agent_config(dict(subentry.data))
    hass.config_entries.async_update_subentry(entry, subentry, data=options)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    manager = hass.data[DOMAIN][DATA_DELAYED_TOOL_MANAGER]
    assert isinstance(manager, DelayedToolManager)
    if mutation == "guest_policy_edited":
        guest = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
        assert guest is not None
        await guest.async_update_trusted(indefinite=True)
        assert guest.is_active()
    call_id = await _schedule_delayed_call(
        agent, manager, user, f"call-delayed-{mutation}"
    )
    assert calls == []

    if mutation in {
        "tool_disabled",
        "tool_deleted",
        "group_disabled",
        "group_detached",
    }:
        options = dict(entry.subentries[subentry.subentry_id].data)
        if mutation == "tool_disabled":
            updated_tool = deepcopy(tool)
            updated_tool["enabled"] = False
            options[CONF_FUNCTION_TOOLS] = [updated_tool]
        elif mutation == "tool_deleted":
            options[CONF_FUNCTION_TOOLS] = []
        elif mutation == "group_disabled":
            updated_group = dict(group)
            updated_group["enabled"] = False
            options[CONF_FUNCTION_GROUPS] = [updated_group]
        else:
            # Detaching leaves the enabled tool standalone; it should still run.
            options[CONF_FUNCTION_GROUPS] = []
        hass.config_entries.async_update_subentry(
            entry,
            entry.subentries[subentry.subentry_id],
            data=normalize_agent_config(options),
        )
        await hass.async_block_till_done()
    elif mutation == "guest_enabled":
        guest = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
        assert guest is not None
        await guest.async_update_trusted(indefinite=True)
        assert guest.is_active()
    elif mutation == "guest_policy_edited":
        options = dict(entry.subentries[subentry.subentry_id].data)
        options[CONF_GUEST_FUNCTION_POLICY] = "off"
        hass.config_entries.async_update_subentry(
            entry,
            entry.subentries[subentry.subentry_id],
            data=normalize_agent_config(options),
        )
        await hass.async_block_till_done()
    elif mutation == "backup_restore_removed_tool":
        snapshot = await async_collect_backup_snapshot(
            hass, entry, entry.subentries[subentry.subentry_id]
        )
        snapshot["agent"]["config"][CONF_FUNCTION_TOOLS] = []
        result = await async_restore_backup(
            hass, entry, entry.subentries[subentry.subentry_id], snapshot
        )
        assert result["status"] == "restored"
        await hass.async_block_till_done()
    elif mutation == "entity_unexposed":
        async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, False)
    elif mutation == "entity_removed":
        hass.states.async_remove(_ENTITY_ID)
    elif mutation == "entity_unavailable":
        hass.states.async_set(_ENTITY_ID, "unavailable")
    elif mutation == "service_removed":
        hass.services.async_remove("light", "turn_off")
    elif mutation == "permission_revoked":
        _set_control_permission(user, allowed=False)
        assert not user.permissions.check_entity(_ENTITY_ID, POLICY_CONTROL)
    elif mutation == "user_deactivated":
        await hass.auth.async_update_user(user, is_active=False)
        await hass.async_block_till_done()
        assert not user.is_active
    elif mutation == "entry_reloaded":
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    elif mutation == "entry_unloaded_and_setup":
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    await _execute_now(hass, manager, call_id)
    assert len(calls) == (1 if mutation in SURVIVES else 0), mutation
    assert manager._records == {}
    record(
        stress_trace,
        "summary",
        layer="Real HA",
        delayed_tool_mutation_cases=1,
        delayed_tool_due_executions=len(calls),
        mutation=mutation,
    )
