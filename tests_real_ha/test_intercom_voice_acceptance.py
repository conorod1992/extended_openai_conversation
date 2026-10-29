"""Real-HA acceptance coverage for local voice broadcast delivery."""

from __future__ import annotations

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.const import (
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    async_get_intercom,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    replay_safety,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_local_intent_exclusions import _make_entry, _setup_entry
from tests_real_ha.test_service_registry_acceptance import (
    _conversation_entity_id,
    _response_service_call,
)


@pytest.mark.asyncio
async def test_real_ha_voice_broadcast_waits_for_idle_target_and_skips_origin(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local voice broadcast must queue while busy, then announce only to peers."""
    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    # This journey validates delivery, not TTL expiry. Avoid leaving the production
    # 120-second expiry callback behind for the Real-HA lingering-timer assertion.
    monkeypatch.setattr(intercom, "async_call_later", lambda *_args, **_kwargs: None)

    entry = _make_entry()
    await _setup_entry(hass, entry)
    entity_id = _conversation_entity_id(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    provider_path = AsyncMock(
        side_effect=AssertionError(
            "Targeted local broadcast unexpectedly fell through to the provider"
        )
    )
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)

    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)

    hass.states.async_set(
        "assist_satellite.kitchen",
        "idle",
        {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
    )
    hass.states.async_set(
        "assist_satellite.hall",
        "responding",
        {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
    )

    announce_calls: list[dict] = []

    async def announce(call) -> None:
        announce_calls.append(dict(call.data))

    hass.services.async_register("assist_satellite", "announce", announce)

    response = await _response_service_call(
        hass,
        SERVICE_PROCESS,
        {
            "text": "broadcast to everyone dinner is ready",
            "agent_id": entity_id,
            "satellite_id": "assist_satellite.kitchen",
            "language": "en",
        },
    )
    await hass.async_block_till_done()

    assert response["response"] == "Broadcast queued."
    assert response["handled_locally"] is True
    provider_path.assert_not_awaited()
    assert announce_calls == []

    queued = manager.history()
    assert len(queued) == 1
    assert queued[0]["message"] == "dinner is ready"
    assert queued[0]["source"] == "local_voice"
    assert queued[0]["origin_entity_id"] == "assist_satellite.kitchen"
    assert queued[0]["targets"] == ["assist_satellite.hall"]
    assert queued[0]["deliveries"]["assist_satellite.hall"]["status"] == "queued_busy"

    hass.states.async_set(
        "assist_satellite.hall",
        "idle",
        {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
    )
    await hass.async_block_till_done()

    assert announce_calls == [
        {
            "message": "dinner is ready",
            "entity_id": "assist_satellite.hall",
        }
    ]
    delivered = manager.history()[0]
    assert delivered["deliveries"]["assist_satellite.hall"]["status"] == "delivered"


@pytest.mark.parametrize("route", ["local", "native"])
async def test_public_broadcast_preserves_originating_context(hass, monkeypatch, route):
    """Both voice routes retain the original context through queued service delivery."""
    from pytest_homeassistant_custom_component.common import MockUser

    from tests_real_ha.test_cross_feature_acceptance import _agent
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
    )
    from tests_stress.test_native_broadcast_authorization import _send_broadcast_tool

    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    user = MockUser(is_owner=True)
    user.add_to_hass(hass)
    agent = await _agent(
        hass, functions=[_send_broadcast_tool()], local_intents_enabled=route == "local"
    )
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    for name in ("kitchen", "hall"):
        hass.states.async_set(
            f"assist_satellite.{name}",
            "idle",
            {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
        )
    contexts = []

    async def announce(call):
        contexts.append(call.context)

    hass.services.async_register("assist_satellite", "announce", announce)
    if route == "native":
        _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call(
                    "broadcast",
                    "send_broadcast",
                    {"whole_home": True, "message": "Dinner is ready"},
                ),
                _chat_sse_text("Done"),
            ],
        )
    context = Context(user_id=user.id, parent_id="originating-broadcast-request")
    try:
        result = await _response_service_call(
            hass,
            SERVICE_PROCESS,
            {
                "text": "broadcast to everyone dinner is ready"
                if route == "local"
                else "Announce dinner",
                "agent_id": agent.entity_id,
                "satellite_id": "assist_satellite.kitchen",
                "language": "en",
            },
            context=context,
        )
        await hass.async_block_till_done()
        assert result["successful"]
        assert contexts == [context]
        assert contexts[0] is context
        assert manager.history()[0]["targets"] == ["assist_satellite.hall"]
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize(
    "selector", ["entity_ids", "device_ids", "area_ids", "floor_ids", "label_ids"]
)
async def test_conflicting_whole_home_selectors_never_queue(hass, selector):
    """Reject contradictory destinations at the common resolver before mutation."""
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    try:
        with pytest.raises(HomeAssistantError, match="either whole_home"):
            await manager.async_send(
                "Private message",
                whole_home=True,
                **{selector: ["specific-destination"]},
            )
        assert not manager.history()
        assert not manager.pending()
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("route", ["native", "service", "websocket"])
async def test_public_broadcast_rejects_conflicting_destinations(hass, route):
    from custom_components.extended_openai_conversation_responses.const import DOMAIN
    from custom_components.extended_openai_conversation_responses.functions.native import (
        NativeFunction,
    )
    from custom_components.extended_openai_conversation_responses.intercom_panel import (
        websocket_broadcast,
    )
    from custom_components.extended_openai_conversation_responses.intercom_services import (
        async_setup_intercom_services,
    )

    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    await async_setup_intercom_services(hass)
    hass.states.async_set(
        "assist_satellite.kitchen", "idle", {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE}
    )
    try:
        if route == "websocket":
            connection = SimpleNamespace(
                context=lambda _: Context(), send_result=Mock(), send_error=Mock()
            )
            await unwrap(websocket_broadcast)(
                hass,
                connection,
                {
                    "id": 1,
                    "action": "send",
                    "message": "Private message",
                    "whole_home": True,
                    "entity_ids": ["assist_satellite.kitchen"],
                },
            )
            connection.send_result.assert_not_called()
            assert "either whole_home" in connection.send_error.call_args.args[2]
        else:
            with pytest.raises(HomeAssistantError, match="either whole_home"):
                if route == "native":
                    await NativeFunction().send_broadcast(
                        hass,
                        {},
                        {
                            "whole_home": True,
                            "destination": "Kitchen",
                            "message": "Private message",
                        },
                        None,
                        [],
                    )
                else:
                    await hass.services.async_call(
                        DOMAIN,
                        "broadcast",
                        {
                            "whole_home": True,
                            "entity_id": "assist_satellite.kitchen",
                            "message": "Private message",
                        },
                        blocking=True,
                    )
        assert not manager.history()
        assert not manager.pending()
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("completion", ["expiry", "delivery", "disable", "shutdown"])
async def test_pending_broadcast_survives_history_eviction_and_expiry(
    hass, monkeypatch, completion
):
    """Recent completions cannot hide an older active delivery from snapshots."""
    from custom_components.extended_openai_conversation_responses.intercom_panel import (
        websocket_broadcast,
    )

    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    for name, state in (("kitchen", "responding"), ("hall", "idle")):
        hass.states.async_set(
            f"assist_satellite.{name}",
            state,
            {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
        )
    hass.services.async_register("assist_satellite", "announce", AsyncMock())
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True), send_result=Mock(), send_error=Mock()
    )
    try:
        old = await manager.async_send(
            "Still pending", entity_ids=["assist_satellite.kitchen"], context=Context()
        )
        for i in range(intercom.HISTORY_LIMIT):
            await manager.async_send(
                f"Finished {i}", entity_ids=["assist_satellite.hall"], context=Context()
            )
            await hass.async_block_till_done()
        assert len(manager.history()) == intercom.HISTORY_LIMIT
        assert old["id"] not in {item["id"] for item in manager.history()}
        await unwrap(websocket_broadcast)(
            hass, connection, {"id": 1, "action": "snapshot"}
        )
        snapshot = connection.send_result.call_args.args[1]
        assert [item["id"] for item in snapshot["pending"]] == [old["id"]]
        if completion == "expiry":
            manager._expire(old["id"])
        elif completion == "delivery":
            hass.states.async_set(
                "assist_satellite.kitchen",
                "idle",
                {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
            )
            await hass.async_block_till_done()
        elif completion == "disable":
            await manager.async_set_enabled(False)
        else:
            await manager.async_shutdown()
        assert manager.pending() == []
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize(
    "first,second",
    [
        ("native", "service"),
        ("service", "native"),
        ("native", "script"),
        ("script", "native"),
    ],
)
async def test_broadcast_replay_identity_is_shared_across_routes(
    hass, monkeypatch, first, second
):
    """A failed real provider continuation cannot repeat an announcement via another tool."""
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
    )
    from tests_stress.test_native_broadcast_authorization import _send_broadcast_tool

    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    target = "assist_satellite.hall"
    tools = [_send_broadcast_tool()]
    for name in ("service", "script"):
        function = (
            {"type": "native", "name": "execute_service_single"}
            if name == "service"
            else {
                "type": "script",
                "sequence": [
                    {
                        "action": "extended_openai_conversation_responses.broadcast",
                        "data": {"message": "{{ message }}", "entity_id": target},
                    }
                ],
            }
        )
        tools.append(
            {
                "spec": {
                    "name": name,
                    "description": "Send announcement",
                    "parameters": {"type": "object", "additionalProperties": True},
                },
                "function": function,
            }
        )
    agent = await _agent(
        hass,
        functions=tools,
        local_intents_enabled=False,
        function_tool_error_recovery=True,
    )
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    hass.states.async_set(target, "idle", {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE})
    async_expose_entity(hass, "conversation", target, True)
    announcements = []

    async def announce(call):
        announcements.append(call.data["message"])

    hass.services.async_register("assist_satellite", "announce", announce)

    def invoke(route, call_id, message="Dinner is ready"):
        args = (
            {"message": message, "whole_home": True}
            if route == "native"
            else (
                {
                    "domain": "extended_openai_conversation_responses",
                    "service": "broadcast",
                    "service_data": {"message": message, "entity_id": target},
                }
                if route == "service"
                else {"message": message}
            )
        )
        return _chat_sse_tool_call(
            call_id, "send_broadcast" if route == "native" else route, args
        )

    try:
        _install_wire(
            monkeypatch,
            agent,
            [
                invoke(first, "first"),
                (
                    400,
                    {
                        "error": {
                            "message": "Failed continuation",
                            "type": "invalid_request_error",
                        }
                    },
                ),
            ],
        )
        result = await _say(hass, agent, "Make an announcement")
        await hass.async_block_till_done()
        assert result.response.error_code is not None
        assert announcements == ["Dinner is ready"]
        _install_wire(
            monkeypatch,
            agent,
            [invoke(second, "retry"), _chat_sse_text("Not repeated")],
        )
        retry = await _say(
            hass, agent, "Recover previous request", result.conversation_id
        )
        await hass.async_block_till_done()
        assert retry.response.error_code is not None
        assert announcements == ["Dinner is ready"]
        assert replay_safety(agent).effects
        # A different message remains executable before acknowledgement.
        _install_wire(
            monkeypatch,
            agent,
            [invoke("native", "different", "Good morning"), _chat_sse_text("Done")],
        )
        changed = await _say(
            hass, agent, "Send a different announcement", retry.conversation_id
        )
        await hass.async_block_till_done()
        assert changed.response.error_code is None
        assert announcements == ["Dinner is ready", "Good morning"]
        # Successful acknowledgement allows a deliberate later repetition.
        _install_wire(
            monkeypatch, agent, [invoke(second, "acknowledged"), _chat_sse_text("Done")]
        )
        final = await _say(hass, agent, "Announce it again", changed.conversation_id)
        await hass.async_block_till_done()
        assert final.response.error_code is None
        assert announcements == ["Dinner is ready", "Good morning", "Dinner is ready"]
    finally:
        await manager.async_shutdown()
