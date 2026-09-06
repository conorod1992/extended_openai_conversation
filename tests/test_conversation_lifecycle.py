"""Focused tests for the start-fresh conversation lifecycle."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.conversation_lifecycle import (
    async_reset_conversation_context,
    begin_conversation_lifecycle,
    end_conversation_lifecycle,
    request_fresh_conversation,
    requested_conversation_reset,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    reset_function_group_runtime,
)
from custom_components.extended_openai_conversation_responses.request import (
    assemble_integration_function_tools,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    get_request_rule_runtime,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError


def test_reset_request_is_scoped_to_current_execution_context() -> None:
    token = begin_conversation_lifecycle()
    try:
        request = request_fresh_conversation(
            "continuity:user:alice", "continuity:user:alice"
        )
        assert requested_conversation_reset() == request
    finally:
        end_conversation_lifecycle(token)

    assert requested_conversation_reset() is None
    with pytest.raises(RuntimeError, match="No active conversation"):
        request_fresh_conversation(None, None)


async def test_reset_clears_only_ephemeral_managed_session_state() -> None:
    hass = cast(Any, SimpleNamespace(data={}))
    continuity = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    first = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "kitchen", None, 30
    )
    assert first.key is not None
    await continuity.async_record_success(
        first.key,
        first.claim_token,
        [conversation.SystemContent(content="system")],
    )

    state_session_id = f"continuity:{first.key}"
    await continuity.async_set_memory_bundle(
        state_session_id, [("alice", "memory-1")], 30
    )
    function_groups = reset_function_group_runtime(hass, "entry", "agent")
    function_groups.begin(state_session_id, 30).loaded_group_ids.add("lights")
    request_rules = get_request_rule_runtime(hass, "entry", "agent")
    request_rules.set(state_session_id, {"model": "test-model"}, 30)

    await async_reset_conversation_context(
        hass,
        continuity,
        "entry",
        "agent",
        continuity_key=first.key,
        state_session_id=state_session_id,
        memory_session_id=state_session_id,
    )

    assert function_groups.stats()["active_function_group_sessions"] == 0
    assert request_rules.get(state_session_id, 30) == {}
    assert await continuity.async_get_memory_bundle(state_session_id, 30) is None
    fresh = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "kitchen", None, 30
    )
    assert fresh.conversation_id != first.conversation_id
    assert fresh.history == []
    assert fresh.resumed is False


async def test_user_reset_does_not_erase_newer_in_flight_claim() -> None:
    continuity = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    first = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "kitchen", None, 30
    )
    assert first.key is not None
    await continuity.async_record_success(
        first.key,
        first.claim_token,
        [conversation.SystemContent(content="old")],
    )

    newer = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "kitchen", None, 30
    )
    assert newer.key == first.key
    assert await continuity.async_request_end(first.key) is True

    overlap = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "kitchen", None, 30
    )
    assert overlap.key is None
    assert overlap.conversation_id != first.conversation_id

    await continuity.async_record_success(
        newer.key,
        newer.claim_token,
        [conversation.SystemContent(content="newer")],
    )
    fresh = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "kitchen", None, 30
    )
    assert fresh.key == first.key
    assert fresh.conversation_id != first.conversation_id
    assert fresh.history == []
    assert fresh.resumed is False


async def test_ha_default_reset_ignores_returned_conversation_id_once() -> None:
    hass = cast(Any, SimpleNamespace(data={}))
    continuity = ConversationContinuity("agent")
    await async_reset_conversation_context(
        hass,
        continuity,
        "entry",
        "agent",
        continuity_key=None,
        state_session_id="conversation:incoming",
        memory_session_id="conversation:incoming",
    )

    fresh = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        user_scope("alice", source="test"),
        "kitchen",
        "incoming",
        30,
    )
    assert fresh.conversation_id is None

    later = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        user_scope("alice", source="test"),
        "kitchen",
        "incoming",
        30,
    )
    assert later.conversation_id == "incoming"


def test_lifecycle_tool_is_builtin_and_reserved() -> None:
    tools = assemble_integration_function_tools(
        {},
        set(),
        memory_scope_available=False,
        temporary_scope_available=False,
        knowledge_available=False,
        archive_available=False,
    )
    lifecycle = next(
        tool
        for tool in tools
        if tool.get("spec", {}).get("name") == "start_fresh_conversation"
    )
    assert lifecycle["function"] == {
        "type": "conversation_lifecycle",
        "operation": "start_fresh",
    }
    assert lifecycle["spec"]["parameters"]["additionalProperties"] is False

    with pytest.raises(HomeAssistantError, match="Reserved conversation lifecycle"):
        assemble_integration_function_tools(
            {},
            {"start_fresh_conversation"},
            memory_scope_available=False,
            temporary_scope_available=False,
            knowledge_available=False,
            archive_available=False,
        )
