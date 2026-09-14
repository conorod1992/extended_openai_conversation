"""Real-HA acceptance for stable multi-layer authorization conflicts."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from homeassistant.auth.models import Group
from homeassistant.auth.permissions.const import CAT_ENTITIES, POLICY_CONTROL, POLICY_READ
from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_ALLOWED_FUNCTION_NAMES,
    CONF_GUEST_CONTROL_EXCLUDED_ENTITIES,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_MODE_ENABLED,
    CONF_GUEST_POLICY_VERSION,
    CONF_GUEST_SEPARATE_CONTROL_RESTRICTIONS,
    DEFAULT_CONF_FUNCTION_TOOLS,
    GUEST_POLICY_VERSION,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _speech,
)

_USER_ID = "multilayer-authorization-user"
_HA_DENIED = "light.ha_permission_denied"
_GUEST_DENIED = "light.guest_policy_denied"
_FULLY_ALLOWED = "light.multilayer_allowed"


def _restricted_user() -> MockUser:
    """Allow control of two entities while deliberately denying the third."""
    group = Group(
        id="multilayer-authorization-group",
        name="Multi-layer authorization acceptance",
        policy={
            CAT_ENTITIES: {
                ENTITY_ENTITY_IDS: {
                    _HA_DENIED: {POLICY_READ: True},
                    _GUEST_DENIED: {
                        POLICY_READ: True,
                        POLICY_CONTROL: True,
                    },
                    _FULLY_ALLOWED: {
                        POLICY_READ: True,
                        POLICY_CONTROL: True,
                    },
                }
            }
        },
    )
    return MockUser(
        id=_USER_ID,
        name="Multi-layer restricted user",
        is_owner=False,
        groups=[group],
    )


def _service_call(entity_id: str, call_id: str) -> bytes:
    arguments = {
        "list": [
            {
                "domain": "light",
                "service": "turn_off",
                "service_data": {"entity_id": [entity_id]},
            }
        ]
    }
    chunk = {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "execute_services",
                                "arguments": json.dumps(arguments, separators=(",", ":")),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _tool_names(request: dict[str, Any]) -> set[str]:
    return {
        item["function"]["name"]
        for item in request.get("tools", [])
        if item.get("type") == "function"
    }


def _tool_result(request: dict[str, Any], call_id: str) -> Any:
    message = next(
        item
        for item in request["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == call_id
    )
    return json.loads(message["content"])


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=_USER_ID),
        language="en",
        agent_id=agent.entry.entry_id,
    )


async def test_guest_tool_policy_and_ha_permissions_resolve_conflicts_stably(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Each authorization layer must remain restrictive without poisoning later turns."""
    user = _restricted_user()
    user.add_to_hass(hass)

    # Keep two ordinary configured tools, but allow only execute_services in Guest
    # Mode. This makes the configured-tool policy an independently observable layer.
    function_tools = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[:2])
    entry = _make_entry(
        "Multi-layer Authorization",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: function_tools,
            CONF_GUEST_MODE_ENABLED: True,
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_FUNCTION_POLICY: "custom",
            CONF_GUEST_ALLOWED_FUNCTION_NAMES: ["execute_services"],
            CONF_GUEST_SEPARATE_CONTROL_RESTRICTIONS: True,
            CONF_GUEST_CONTROL_EXCLUDED_ENTITIES: [_GUEST_DENIED],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    calls: list[Any] = []

    async def turn_off(call: Any) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    for entity_id in (_HA_DENIED, _GUEST_DENIED, _FULLY_ALLOWED):
        hass.states.async_set(entity_id, "on")
        async_expose_entity(hass, conversation.DOMAIN, entity_id, True)

    await agent._guest_mode.async_update_trusted(indefinite=True)
    assert agent._guest_mode.is_active()

    # Layer 1: Guest Mode permits the tool and this entity, but Home Assistant's
    # authenticated user policy denies control. The service must never run.
    ha_call_id = "call-multilayer-ha-denied"
    ha_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _service_call(_HA_DENIED, ha_call_id),
            _chat_sse_text("Home Assistant permissions denied that action."),
        ],
    )
    ha_denied = await _say(hass, agent, "Turn off the HA-denied light")
    assert _speech(ha_denied) == "Home Assistant permissions denied that action."
    assert calls == []
    assert len(ha_wire.requests) == 2
    assert "execute_services" in _tool_names(ha_wire.requests[0]["body"])
    assert "get_attributes" not in _tool_names(ha_wire.requests[0]["body"])
    ha_result = _tool_result(ha_wire.requests[1]["body"], ha_call_id)
    ha_result_text = json.dumps(ha_result)
    assert "does not have permission to control" in ha_result_text
    assert _HA_DENIED in ha_result_text
    assert "guest_mode" not in ha_result_text

    # Layer 2: HA permits this user's control, but Guest Mode explicitly excludes
    # the entity. The result must be a Guest Mode denial and still no service call.
    guest_call_id = "call-multilayer-guest-denied"
    guest_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _service_call(_GUEST_DENIED, guest_call_id),
            _chat_sse_text("Guest Mode denied that action."),
        ],
    )
    guest_denied = await _say(hass, agent, "Turn off the Guest-denied light")
    assert _speech(guest_denied) == "Guest Mode denied that action."
    assert calls == []
    assert len(guest_wire.requests) == 2
    assert "execute_services" in _tool_names(guest_wire.requests[0]["body"])
    assert "get_attributes" not in _tool_names(guest_wire.requests[0]["body"])
    guest_result = _tool_result(guest_wire.requests[1]["body"], guest_call_id)
    guest_result_text = json.dumps(guest_result)
    assert "guest_mode" in guest_result_text
    assert "does not have permission to control" not in guest_result_text

    # Layer 3: a later action allowed by all three layers must still succeed. This
    # proves either denial cannot mutate or poison the effective authorization state.
    allowed_call_id = "call-multilayer-allowed"
    allowed_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _service_call(_FULLY_ALLOWED, allowed_call_id),
            _chat_sse_text("The fully allowed light is off."),
        ],
    )
    allowed = await _say(hass, agent, "Turn off the fully allowed light")
    assert _speech(allowed) == "The fully allowed light is off."
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == [_FULLY_ALLOWED]
    assert calls[0].context.user_id == _USER_ID
    assert len(allowed_wire.requests) == 2
    assert "execute_services" in _tool_names(allowed_wire.requests[0]["body"])
    assert "get_attributes" not in _tool_names(allowed_wire.requests[0]["body"])
    allowed_result = _tool_result(allowed_wire.requests[1]["body"], allowed_call_id)
    assert allowed_result["result"][0]["success"] is True

    # The Guest schedule and tool-filter policy remain unchanged after all attempts.
    assert agent._guest_mode.is_active()
    assert agent.subentry.data[CONF_GUEST_FUNCTION_POLICY] == "custom"
    assert agent.subentry.data[CONF_GUEST_ALLOWED_FUNCTION_NAMES] == ["execute_services"]
