"""Real Home Assistant acceptance coverage for authenticated user permissions."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.auth.models import Group
from homeassistant.auth.permissions.const import CAT_ENTITIES, POLICY_CONTROL, POLICY_READ
from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    WS_COMMAND,
)
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _install_wire,
    _speech,
    _tool_result_from_chat_request,
)

_ALLOWED_ENTITY = "light.permission_allowed"
_DENIED_ENTITY = "light.permission_denied"
_USER_ID = "real-ha-permission-user"


def _chat_sse_tool_call(entity_id: str, call_id: str) -> bytes:
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


def _restricted_user() -> MockUser:
    group = Group(
        id="real-ha-permission-group",
        name="Real HA permission acceptance",
        policy={
            CAT_ENTITIES: {
                ENTITY_ENTITY_IDS: {
                    _ALLOWED_ENTITY: {
                        POLICY_READ: True,
                        POLICY_CONTROL: True,
                    },
                    _DENIED_ENTITY: {POLICY_READ: True},
                }
            }
        },
    )
    return MockUser(
        id=_USER_ID,
        name="Restricted acceptance user",
        is_owner=False,
        groups=[group],
    )


async def _restricted_ws_client(
    hass: HomeAssistant, hass_ws_client: Any, user: MockUser
) -> Any:
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    access_token = hass.auth.async_create_access_token(refresh_token)
    return await hass_ws_client(hass, access_token)


async def _say_as_user(
    hass: HomeAssistant,
    agent: Any,
    user: MockUser,
    text: str,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=user.id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


async def test_restricted_user_cannot_bypass_ha_entity_or_management_permissions(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: Any,
) -> None:
    """A real HA user stays inside both entity-control and admin boundaries."""
    user = _restricted_user()
    user.add_to_hass(hass)

    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    entry = hass.config_entries.async_get_entry(agent.entry.entry_id)
    assert entry is not None
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == "conversation"
    )

    calls: list[Any] = []

    async def turn_off(call: Any) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set(_ALLOWED_ENTITY, "on", {"friendly_name": "Allowed light"})
    hass.states.async_set(_DENIED_ENTITY, "on", {"friendly_name": "Denied light"})
    async_expose_entity(hass, conversation.DOMAIN, _ALLOWED_ENTITY, True)
    async_expose_entity(hass, conversation.DOMAIN, _DENIED_ENTITY, True)

    denied_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(_DENIED_ENTITY, "call-permission-denied"),
            _chat_sse_text("The denied light was not changed."),
        ],
    )
    denied = await _say_as_user(
        hass, agent, user, "Turn off the denied permission test light"
    )

    assert _speech(denied) == "The denied light was not changed."
    assert calls == []
    denied_tool_result = _tool_result_from_chat_request(denied_wire.requests[1]["body"])
    assert "error" in denied_tool_result["result"][0]
    assert "does not have permission to control" in denied_tool_result["result"][0][
        "error"
    ]
    assert _DENIED_ENTITY in denied_tool_result["result"][0]["error"]

    allowed_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(_ALLOWED_ENTITY, "call-permission-allowed"),
            _chat_sse_text("The allowed light is off."),
        ],
    )
    allowed = await _say_as_user(
        hass, agent, user, "Turn off the allowed permission test light"
    )

    assert _speech(allowed) == "The allowed light is off."
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == [_ALLOWED_ENTITY]
    assert calls[0].context.user_id == user.id
    allowed_tool_result = _tool_result_from_chat_request(
        allowed_wire.requests[1]["body"]
    )
    assert allowed_tool_result["result"][0]["success"] is True

    client = await _restricted_ws_client(hass, hass_ws_client, user)
    await client.send_json_auto_id(
        {
            "type": WS_COMMAND,
            "section": "configuration",
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert "Administrator permission is required" in str(response)
