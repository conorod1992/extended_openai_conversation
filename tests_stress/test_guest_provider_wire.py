"""Guest authorization at the public Assist and actual provider-wire boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_ALLOWED_FUNCTION_NAMES,
    CONF_GUEST_ALLOWED_GROUP_IDS,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_KNOWLEDGE_POLICY,
    CONF_GUEST_KNOWLEDGE_SOURCE_IDS,
    CONF_GUEST_POLICY_VERSION,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    DEFAULT_CONF_FUNCTION_TOOLS,
    GUEST_POLICY_VERSION,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import _tool_names
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_stress.conftest import record

_OWNER = "enhanced-guest-owner"
_PRIVATE = "GUEST-PRIVATE-PERSISTENT-東京"
_TEMPORARY = "GUEST-PRIVATE-TEMPORARY-東京"
_KNOWLEDGE = "GUEST-KNOWLEDGE-東京"
_GROUP = "guest-safe-group"


async def _say(hass: HomeAssistant, entry_id: str, text: str):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=_OWNER),
        language="en",
        agent_id=entry_id,
    )


@pytest.mark.parametrize("function_policy", ["off", "on", "custom"])
async def test_guest_wire_only_subtracts_private_context_and_function_capabilities(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    function_policy: str,
    stress_trace: list[dict],
) -> None:
    MockUser(id=_OWNER, name="Guest matrix owner", is_owner=True).add_to_hass(hass)
    safe = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    denied = {
        "spec": {
            "name": "guest_unsafe_template",
            "description": "Unsafe local template",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "UNSAFE-EXECUTED"},
        "enabled": True,
    }
    entry = _make_entry(
        f"Guest provider wire {function_policy}",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_FUNCTION_POLICY: function_policy,
            CONF_GUEST_ALLOWED_FUNCTION_NAMES: [
                "execute_services",
                "guest_unsafe_template",
            ],
            CONF_GUEST_ALLOWED_GROUP_IDS: [_GROUP],
            CONF_GUEST_KNOWLEDGE_POLICY: "custom",
            CONF_FUNCTION_TOOLS: [safe, denied],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": _GROUP,
                    "name": "Guest safe group",
                    "description": "Loads the safe HA control function",
                    "loading_mode": "on_demand",
                    "functions": ["execute_services"],
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    await agent._memory.async_add(_OWNER, _PRIVATE, "preferences", "explicit")
    await agent._temporary_memory.async_add(
        f"user:{_OWNER}",
        _TEMPORARY,
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "acceptance",
        owner_scope_id=f"user:{_OWNER}",
    )
    knowledge = await agent._knowledge.async_create(
        "Guest test knowledge", "Guest policy fixture", _KNOWLEDGE
    )
    # The custom allowlist uses the actual source ID, not a fabricated ID.
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    options = dict(subentry.data)
    options[CONF_GUEST_KNOWLEDGE_SOURCE_IDS] = [knowledge.source_id]
    hass.config_entries.async_update_subentry(entry, subentry, data=options)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    calls = []

    async def turn_off(call):
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.guest_matrix", "on")
    async_expose_entity(hass, conversation.DOMAIN, "light.guest_matrix", True)

    owner_wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Owner context")])
    assert (
        _speech(await _say(hass, entry.entry_id, "Read my private context"))
        == "Owner context"
    )
    assert len(owner_wire.requests) == 1
    owner_body = json.dumps(owner_wire.requests[0]["body"], ensure_ascii=False)
    assert _PRIVATE in owner_body and _TEMPORARY in owner_body
    assert "guest_unsafe_template" in _tool_names(
        owner_wire.requests[0]["body"], API_MODE_CHAT_COMPLETIONS
    )

    await agent._guest_mode.async_update_trusted(indefinite=True)
    guest_wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Guest context")])
    assert (
        _speech(await _say(hass, entry.entry_id, "Read my private context"))
        == "Guest context"
    )
    assert len(guest_wire.requests) == 1
    guest_body = json.dumps(guest_wire.requests[0]["body"], ensure_ascii=False)
    assert _PRIVATE not in guest_body and _TEMPORARY not in guest_body
    guest_tools = _tool_names(guest_wire.requests[0]["body"], API_MODE_CHAT_COMPLETIONS)
    assert "guest_unsafe_template" not in guest_tools
    assert guest_tools <= _tool_names(
        owner_wire.requests[0]["body"], API_MODE_CHAT_COMPLETIONS
    ) | {"guest_mode_restrict"}
    assert ("load_function_groups" in guest_tools) == (function_policy != "off")
    assert not calls
    assert len(await agent._memory.async_list(_OWNER)) == 1
    assert (
        len(
            await agent._temporary_memory.async_active(
                f"user:{_OWNER}", owner_scope_id=f"user:{_OWNER}"
            )
        )
        == 1
    )

    await agent._guest_mode.async_disable_trusted()
    restored_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Owner restored")]
    )
    assert (
        _speech(await _say(hass, entry.entry_id, "Read my private context"))
        == "Owner restored"
    )
    assert _PRIVATE in json.dumps(restored_wire.requests[0]["body"], ensure_ascii=False)
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        guest_policy=function_policy,
        guest_end_to_end_combinations=1,
        private_context_probes=3,
        public_turns=3,
        provider_requests=3,
        actual_function_executions=0,
        ha_service_calls=len(calls),
    )
