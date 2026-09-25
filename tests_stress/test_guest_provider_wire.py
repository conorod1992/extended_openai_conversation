"""Guest authorization at the public Assist and actual provider-wire boundary."""

from __future__ import annotations

import asyncio
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
from homeassistant.auth.models import Group
from homeassistant.auth.permissions.const import (
    CAT_ENTITIES,
    POLICY_CONTROL,
    POLICY_READ,
)
from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_tool_call,
    _chat_tool_result,
    _tool_names,
)
from tests_real_ha.test_memory_provider_wire_e2e import _memory_agent
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _speech,
)
from tests_stress.conftest import record

_OWNER = "enhanced-guest-owner"
_PRIVATE = "GUEST-PRIVATE-PERSISTENT-東京"
_TEMPORARY = "GUEST-PRIVATE-TEMPORARY-東京"
_KNOWLEDGE = "GUEST-KNOWLEDGE-東京"
_DENIED_KNOWLEDGE = "GUEST-DENIED-KNOWLEDGE-東京"
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


def _native_tool_result(body: dict, call_id: str):
    """Native tools serialize a JSON list, unlike the Knowledge tool payload."""
    message = next(
        item
        for item in body["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == call_id
    )
    return json.loads(message["content"])["result"]


@pytest.mark.parametrize("function_policy", ["off", "on", "custom"])
async def test_guest_wire_only_subtracts_private_context_and_function_capabilities(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    function_policy: str,
    stress_trace: list[dict],
) -> None:
    def permission_group(control: bool) -> Group:
        entity_policy = {POLICY_READ: True}
        if control:
            entity_policy[POLICY_CONTROL] = True
        return Group(
            id=f"guest-matrix-{'control' if control else 'read'}",
            name="Guest matrix",
            policy={
                CAT_ENTITIES: {ENTITY_ENTITY_IDS: {"light.guest_matrix": entity_policy}}
            },
        )

    user = MockUser(
        id=_OWNER,
        name="Guest matrix user",
        is_owner=False,
        groups=[permission_group(True)],
    )
    user.add_to_hass(hass)
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
    await agent._memory.async_add(
        _OWNER, f"The calibration token is {_PRIVATE}.", "preferences", "explicit"
    )
    await agent._temporary_memory.async_add(
        f"user:{_OWNER}",
        f"The temporary calibration token is {_TEMPORARY}.",
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "acceptance",
        owner_scope_id=f"user:{_OWNER}",
    )
    knowledge = await agent._knowledge.async_create(
        "Guest test knowledge", "Guest policy fixture", _KNOWLEDGE
    )
    await agent._knowledge.async_create(
        "Denied guest knowledge", "Guest policy fixture", _DENIED_KNOWLEDGE
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
    await agent._request_rules.async_create(
        {
            "name": "Guest route continuation",
            "phrases": ["guest route probe"],
            "match_type": "equals",
            "action_type": "model_routing",
            "action": {
                "model": "gpt-5.6",
                "scope": "request",
                "continue_to_ai": True,
            },
        }
    )

    calls = []

    async def turn_off(call):
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.guest_matrix", "on")
    async_expose_entity(hass, conversation.DOMAIN, "light.guest_matrix", True)
    assert user.permissions.check_entity("light.guest_matrix", POLICY_CONTROL)

    owner_wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Owner context")])
    assert (
        _speech(await _say(hass, entry.entry_id, "What is my calibration token?"))
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
        _speech(await _say(hass, entry.entry_id, "What is my calibration token?"))
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

    knowledge_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-guest-knowledge",
                "knowledge_search",
                {"query": "Guest policy fixture", "limit": 5},
            ),
            _chat_sse_text("Allowed knowledge found"),
        ],
    )
    assert (
        _speech(await _say(hass, entry.entry_id, "Search Guest policy fixture"))
        == "Allowed knowledge found"
    )
    assert len(knowledge_wire.requests) == 2
    search_result = json.dumps(
        _chat_tool_result(knowledge_wire.requests[1]["body"], "call-guest-knowledge"),
        ensure_ascii=False,
    )
    assert _KNOWLEDGE in search_result
    assert _DENIED_KNOWLEDGE not in search_result
    assert _PRIVATE not in json.dumps(
        knowledge_wire.requests[0]["body"], ensure_ascii=False
    )

    routed_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Routed in Guest Mode")]
    )
    assert (
        _speech(await _say(hass, entry.entry_id, "guest route probe"))
        == "Routed in Guest Mode"
    )
    assert routed_wire.requests[0]["body"]["model"] == "gpt-5.6"
    assert _PRIVATE not in json.dumps(
        routed_wire.requests[0]["body"], ensure_ascii=False
    )

    executed_functions = 1  # Knowledge search
    provider_requests = 6
    public_turns = 5
    if function_policy != "off":
        tool_wire = _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call(
                    "call-guest-load", "load_function_groups", {"groups": [_GROUP]}
                ),
                _chat_sse_tool_call(
                    "call-guest-service",
                    "execute_services",
                    {
                        "list": [
                            {
                                "domain": "light",
                                "service": "turn_off",
                                "service_data": {"entity_id": ["light.guest_matrix"]},
                            }
                        ]
                    },
                ),
                _chat_sse_text("Guest safe control complete"),
            ],
        )
        assert (
            _speech(await _say(hass, entry.entry_id, "Turn off the guest matrix light"))
            == "Guest safe control complete"
        )
        assert len(tool_wire.requests) == 3
        assert "execute_services" in _tool_names(
            tool_wire.requests[1]["body"], API_MODE_CHAT_COMPLETIONS
        )
        assert (
            _chat_tool_result(tool_wire.requests[1]["body"], "call-guest-load")[
                "status"
            ]
            == "success"
        )
        service_result = _native_tool_result(
            tool_wire.requests[2]["body"], "call-guest-service"
        )
        assert service_result[0]["success"] is True
        assert len(calls) == 1
        executed_functions = 2  # loader and the actual HA service Function
        provider_requests += 3
        public_turns += 1

        # The same function remains configured and advertised, but live HA
        # permissions now veto execution. Replacing groups invalidates HA's
        # cached permissions; mutating the list in place would not.
        user.groups = [permission_group(False)]
        assert not user.permissions.check_entity("light.guest_matrix", POLICY_CONTROL)
        denied_wire = _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call(
                    "call-denied-load", "load_function_groups", {"groups": [_GROUP]}
                ),
                _chat_sse_tool_call(
                    "call-denied-service",
                    "execute_services",
                    {
                        "list": [
                            {
                                "domain": "light",
                                "service": "turn_off",
                                "service_data": {"entity_id": ["light.guest_matrix"]},
                            }
                        ]
                    },
                ),
                _chat_sse_text("Control denied"),
            ],
        )
        assert (
            _speech(await _say(hass, entry.entry_id, "Try to turn off the light again"))
            == "Control denied"
        )
        assert len(denied_wire.requests) == 3
        denied_result = _native_tool_result(
            denied_wire.requests[2]["body"], "call-denied-service"
        )
        assert not any(
            item.get("success") is True
            for item in denied_result
            if isinstance(item, dict)
        )
        assert len(calls) == 1
        executed_functions += 1  # The loader ran; denied HA control did not.
        provider_requests += 3
        public_turns += 1

    await agent._guest_mode.async_disable_trusted()
    restored_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Owner restored")]
    )
    assert (
        _speech(await _say(hass, entry.entry_id, "What is my calibration token?"))
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
        public_turns=public_turns,
        provider_requests=provider_requests,
        actual_function_executions=executed_functions,
        native_function_executions=1 if function_policy != "off" else 0,
        ha_service_calls=len(calls),
    )


async def test_blocked_provider_request_keeps_guest_authorization_snapshot(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A committed Guest change affects the next turn, not an already sent one."""
    MockUser(id=_OWNER, name="Guest transition owner", is_owner=True).add_to_hass(hass)
    entry, agent = await _memory_agent(hass, API_MODE_CHAT_COMPLETIONS)
    await agent._memory.async_add(
        _OWNER,
        f"The transition calibration token is {_PRIVATE}.",
        "preferences",
        "explicit",
    )

    async def blocked_turn(label: str, change):
        wire = _install_wire(monkeypatch, agent, [_chat_sse_text(label)])
        real_send = wire.send
        entered = asyncio.Event()
        release = asyncio.Event()

        async def gated_send(request, *args, **kwargs):
            entered.set()  # SDK has already serialized the authorization snapshot.
            await release.wait()
            return await real_send(request, *args, **kwargs)

        monkeypatch.setattr(_raw_client(agent)._client, "send", gated_send)
        turn = asyncio.create_task(
            _say(hass, entry.entry_id, "What is my transition calibration token?")
        )
        await asyncio.wait_for(entered.wait(), 10)
        await change()
        release.set()
        assert _speech(await asyncio.wait_for(turn, 10)) == label
        assert len(wire.requests) == 1
        return json.dumps(wire.requests[0]["body"], ensure_ascii=False)

    owner_snapshot = await blocked_turn(
        "Owner snapshot",
        lambda: agent._guest_mode.async_update_trusted(indefinite=True),
    )
    assert _PRIVATE in owner_snapshot
    guest_wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Guest next turn")])
    assert (
        _speech(
            await _say(hass, entry.entry_id, "What is my transition calibration token?")
        )
        == "Guest next turn"
    )
    assert _PRIVATE not in json.dumps(
        guest_wire.requests[0]["body"], ensure_ascii=False
    )

    guest_snapshot = await blocked_turn(
        "Guest snapshot", agent._guest_mode.async_disable_trusted
    )
    assert _PRIVATE not in guest_snapshot
    restored_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Owner next turn")]
    )
    assert (
        _speech(
            await _say(hass, entry.entry_id, "What is my transition calibration token?")
        )
        == "Owner next turn"
    )
    assert _PRIVATE in json.dumps(restored_wire.requests[0]["body"], ensure_ascii=False)
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        guest_transition_snapshots=2,
        private_context_probes=4,
        public_turns=4,
        provider_requests=4,
    )
