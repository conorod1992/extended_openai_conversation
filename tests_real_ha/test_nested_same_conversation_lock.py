"""Real blocking Script Functions fail usefully instead of waiting on an ancestor."""

import asyncio
import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    CONF_FUNCTION_TOOLS,
)
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider
from tests_real_ha.test_request_rules_script_semantics import _local
from tests_real_ha.test_service_registry_acceptance import _response_service_call


@pytest.mark.parametrize("route", ["conversation.process", f"{DOMAIN}.process"])
@pytest.mark.parametrize("target", ["same", "new-id", "other-agent"])
async def test_blocking_nested_request_does_not_reenter_parent_conversation(
    hass, monkeypatch, route, target
):
    tool = {
        "spec": {
            "name": "delegate",
            "description": "Ask a finite child rule",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_agent": {"type": "string"},
                    "target_conversation": {"type": "string"},
                },
                "required": ["target_agent", "target_conversation"],
            },
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": route,
                    "data": {
                        "agent_id": "{{ target_agent }}",
                        "conversation_id": "{{ target_conversation }}",
                        "text": "finite child request",
                        "language": "en",
                    },
                    "response_variable": "child_answer",
                },
                {"variables": {"_function_result": "{{ child_answer }}"}},
            ],
        },
    }
    parent = await _agent(
        hass,
        title="Lock parent",
        **{CONF_FUNCTION_TOOLS: [tool], "conversation_continuity": "ha_default"},
    )
    child = (
        await _agent(hass, title="Lock child") if target == "other-agent" else parent
    )
    async_expose_entity(hass, "conversation", child.entity_id, True)
    effects = []

    async def mark(call):
        effects.append("child")

    hass.services.async_register("nested_probe", "mark", mark)
    await child._request_rules.async_create(
        _local(
            [{"action": "nested_probe.mark"}],
            phrase="finite child request",
            success="Child local answer",
        )
    )
    if child is not parent:
        _provider(monkeypatch, child, [])
    # Retain lock/scope observations if a regression reaches the bounded witness.
    observed_claims = []
    manager_type = type(parent._continuity)
    claim_method = manager_type._async_claim_ha_default_conversation

    async def observe_claim(manager, conversation_id):
        owners = claim_method.__globals__["_PROCESSING_CONVERSATIONS"].get()
        observed_claims.append(
            {
                "parent_manager": manager is parent._continuity,
                "conversation_id": conversation_id,
                "ancestors": [
                    (
                        owner.manager is manager,
                        owner.conversation_id,
                        owner.claimed_conversation_id,
                        owner.active,
                    )
                    for owner in owners
                ],
                "users": dict(manager._ha_default_lock_users),
            }
        )
        return await claim_method(manager, conversation_id)

    monkeypatch.setattr(
        manager_type, "_async_claim_ha_default_conversation", observe_claim
    )
    parent_id = "nested-parent-conversation"
    child_id = "nested-isolated-child" if target == "new-id" else parent_id
    sent = _provider(
        monkeypatch,
        parent,
        [
            {
                "index": 0,
                "id": "nested-call",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": json.dumps(
                        {
                            "target_agent": child.entity_id,
                            "target_conversation": child_id,
                        }
                    ),
                },
            },
            "Parent completed",
        ],
    )
    user = MockUser(id="lock-owner", name="Lock owner", is_owner=True).add_to_hass(hass)
    outer = asyncio.create_task(
        _response_service_call(
            hass,
            "process",
            {
                "agent_id": parent.entity_id,
                "text": "Delegate once",
                "conversation_id": parent_id,
            },
            user_id=user.id,
        )
    )
    try:
        result = await asyncio.wait_for(asyncio.shield(outer), 5)
    except TimeoutError:
        pending = []
        for task in asyncio.all_tasks():
            chain = []
            coroutine = task.get_coro()
            while coroutine is not None and len(chain) < 35:
                frame = getattr(coroutine, "cr_frame", None)
                if frame is not None:
                    chain.append((frame.f_code.co_name, frame.f_lineno))
                coroutine = getattr(coroutine, "cr_await", None)
            if any(
                name
                in {
                    "_async_claim_ha_default_conversation",
                    "async_resolve",
                    "_async_process",
                    "async_call",
                }
                for name, _ in chain
            ):
                pending.append(chain)
        outer.cancel()
        await asyncio.gather(outer, return_exceptions=True)
        pytest.fail(
            f"Nested request remained pending: claims={observed_claims}; tasks={pending}"
        )
    if target == "same":
        assert effects == []
        assert "Nested processing cannot reuse" in json.dumps(sent) + str(result)
        # The rejected child must leave no stale lock/claim after parent cleanup.
        independent = await _response_service_call(
            hass,
            "process",
            {
                "agent_id": parent.entity_id,
                "text": "finite child request",
                "conversation_id": parent_id,
            },
            user_id=user.id,
        )
        assert independent["successful"] is True, independent
        assert effects == ["child"]
    else:
        assert result["successful"] is True, result
        assert result["response"] == "Parent completed"
        assert effects == ["child"]
        assert len(sent) == 2
    assert not parent._continuity._ha_default_locks
    assert not parent._continuity._ha_default_claims
