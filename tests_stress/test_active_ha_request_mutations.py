"""Nightly public Assist requests facing live Home Assistant mutation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOL_ERROR_RECOVERY,
    CONF_FUNCTION_TOOLS,
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
from homeassistant.core import Context, HomeAssistant, ServiceCall
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_native_service_disappearance import (
    _DOMAIN,
    _ENTITY_ID,
    _SERVICE,
    _TOOL_NAME,
    _arguments,
    _native_execute_service_tool,
    _tool_result_from_chat_request,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _raw_client, _speech
from tests_stress.conftest import record


@pytest.mark.parametrize(
    "mutation",
    ("unexposed", "removed", "unavailable", "service_removed", "permission_revoked"),
)
async def test_active_provider_request_rechecks_live_ha_before_action(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    stress_trace: list[dict],
) -> None:
    """The provider can propose an action after its initial HA view becomes stale."""
    service_calls: list[ServiceCall] = []

    async def service_handler(call: ServiceCall) -> None:
        service_calls.append(call)

    hass.services.async_register(_DOMAIN, _SERVICE, service_handler)
    hass.states.async_set(_ENTITY_ID, "on")
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)

    def permission_group(*, control: bool) -> Group:
        policy = {POLICY_READ: True}
        if control:
            policy[POLICY_CONTROL] = True
        return Group(
            id="active-request-mutation-group",
            name="Active request mutation",
            policy={CAT_ENTITIES: {ENTITY_ENTITY_IDS: {_ENTITY_ID: policy}}},
        )

    user = MockUser(
        id="active-request-mutation-user",
        name="Active request mutation",
        is_owner=False,
        groups=[permission_group(control=True)],
    )
    user.add_to_hass(hass)
    entry = _make_entry(
        f"Active request HA churn {mutation}",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOL_ERROR_RECOVERY: True,
            CONF_FUNCTION_TOOLS: [_native_execute_service_tool()],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    call_id = f"call-active-request-{mutation}"
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(call_id, _TOOL_NAME, _arguments()),
            _chat_sse_text("The target changed before the action."),
        ],
    )
    original_send = wire.send
    provider_reached = asyncio.Event()
    release_provider = asyncio.Event()

    async def gated_send(*args: Any, **kwargs: Any) -> Any:
        response = await original_send(*args, **kwargs)
        if len(wire.requests) == 1:
            provider_reached.set()
            await release_provider.wait()
        return response

    monkeypatch.setattr(_raw_client(agent)._client, "send", gated_send)
    task = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Act on the light",
            conversation_id=None,
            context=Context(user_id=user.id),
            language="en",
            agent_id=entry.entry_id,
        )
    )
    try:
        await asyncio.wait_for(provider_reached.wait(), timeout=10)
        if mutation == "unexposed":
            async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, False)
        elif mutation == "removed":
            hass.states.async_remove(_ENTITY_ID)
        elif mutation == "unavailable":
            hass.states.async_set(_ENTITY_ID, "unavailable")
        elif mutation == "permission_revoked":
            user.groups = [permission_group(control=False)]
            assert not user.permissions.check_entity(_ENTITY_ID, POLICY_CONTROL)
        else:
            hass.services.async_remove(_DOMAIN, _SERVICE)
        await hass.async_block_till_done()
    finally:
        release_provider.set()

    result = await task
    assert service_calls == []
    assert len(wire.requests) in {1, 2}
    if len(wire.requests) == 2:
        failure = _tool_result_from_chat_request(wire.requests[1]["body"], call_id)
        assert "error" in failure["result"][0]
        assert _speech(result) == "The target changed before the action."
    else:
        assert result.response.error_code is not None
    if mutation in {"unexposed", "removed"}:
        later_wire = _install_wire(
            monkeypatch, agent, [_chat_sse_text("The current HA view is available.")]
        )
        later = await conversation.async_converse(
            hass=hass,
            text="Describe the currently available targets",
            conversation_id=None,
            context=Context(user_id=user.id),
            language="en",
            agent_id=entry.entry_id,
        )
        assert _speech(later) == "The current HA view is available."
        assert len(later_wire.requests) == 1
        assert _ENTITY_ID not in json.dumps(later_wire.requests[0]["body"])
    record(
        stress_trace,
        "summary",
        layer="Real HA",
        active_request_ha_mutations=1,
        mutation=mutation,
        provider_requests=len(wire.requests),
    )
