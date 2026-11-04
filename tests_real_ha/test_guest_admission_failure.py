"""Native HA live admissions must not pollute another continuity scope on failure."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as runtime,
)
from custom_components.extended_openai_conversation_responses.ha_chat_session_compat import (
    async_get_chat_session,
)
from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import (
    DATA_CHAT_LOGS,
    async_get_chat_log,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import (
    _install_wire,
    _chat_sse_text,
    _responses_sse_text,
)


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("guest_active", [False, True])
@pytest.mark.parametrize("failure", [HomeAssistantError, asyncio.CancelledError])
@pytest.mark.parametrize("stage", ["before_continuity", "after_continuity"])
async def test_guest_failure_never_retains_admitted_input_in_owner_history(
    hass, monkeypatch, failure, stage, guest_active, api
):
    user = await hass.auth.async_create_user(
        "Admission owner", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    agent = await _agent(hass, conversation_continuity="user", api_mode=api)
    text_reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [text_reply("Owner response"), text_reply("Owner resumed")],
    )

    async def say(text, conversation_id):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=context,
            language="en",
            agent_id=agent.entity_id,
        )

    owner = await say("Original owner input", None)
    assert owner.response.error_code is None
    owner_id = owner.conversation_id
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    admitted = conversation.ConversationInput(
        text="GUEST_ADMISSION_SENTINEL",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id=owner_id,
        agent_id=agent.entity_id,
    )
    with monkeypatch.context() as patch:
        if stage == "before_continuity":
            patch.setattr(
                runtime,
                "async_prepare_runtime_function_tools",
                AsyncMock(side_effect=failure("admission interrupted")),
            )
        else:
            patch.setattr(
                agent,
                "_async_begin_archive_session",
                AsyncMock(side_effect=failure("admission interrupted")),
            )
        with pytest.raises(failure):
            with async_get_chat_session(
                hass, owner_id, context=context
            ) as source_session:
                with async_get_chat_log(hass, source_session, admitted):
                    await agent.async_process(admitted)
    assert len(wire.requests) == 1
    source = hass.data[DATA_CHAT_LOGS][owner_id]
    owned_failure = not guest_active and stage == "after_continuity"
    assert [item.content for item in source.content if item.role == "user"] == (
        ["Original owner input", "GUEST_ADMISSION_SENTINEL"]
        if owned_failure
        else ["Original owner input"]
    )
    await agent._guest_mode.async_disable_trusted()
    resumed = await say("Owner follow-up", owner_id)
    assert resumed.response.error_code is None
    assert (
        "GUEST_ADMISSION_SENTINEL" in json.dumps(wire.requests[-1]["body"])
    ) == owned_failure


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("guest_active", [False, True])
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("failure", [HomeAssistantError, asyncio.CancelledError])
@pytest.mark.parametrize("stage", ["before_continuity", "after_continuity"])
async def test_failed_delegation_preserves_foreign_parent_input(
    hass, monkeypatch, guest_active, api, failure, stage
):
    agent = await _agent(hass, conversation_continuity="user", api_mode=api)
    user = await hass.auth.async_create_user(
        "Delegating owner", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    parent_input = conversation.ConversationInput(
        text="Delegate this same question",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id="foreign-parent",
        agent_id="conversation.other_integration",
    )
    if stage == "before_continuity":
        monkeypatch.setattr(
            runtime,
            "async_prepare_runtime_function_tools",
            AsyncMock(side_effect=failure("child unavailable")),
        )
    else:
        monkeypatch.setattr(
            agent,
            "_async_begin_archive_session",
            AsyncMock(side_effect=failure("child unavailable")),
        )
    with async_get_chat_session(hass, "foreign-parent", context=context) as session:
        with async_get_chat_log(hass, session, parent_input) as parent:

            async def delegate():
                return await conversation.async_converse(
                    hass=hass,
                    text=parent_input.text,
                    conversation_id=session.conversation_id,
                    context=context,
                    language="en",
                    agent_id=agent.entity_id,
                )

            if failure is asyncio.CancelledError:
                with pytest.raises(asyncio.CancelledError):
                    await delegate()
            else:
                result = await delegate()
                assert result.response.error_code is not None
            assert [item.content for item in parent.content if item.role == "user"] == [
                parent_input.text
            ]
            parent.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=parent_input.agent_id,
                    content="Parent handled the failed delegation",
                )
            )
    stored = hass.data[DATA_CHAT_LOGS][session.conversation_id]
    assert [item.content for item in stored.content if item.role == "user"] == [
        parent_input.text
    ]


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_first_unclaimed_guest_log_is_never_later_owner_context(
    hass, monkeypatch, api, outcome
):
    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user("First owner", group_ids=["system-admin"])
    context = Context(user_id=user.id)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [reply("Guest response"), reply("Owner response")]
        if outcome == "success"
        else [reply("Owner response")],
    )
    await agent._guest_mode.async_update_trusted(indefinite=True)
    admitted = conversation.ConversationInput(
        text="UNCLAIMED_GUEST_SENTINEL",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id="first-unclaimed",
        agent_id=agent.entity_id,
    )
    with monkeypatch.context() as patch:
        if outcome != "success":
            failure = (
                asyncio.CancelledError if outcome == "cancel" else HomeAssistantError
            )
            patch.setattr(
                runtime,
                "async_prepare_runtime_function_tools",
                AsyncMock(side_effect=failure("not ready")),
            )
        with async_get_chat_session(
            hass, "first-unclaimed", context=context
        ) as session:
            with async_get_chat_log(hass, session, admitted) as source:
                if outcome == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await agent.async_process(admitted)
                else:
                    result = await conversation.async_converse(
                        hass=hass,
                        text=admitted.text,
                        conversation_id=session.conversation_id,
                        context=context,
                        language="en",
                        agent_id=agent.entity_id,
                    )
                    assert (result.response.error_code is None) == (
                        outcome == "success"
                    )
                assert [
                    item.content for item in source.content if item.role == "user"
                ] == ["UNCLAIMED_GUEST_SENTINEL"]
    await agent._guest_mode.async_disable_trusted()
    resumed = await conversation.async_converse(
        hass=hass,
        text="Owner follow-up",
        conversation_id="first-unclaimed",
        context=context,
        language="en",
        agent_id=agent.entity_id,
    )
    assert resumed.response.error_code is None
    assert resumed.conversation_id != "first-unclaimed"
    assert "UNCLAIMED_GUEST_SENTINEL" not in json.dumps(wire.requests[-1]["body"])


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("fail_owned", [False, True])
async def test_initial_ha_default_log_can_be_claimed_and_retained(
    hass, monkeypatch, api, fail_owned
):
    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user(
        "Initial owner", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [reply("First response"), reply("Follow-up")]
        if not fail_owned
        else [reply("Follow-up")],
    )
    admitted = conversation.ConversationInput(
        text="INITIAL_OWNER_SENTINEL",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id="initial-owner",
        agent_id=agent.entity_id,
    )
    with monkeypatch.context() as patch:
        if fail_owned:
            patch.setattr(
                agent,
                "_async_begin_archive_session",
                AsyncMock(side_effect=HomeAssistantError("owned failure")),
            )
        with async_get_chat_session(hass, "initial-owner", context=context) as session:
            with async_get_chat_log(hass, session, admitted) as source:
                result = await conversation.async_converse(
                    hass=hass,
                    text=admitted.text,
                    conversation_id=session.conversation_id,
                    context=context,
                    language="en",
                    agent_id=agent.entity_id,
                )
                assert (result.response.error_code is None) == (not fail_owned)
                assert result.conversation_id == "initial-owner"
                assert [
                    item.content for item in source.content if item.role == "user"
                ] == ["INITIAL_OWNER_SENTINEL"]
    resumed = await conversation.async_converse(
        hass=hass,
        text="Owner follow-up",
        conversation_id="initial-owner",
        context=context,
        language="en",
        agent_id=agent.entity_id,
    )
    assert resumed.response.error_code is None
    assert resumed.conversation_id == "initial-owner"
    assert "INITIAL_OWNER_SENTINEL" in json.dumps(wire.requests[-1]["body"])
