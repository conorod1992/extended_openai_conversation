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
